from flask import request
from flask_socketio import emit
import os
import ssl
import subprocess
import time
import websocket as ws_client
from src.services.pty_service import pty_service
from src.services.docker import docker_service
from src.services.acp import acp_manager
from src.services.rag import search as rag_search
from src.services.automation import (
    automation_manager, create_rule, update_rule, delete_rule, list_rules,
    get_history as get_automation_history, load_meta_policy, save_meta_policy,
    record_history, _execute_rule,
)
import json
import threading
import base64
import secrets
import logging
import uuid

logger = logging.getLogger("fernando.websocket")

# Store CSRF tokens per session
csrf_tokens = {}
# Track all terminal sids per socket sid for cleanup
socket_terminals = {}
# Track open Jupyter session names (for sidebar)
_open_jupyter = set()
# Track jupyter_cmd origin: {cmd_id: originating_socket_sid}
_jupyter_cmd_origins = {}
_jupyter_cmd_origins_lock = threading.Lock()

# Module-level reference to acp_subscribers (set by register_handlers)
_acp_subscribers_ref = {}


def get_acp_subscribers(session_id):
    """Return set of socket sids subscribed to a given ACP session."""
    return set(_acp_subscribers_ref.get(session_id, set()))


def register_handlers(socketio):
    @socketio.on("connect")
    def handle_connect():
        # Validate API key
        api_key = request.args.get("api_key")
        try:
            with open("/tmp/fernando-api-key", "r") as f:
                valid_key = f.read().strip()
        except:
            return False

        if api_key != valid_key:
            return False

        # Generate CSRF token for this session
        csrf_token = secrets.token_urlsafe(32)
        csrf_tokens[request.sid] = csrf_token
        socket_terminals[request.sid] = set()
        logger.info(f"Client connected: sid={request.sid}")
        emit("connected", {"data": "Connected", "csrf_token": csrf_token})

    def validate_csrf(data):
        """Validate CSRF token for the current session"""
        token = data.get("csrf_token")
        return token and csrf_tokens.get(request.sid) == token

    @socketio.on("get_sessions")
    def get_sessions(data={}):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        sessions = pty_service.list_sessions()
        chat_sessions = acp_manager.list_sessions()
        from src.services.notebooks import list_notebooks
        from src.services import groups
        running_notebooks = [nb["name"] for nb in list_notebooks() if nb["running"]]
        group_data = groups.get_all()
        emit("sessions_list", {
            "sessions": sessions,
            "chat_sessions": chat_sessions,
            "running_notebooks": running_notebooks,
            "running_jupyter": list(_open_jupyter),
            "groups": group_data["groups"],
            "session_groups": group_data["session_groups"],
        })

    @socketio.on("kasm_ws")
    def handle_kasm_ws(data):
        """Proxy WebSocket messages to Kasm"""
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return

        path = data.get("path", "")
        client_sid = request.sid

        # Read VNC password
        try:
            with open("/tmp/fernando-vnc-password", "r") as f:
                vnc_password = f.read().strip()
        except:
            emit("error", {"message": "VNC password not found"})
            return

        # Create WebSocket connection to Kasm
        auth_str = base64.b64encode(f"kasm_user:{vnc_password}".encode()).decode("ascii")
        ws_url = f"wss://localhost:6901/{path}"

        ws = ws_client.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
        ws.connect(ws_url, header=[f"Authorization: Basic {auth_str}"])

        def forward_from_kasm():
            while True:
                try:
                    msg = ws.recv()
                    if msg:
                        socketio.emit("kasm_data", {"data": msg}, room=client_sid)
                except:
                    break

        threading.Thread(target=forward_from_kasm, daemon=True).start()

        @socketio.on("kasm_send")
        def send_to_kasm(msg):
            try:
                ws.send(msg["data"])
            except:
                pass

    @socketio.on("desktop_key")
    def handle_desktop_key(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        key = data.get("key", "")
        if not key or not all(c.isalnum() or c in "+-_" for c in key):
            return
        subprocess.Popen(
            ["docker", "exec", "-e", "DISPLAY=:1", "--user", "1000:1000", "fernando-desktop",
             "xdotool", "key", "--", key],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @socketio.on("attach_session")
    def attach_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return

        session_name = data["session"]
        terminal = data.get("terminal", 1)
        vid = f"{request.sid}_{terminal}"
        client_sid = request.sid

        logger.info(f"Attaching session {session_name} to terminal {terminal}, vid={vid}")

        if client_sid in socket_terminals:
            socket_terminals[client_sid].add(vid)

        # Detach any previous viewer for this terminal
        pty_service.detach_viewer(vid)

        def on_output(raw_bytes):
            decoded = raw_bytes.decode("utf-8", errors="ignore")
            socketio.emit("output", {"terminal": terminal, "data": decoded}, room=client_sid)

        try:
            scrollback = pty_service.attach_viewer(vid, session_name, on_output)
        except ValueError as e:
            emit("error", {"message": str(e)})
            return

        # Replay scrollback so the user sees previous output
        if scrollback and not data.get("skip_replay"):
            decoded = scrollback.decode("utf-8", errors="ignore")
            emit("output", {"terminal": terminal, "data": decoded})

        # Send a resize to ensure the PTY matches the browser's terminal size
        # (the session may have started at default 80x24 before the browser attached)

    @socketio.on("create_session")
    def create_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_type = data.get("type", "shell")
        group_id = data.get("group_id")
        name = pty_service.create_session(session_type)
        # Assign to group if specified
        if group_id:
            from src.services import groups
            groups.move_session_to_group(name, group_id)
        emit("session_created", {"name": name, "switch": True})

    @socketio.on("detach_viewer")
    def handle_detach_viewer(data):
        if not validate_csrf(data):
            return
        terminal = data.get("terminal", 1)
        vid = f"{request.sid}_{terminal}"
        pty_service.detach_viewer(vid)

    @socketio.on("input")
    def handle_input(data):
        if not validate_csrf(data):
            return
        terminal = data.get("terminal", 1)
        vid = f"{request.sid}_{terminal}"
        pty_service.write_input(vid, data["data"])

    @socketio.on("resize")
    def handle_resize(data):
        if not validate_csrf(data):
            return
        terminal = data.get("terminal", 1)
        vid = f"{request.sid}_{terminal}"
        pty_service.resize(vid, data["rows"], data["cols"])

    @socketio.on("rename_session")
    def rename_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        try:
            old_name = data["old_name"]
            new_name = data["new_name"]
            pty_service.rename_session(old_name, new_name)
            emit("session_renamed", {"old_name": old_name, "new_name": new_name}, broadcast=True)
        except Exception as e:
            emit("error", {"message": str(e)})

    @socketio.on("close_session")
    def close_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_name = data["session"]
        pty_service.kill_session(session_name)
        emit("session_closed", {"session": session_name}, broadcast=True)

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = request.sid
        logger.info(f"Client disconnected: sid={sid}")
        csrf_tokens.pop(sid, None)
        # Detach all viewer connections for this socket
        terminal_vids = socket_terminals.pop(sid, set())
        for vid in terminal_vids:
            logger.info(f"Detaching viewer on disconnect: {vid}")
            pty_service.detach_viewer(vid)
        # Also detach the default _1 and _2 in case they weren't tracked
        pty_service.detach_viewer(f"{sid}_1")
        pty_service.detach_viewer(f"{sid}_2")
        # Clean up active pane tracking for this socket
        if sid in _socket_active_panes:
            del _socket_active_panes[sid]
            _rebuild_active_pane_sessions()

    @socketio.on("restart_desktop")
    def restart_desktop(data={}):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        try:
            docker_service.restart_kasm()
            emit(
                "desktop_restarted",
                {"message": "Desktop container restarted successfully"},
            )
        except Exception as e:
            emit("desktop_restart_error", {"error": str(e)})

    # --- Notebook handlers ---

    @socketio.on("list_notebooks")
    def handle_list_notebooks(data={}):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services.notebooks import list_notebooks
        emit("notebooks_list", {"notebooks": list_notebooks()})

    @socketio.on("create_notebook")
    def handle_create_notebook(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        name = data.get("name", "").strip().lower()
        from src.services.notebooks import create_notebook
        nb, err = create_notebook(name)
        if err:
            emit("notebook_error", {"error": err})
        else:
            emit("notebook_created", {"notebook": nb})

    @socketio.on("delete_notebook")
    def handle_delete_notebook(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        name = data.get("name", "")
        from src.services.notebooks import delete_notebook
        err = delete_notebook(name)
        if err:
            emit("notebook_error", {"error": err})
        else:
            emit("notebook_deleted", {"name": name})

    @socketio.on("start_notebook")
    def handle_start_notebook(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        name = data.get("name", "")
        group_id = data.get("group_id")
        logger.info(f"start_notebook requested: name={name}")
        client_sid = request.sid
        # Assign to group if specified
        if group_id:
            from src.services import groups
            groups.move_session_to_group('notebook:' + name, group_id)
        from src.services.notebooks import start_notebook
        def _start():
            logger.info(f"start_notebook background task running for '{name}'")
            info, err = start_notebook(name)
            if err:
                logger.error(f"start_notebook failed: {err}")
                socketio.emit("notebook_error", {"error": err}, room=client_sid)
            else:
                logger.info(f"start_notebook succeeded: {name} on port {info['port']}")
                socketio.emit("notebook_started", {"name": name, "port": info["port"]}, room=client_sid)
        socketio.start_background_task(_start)

    @socketio.on("stop_notebook")
    def handle_stop_notebook(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        name = data.get("name", "")
        from src.services.notebooks import stop_notebook
        stop_notebook(name)
        emit("notebook_stopped", {"name": name})

    @socketio.on("jupyter_cmd")
    def handle_jupyter_cmd(data):
        """Forward a command to the Jupyter iframe via broadcast."""
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        cmd_id = data.get("id", "")
        if cmd_id:
            with _jupyter_cmd_origins_lock:
                _jupyter_cmd_origins[cmd_id] = request.sid
        # Broadcast to all clients — the frontend will forward to the matching Jupyter iframe
        socketio.emit("jupyter_cmd", {
            "action": data.get("action"),
            "source": data.get("source", ""),
            "cell_type": data.get("cell_type", "code"),
            "id": cmd_id,
            "index": data.get("index", 0),
            "position": data.get("position", "bottom"),
            "notebook": data.get("notebook", ""),
        })

    @socketio.on("jupyter_cmd_ack")
    def handle_jupyter_cmd_ack(data):
        """Receive an ack from the frontend and relay result to the originating MCP client."""
        if not validate_csrf(data):
            return
        cmd_id = data.get("id", "")
        receivers = int(data.get("receivers", 0) or 0)
        if not cmd_id:
            return
        # Only relay acks that report at least one receiver — ignore zero-receiver acks
        # from clients that don't have the target notebook open
        if receivers == 0:
            return
        with _jupyter_cmd_origins_lock:
            origin_sid = _jupyter_cmd_origins.pop(cmd_id, None)
        if origin_sid:
            socketio.emit("jupyter_cmd_result", {
                "id": cmd_id,
                "receivers": receivers,
            }, room=origin_sid)

    @socketio.on("open_jupyter")
    def handle_open_jupyter(data):
        if not validate_csrf(data):
            return
        name = data.get("name", "Jupyter")
        group_id = data.get("group_id")
        _open_jupyter.add(name)
        # Assign to group if specified
        if group_id:
            from src.services import groups
            groups.move_session_to_group('jupyter:' + name, group_id)

    @socketio.on("close_jupyter")
    def handle_close_jupyter(data):
        if not validate_csrf(data):
            return
        name = data.get("name", "Jupyter")
        preserve_group = data.get("preserve_group", False)
        _open_jupyter.discard(name)
        # Clean up group assignment for this session (unless preserving for rename)
        if not preserve_group:
            from src.services import groups
            groups.move_session_to_group('jupyter:' + name, None)

    # --- Project Group handlers ---

    @socketio.on("group_create")
    def handle_group_create(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services import groups
        name = data.get("name", "New Group").strip()
        color = data.get("color", "#7ea8e3")
        group = groups.create_group(name, color)
        emit("group_created", {"group": group}, broadcast=True)

    @socketio.on("group_rename")
    def handle_group_rename(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services import groups
        group_id = data.get("group_id")
        new_name = data.get("name", "").strip()
        if group_id and new_name:
            group = groups.rename_group(group_id, new_name)
            if group:
                emit("group_updated", {"group": group}, broadcast=True)

    @socketio.on("group_set_color")
    def handle_group_set_color(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services import groups
        group_id = data.get("group_id")
        color = data.get("color")
        if group_id and color:
            group = groups.set_group_color(group_id, color)
            if group:
                emit("group_updated", {"group": group}, broadcast=True)

    @socketio.on("group_delete")
    def handle_group_delete(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services import groups
        group_id = data.get("group_id")
        if group_id:
            groups.delete_group(group_id)
            emit("group_deleted", {"group_id": group_id}, broadcast=True)

    @socketio.on("group_move_session")
    def handle_group_move_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services import groups
        session_key = data.get("session_key")
        group_id = data.get("group_id")
        if session_key:
            groups.move_session_to_group(session_key, group_id)
            emit("session_group_changed", {"session_key": session_key, "group_id": group_id}, broadcast=True)

    # --- Workflow handlers ---

    @socketio.on("list_subagents")
    def list_subagents(data={}):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.list_subagents()
        emit("subagents_list", {"subagents": result})

    @socketio.on("get_subagent_status")
    def get_subagent_status(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.get_subagent_status(data["task_id"])
        emit("subagent_status", result)

    @socketio.on("terminate_subagent")
    def terminate_subagent(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.terminate_subagent(data["task_id"])
        emit("subagent_terminated", result)

    @socketio.on("delete_subagent")
    def delete_subagent(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.delete_subagent(data["task_id"])
        emit("subagent_deleted", result)

    @socketio.on("get_at_jobs")
    def get_at_jobs(data={}):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.get_at_jobs()
        emit("at_jobs", {"jobs": result})

    @socketio.on("get_cron_jobs")
    def get_cron_jobs(data={}):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.get_cron_jobs()
        emit("cron_jobs", {"jobs": result})

    @socketio.on("remove_at_job")
    def remove_at_job(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.remove_at_job(data["job_id"])
        emit("at_job_removed", result)

    @socketio.on("remove_cron_job")
    def remove_cron_job(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = automation_manager.remove_cron_job(data["task_id"])
        emit("cron_job_removed", result)

    @socketio.on("automation_create_rule")
    def automation_create_rule(data):
        if not validate_csrf(data):
            return
        rule_data = data.get("rule")
        if not rule_data:
            emit("automation_error", {"error": "No rule provided"})
            return
        rule, err = create_rule(rule_data)
        if err:
            emit("automation_error", {"error": err})
        else:
            emit("automation_rule_created", {"rule": rule})

    @socketio.on("automation_list_rules")
    def automation_list_rules(data={}):
        if not validate_csrf(data):
            return
        emit("automation_rules", {"rules": list_rules()})

    @socketio.on("automation_update_rule")
    def automation_update_rule(data):
        if not validate_csrf(data):
            return
        rule_id = data.get("rule_id")
        updates = data.get("updates", {})
        if not rule_id:
            return
        rule, err = update_rule(rule_id, updates)
        if err:
            emit("automation_error", {"error": err})
        else:
            emit("automation_rule_updated", {"rule": rule})

    @socketio.on("automation_delete_rule")
    def automation_delete_rule(data):
        if not validate_csrf(data):
            return
        rule_id = data.get("rule_id")
        if rule_id:
            delete_rule(rule_id)
            emit("automation_rule_deleted", {"rule_id": rule_id})

    @socketio.on("automation_toggle_rule")
    def automation_toggle_rule(data):
        if not validate_csrf(data):
            return
        rule_id = data.get("rule_id")
        enabled = data.get("enabled", True)
        if rule_id:
            rule, err = update_rule(rule_id, {"enabled": enabled})
            if not err:
                emit("automation_rule_updated", {"rule": rule})

    @socketio.on("automation_get_history")
    def automation_get_history(data={}):
        if not validate_csrf(data):
            return
        limit = min(data.get("limit", 50), 200)
        emit("automation_history", {"history": get_automation_history(limit)})

    @socketio.on("automation_get_meta_policy")
    def automation_get_meta_policy(data={}):
        if not validate_csrf(data):
            return
        emit("automation_meta_policy", {"policy": load_meta_policy()})

    @socketio.on("automation_update_meta_policy")
    def automation_update_meta_policy(data):
        if not validate_csrf(data):
            return
        policy = data.get("policy")
        if policy:
            save_meta_policy(policy)
            emit("automation_meta_policy_saved", {"policy": policy})

    # --- ACP Chat handlers ---

    acp_subscribers = _acp_subscribers_ref  # use module-level dict so web.py can access it

    acp_event_seq = {}  # session_id -> sequence counter

    def acp_on_event(session_id, event):
        """Broadcast ACP events to subscribed websocket clients."""
        seq = acp_event_seq.get(session_id, 0)
        acp_event_seq[session_id] = seq + 1
        sids = acp_subscribers.get(session_id, set())
        # Log first event and periodically to help debug delivery issues
        evt_type = event.get("type") or ((event.get("params") or {}).get("update") or {}).get("sessionUpdate", "")
        if seq == 0 or evt_type in ("session_ready", "session_ended", "session_error"):
            logger.info(f"acp_on_event: session={session_id} seq={seq} type={evt_type} subscribers={len(sids)}")
        for sid in sids:
            socketio.emit("acp_event", {"session_id": session_id, "seq": seq, "event": event}, room=sid)

    def broadcast_sessions_list():
        """Broadcast updated sessions list to all connected clients."""
        logger.info("[broadcast] Broadcasting sessions_list update")
        sessions = pty_service.list_sessions()
        chat_sessions = acp_manager.list_sessions()
        from src.services.notebooks import list_notebooks
        from src.services import groups
        running_notebooks = [nb["name"] for nb in list_notebooks() if nb["running"]]
        group_data = groups.get_all()
        socketio.emit("sessions_list", {
            "sessions": sessions,
            "chat_sessions": chat_sessions,
            "running_notebooks": running_notebooks,
            "running_jupyter": list(_open_jupyter),
            "groups": group_data["groups"],
            "session_groups": group_data["session_groups"],
        })

    def broadcast_status_change(session_id, status):
        """Broadcast session status change to all connected clients."""
        socketio.emit("acp_status_change", {"session_id": session_id, "status": status})

    # Restore persisted chat sessions on startup
    acp_manager.default_on_event = acp_on_event
    acp_manager.set_on_sessions_change(broadcast_sessions_list)
    acp_manager.set_on_status_change(broadcast_status_change)
    acp_manager.restore_sessions(lambda sid: acp_on_event)

    @socketio.on("acp_create")
    def acp_create(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        model = data.get("model")
        backend = data.get("backend", "kiro")
        group_id = data.get("group_id")
        session_id = acp_manager.create_session(on_event=acp_on_event, model=model, backend=backend)
        # Assign to group if specified
        if group_id:
            from src.services import groups
            groups.move_session_to_group('chat:' + session_id, group_id)
        emit("acp_created", {"session_id": session_id})

    @socketio.on("acp_clone")
    def acp_clone(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        source_id = data.get("session_id")
        if not source_id:
            emit("error", {"message": "Missing session_id"})
            return
        new_id = acp_manager.clone_session(source_id, on_event=acp_on_event)
        if not new_id:
            emit("error", {"message": "Session not found"})
            return
        group_id = data.get("group_id")
        if group_id:
            from src.services import groups
            groups.move_session_to_group('chat:' + new_id, group_id)
        emit("acp_created", {"session_id": new_id})

    @socketio.on("acp_execute_command")
    def acp_execute_command(data):
        """Execute a slash command in an ACP session (for testing tangent/rewind)."""
        logger.info(f"[acp_execute_command] received: {data}")
        if not validate_csrf(data):
            logger.warning("[acp_execute_command] CSRF validation failed")
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_id = data.get("session_id")
        command = data.get("command")
        if not session_id or not command:
            logger.warning(f"[acp_execute_command] missing params: session_id={session_id}, command={command}")
            emit("error", {"message": "Missing session_id or command"})
            return
        session = acp_manager.get_session(session_id)
        if not session:
            logger.warning(f"[acp_execute_command] session not found: {session_id}")
            emit("error", {"message": "Session not found"})
            return
        if not session.is_loaded:
            logger.warning(f"[acp_execute_command] session not loaded: {session_id}")
            emit("error", {"message": "Session not loaded"})
            return
        logger.info(f"[acp_execute_command] executing '{command}' on session {session_id}")
        result = session.execute_command(command)
        logger.info(f"[acp_execute_command] result: {result}")
        emit("acp_command_result", {"session_id": session_id, "command": command, "result": result})

    @socketio.on("acp_subscribe")
    def acp_subscribe(data):
        if not validate_csrf(data):
            logger.warning(f"acp_subscribe: CSRF validation failed for sid={request.sid}")
            return
        acp_sid = data.get("session_id")
        if acp_sid:
            acp_subscribers.setdefault(acp_sid, set()).add(request.sid)
            # Replay history for reconnecting clients
            session = acp_manager.get_session(acp_sid)
            logger.info(f"acp_subscribe: session_id={acp_sid} found={session is not None} ready={session.ready if session else 'N/A'} history_len={len(session.history) if session else 0}")
            if session:
                # Mark as read when user views the chat
                session.mark_read()
                # Update activity timestamp if session is already loaded (opening counts as activity)
                if session.is_loaded:
                    session._last_activity = time.time()
                offset = data.get("history_offset", 0)
                history = session.history[offset:]
                # Tell client how many events to expect so it can show progress
                emit("acp_history_size", {"session_id": acp_sid, "count": len(history)})
                # Collapse consecutive agent_message_chunk text events into single events
                collapsed = []
                text_buf = ""
                text_buf_ts = None
                text_buf_model = None
                for evt in history:
                    su = ((evt.get("params") or {}).get("update") or {}).get("sessionUpdate", "")
                    content = ((evt.get("params") or {}).get("update") or {}).get("content") or {}
                    if su == "agent_message_chunk" and content.get("type") == "text":
                        if not text_buf_ts and evt.get("ts"):
                            text_buf_ts = evt["ts"]
                        if not text_buf_model and evt.get("model"):
                            text_buf_model = evt["model"]
                        text_buf += content["text"]
                    else:
                        if text_buf:
                            entry = {"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}}
                            if text_buf_ts:
                                entry["ts"] = text_buf_ts
                            if text_buf_model:
                                entry["model"] = text_buf_model
                            collapsed.append(entry)
                            text_buf = ""
                            text_buf_ts = None
                            text_buf_model = None
                        collapsed.append(evt)
                if text_buf:
                    entry = {"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}}
                    if text_buf_ts:
                        entry["ts"] = text_buf_ts
                    if text_buf_model:
                        entry["model"] = text_buf_model
                    collapsed.append(entry)
                # Send history as a single batch to avoid "replay" effect
                next_seq = acp_event_seq.get(acp_sid, 0)
                logger.info(f"acp_subscribe: sending history batch ({len(collapsed)} events) sync_seq={next_seq} history_len={len(session.history)} ready={session.ready}")
                # Append step_progress catch-up events for reconnects
                step_progress_events = []
                if offset > 0:
                    seen_pids = set()
                    for evt in reversed(session.history):
                        if evt.get("type") == "step_progress":
                            pid = (evt.get("data") or {}).get("pipeline_id")
                            if pid and pid not in seen_pids:
                                seen_pids.add(pid)
                                step_progress_events.append(evt)
                emit("acp_history_batch", {
                    "session_id": acp_sid,
                    "events": collapsed,
                    "step_progress": step_progress_events,
                    "sync_seq": next_seq,
                    "history_length": len(session.history),
                    "model": session.model,
                    "backend": session.backend,
                })
                if session.ready:
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_ready"}})
                elif not session.is_loaded and session.acp_session_id:
                    # Session was unloaded due to idle timeout — reload it
                    logger.info(f"acp_subscribe: session {acp_sid} unloaded, triggering reload")
                    acp_manager.reload_session(acp_sid)
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_loading"}})
                elif session.proc is None or session.proc.poll() is not None:
                    if len(session.history) > 0:
                        # Process died after loading — it's actually crashed
                        logger.info(f"acp_subscribe: session {acp_sid} proc dead (post-load), sending session_ready")
                        emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_ready"}})
                    else:
                        # Process hasn't started yet — session is still loading, don't send ready
                        logger.info(f"acp_subscribe: session {acp_sid} still loading (proc not yet spawned)")
                        emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_loading"}})
                elif not session._is_prompting and (time.time() - session._last_activity) > 60:
                    # Process alive but idle for >60s without being in a prompt — stalled
                    logger.info(f"acp_subscribe: session {acp_sid} stalled (idle {time.time() - session._last_activity:.0f}s), sending session_ready")
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_ready"}})
                else:
                    # Session exists but not ready yet — still loading
                    logger.info(f"acp_subscribe: session {acp_sid} not ready. ready={session.ready} proc={session.proc is not None} poll={session.proc.poll() if session.proc else 'N/A'} prompting={session._is_prompting} idle={time.time() - session._last_activity:.0f}s")
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_loading"}})
            else:
                # Archived session — replay from history file as read-only preview
                from src.services.acp import load_history_file
                history = load_history_file(acp_sid)
                if history:
                    emit("acp_history_size", {"session_id": acp_sid, "count": len(history)})
                    collapsed = []
                    text_buf = ""
                    text_buf_ts = None
                    text_buf_model = None
                    for evt in history:
                        su = ((evt.get("params") or {}).get("update") or {}).get("sessionUpdate", "")
                        content = ((evt.get("params") or {}).get("update") or {}).get("content") or {}
                        if su == "agent_message_chunk" and content.get("type") == "text":
                            if not text_buf_ts and evt.get("ts"):
                                text_buf_ts = evt["ts"]
                            if not text_buf_model and evt.get("model"):
                                text_buf_model = evt["model"]
                            text_buf += content["text"]
                        else:
                            if text_buf:
                                entry = {"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}}
                                if text_buf_ts:
                                    entry["ts"] = text_buf_ts
                                if text_buf_model:
                                    entry["model"] = text_buf_model
                                collapsed.append(entry)
                                text_buf = ""
                                text_buf_ts = None
                                text_buf_model = None
                            collapsed.append(evt)
                    if text_buf:
                        entry = {"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}}
                        if text_buf_ts:
                            entry["ts"] = text_buf_ts
                        if text_buf_model:
                            entry["model"] = text_buf_model
                        collapsed.append(entry)
                    # Get group info for archived session
                    from src.services import groups
                    session_groups = groups.get_session_groups()
                    all_groups = groups.list_groups()
                    group_id = session_groups.get(f"chat:{acp_sid}")
                    group_name = None
                    if group_id:
                        for g in all_groups:
                            if g["id"] == group_id:
                                group_name = g["name"]
                                break
                    emit("acp_history_batch", {
                        "session_id": acp_sid,
                        "events": collapsed,
                        "step_progress": [],
                        "sync_seq": 0,
                        "history_length": len(history),
                        "model": None,
                        "archived": True,
                        "group_name": group_name,
                    })

    @socketio.on("acp_prompt")
    def acp_prompt(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            text = data.get("text", "")
            session.send_prompt(text)

    @socketio.on("acp_cancel")
    def acp_cancel(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            session.cancel()

    @socketio.on("acp_mark_read")
    def acp_mark_read(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            session.mark_read()

    # Track active pane sessions per socket connection (for multi-tab support)
    _socket_active_panes = {}  # socket_sid -> set of chat session IDs

    def _rebuild_active_pane_sessions():
        """Rebuild the union of all active pane sessions across all connections."""
        all_active = set()
        for sessions in _socket_active_panes.values():
            all_active.update(sessions)
        acp_manager.set_active_pane_sessions(all_active)

    @socketio.on("acp_set_active_panes")
    def acp_set_active_panes(data):
        """Update which ACP sessions are currently open in UI panes for this connection."""
        if not validate_csrf(data):
            return
        session_ids = set(data.get("session_ids", []))
        socket_sid = request.sid
        logger.info(f"[active-panes] socket={socket_sid[:8]} sessions={session_ids}")
        
        # Get this socket's previous sessions
        previous = _socket_active_panes.get(socket_sid, set())
        
        # Update activity for sessions being switched away from (in this tab)
        for sid in previous - session_ids:
            session = acp_manager.get_session(sid)
            if session and session.is_loaded:
                session._last_activity = time.time()
        
        # Update activity for sessions being switched to
        for sid in session_ids:
            session = acp_manager.get_session(sid)
            if session and session.is_loaded:
                session._last_activity = time.time()
        
        # Store this socket's active sessions and rebuild the union
        _socket_active_panes[socket_sid] = session_ids
        _rebuild_active_pane_sessions()

    @socketio.on("acp_stall_info")
    def acp_stall_info(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            emit("acp_stall_info", {"session_id": data["session_id"], **session.get_stall_info()})

    @socketio.on("acp_check_pending_auth")
    def acp_check_pending_auth(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        if sid:
            from src.routes.web import _pending_auth_requests
            # Find all pending auth requests for this session and emit them
            for key, pending in list(_pending_auth_requests.items()):
                if key.startswith(f"{sid}:"):
                    emit("authorization_request", pending)

    @socketio.on("acp_force_unstick")
    def acp_force_unstick(data):
        """Kill the stuck kiro-cli process and reload the session."""
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        session = acp_manager.get_session(sid)
        if not session or not session.acp_session_id:
            return
        acp_id = session.acp_session_id
        logger.warning(f"acp_force_unstick: killing session {sid} acp={acp_id}")
        session.stop()
        # Reload in background
        session.on_event = acp_on_event
        threading.Thread(
            target=acp_manager._load_existing,
            args=(sid, session, acp_id),
            daemon=True,
        ).start()

    @socketio.on("acp_change_model")
    def acp_change_model(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        model = data.get("model")
        if not sid or not model:
            return
        ok = acp_manager.change_model(sid, model)
        emit("acp_model_changed", {"session_id": sid, "model": model, "ok": ok})

    @socketio.on("acp_restart")
    def acp_restart(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        if not sid:
            return
        session = acp_manager.get_session(sid)
        if not session or not session.acp_session_id:
            emit("acp_restarted", {"session_id": sid, "ok": False})
            return
        acp_id = session.acp_session_id
        session.stop()
        def _reload():
            try:
                session._recording = True
                session._broadcasting = True
                session._alive = True
                session.load(acp_id)
                session.ready = True
                acp_manager._save_pid_map()
                if session.on_event:
                    session.on_event(sid, {"type": "session_ready"})
            except Exception as e:
                logger.error(f"acp_restart failed for {sid}: {e}")
                if session.on_event:
                    session.on_event(sid, {"type": "session_error", "error": str(e)})
        import threading
        threading.Thread(target=_reload, daemon=True).start()
        emit("acp_restarted", {"session_id": sid, "ok": True})

    @socketio.on("acp_system_message")
    def acp_system_message(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        text = data.get("text", "")
        if not sid or not text:
            return
        session = acp_manager.get_session(sid)
        if session:
            evt = {"type": "system_message", "text": text, "ts": __import__('time').time()}
            session.history.append(evt)
            session._save_history()

    @socketio.on("acp_get_model")
    def acp_get_model(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        model = session.model if session else None
        emit("acp_current_model", {"session_id": data.get("session_id"), "model": model})

    @socketio.on("acp_close")
    def acp_close(data):
        if not validate_csrf(data):
            return
        acp_sid = data.get("session_id")
        if acp_sid:
            acp_subscribers.pop(acp_sid, None)
            acp_manager.archive_session(acp_sid)

    @socketio.on("acp_sleep")
    def acp_sleep(data):
        if not validate_csrf(data):
            return
        acp_sid = data.get("session_id")
        if acp_sid:
            session = acp_manager.get_session(acp_sid)
            if session:
                session.unload()
                emit("acp_session_slept", {"session_id": acp_sid}, broadcast=True)

    @socketio.on("acp_sleep_group")
    def acp_sleep_group(data):
        if not validate_csrf(data):
            return
        group_id = data.get("group_id")
        if not group_id:
            return
        from src.services import groups
        session_groups = groups.get_session_groups()
        slept = []
        for key, gid in session_groups.items():
            if gid == group_id and key.startswith("chat:"):
                sid = key[5:]
                session = acp_manager.get_session(sid)
                if session and session.is_loaded:
                    session.unload()
                    slept.append(sid)
        for sid in slept:
            emit("acp_session_slept", {"session_id": sid}, broadcast=True)

    @socketio.on("acp_wake_group")
    def acp_wake_group(data):
        if not validate_csrf(data):
            return
        group_id = data.get("group_id")
        if not group_id:
            return
        from src.services import groups
        session_groups = groups.get_session_groups()
        for key, gid in session_groups.items():
            if gid == group_id and key.startswith("chat:"):
                sid = key[5:]
                session = acp_manager.get_session(sid)
                if session and not session.is_loaded and session.acp_session_id:
                    acp_manager.reload_session(sid)

    @socketio.on("acp_list_archived")
    def acp_list_archived(data):
        if not validate_csrf(data):
            return
        emit("acp_archived_list", {"sessions": acp_manager.list_archived()})

    @socketio.on("acp_search_archived")
    def acp_search_archived(data):
        if not validate_csrf(data):
            return
        query = (data.get("query") or "").strip()
        if not query:
            emit("acp_archived_search_results", {"sessions": []})
            return
        # Get all archived sessions for title matching
        archived = acp_manager.list_archived()
        archived_ids = {s["id"] for s in archived}
        # Title matches (substring, case-insensitive)
        q_lower = query.lower()
        title_matches = {s["id"] for s in archived if q_lower in s["name"].lower()}
        # RAG matches (semantic search across all conversations, then filter to archived only)
        rag_hits = rag_search(query, limit=20)
        rag_matches = {h["session_id"] for h in rag_hits if h["session_id"] in archived_ids}
        # Merge: title matches first, then RAG-only matches
        all_match_ids = title_matches | rag_matches
        results = [s for s in archived if s["id"] in all_match_ids]
        emit("acp_archived_search_results", {"sessions": results})

    @socketio.on("acp_restore")
    def acp_restore(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        if sid:
            ok = acp_manager.restore_session(sid, on_event=acp_on_event)
            if ok:
                # Check if session's group still exists, remove from group if not
                from src.services import groups
                session_groups = groups.get_session_groups()
                all_groups = groups.list_groups()
                group_ids = {g["id"] for g in all_groups}
                session_key = f"chat:{sid}"
                current_group = session_groups.get(session_key)
                if current_group and current_group not in group_ids:
                    groups.move_session_to_group(session_key, None)
            emit("acp_restored", {"session_id": sid, "ok": ok})

    @socketio.on("acp_delete_archived")
    def acp_delete_archived(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        if sid:
            acp_manager.delete_archived(sid)

    @socketio.on("acp_rename")
    def acp_rename(data):
        if not validate_csrf(data):
            return
        name = (data.get("name") or "").strip()
        if not name:
            return
        acp_manager.rename_session(data.get("session_id"), name)

    # --- Workflow handlers ---

    def _automation_dispatch(action, rule, message):
        """Called by the email poller when an inbound message matches a rule."""
        logger.info(f"Automation dispatch: action={action} rule={rule.get('id') if rule else 'default'} from={message.get('from')} subject={message.get('subject','')[:60]}")
        if action in ("dispatch", "summary"):
            result = _execute_rule(rule, inbound_message=message)
            record_history(rule, message, action, result)

    automation_manager.start(on_dispatch=_automation_dispatch)

    # --- Reward/Debit handlers ---

    @socketio.on("reward_add")
    def reward_add(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        from src.services import rewards
        amount = data.get("amount", 0)
        note = data.get("note", "")
        session_id = data.get("session_id")
        entry, new_balance = rewards.add_entry(amount, note=note, session_id=session_id)
        # Record in session history so it persists across reloads
        if session_id:
            session = acp_manager.get_session(session_id)
            if session:
                import time
                reward_event = {
                    "type": "reward",
                    "amount": amount,
                    "note": note,
                    "balance": new_balance,
                    "ts": time.time(),
                }
                session.history.append(reward_event)
                session._save_history()
        emit("reward_added", {"entry": entry, "balance": new_balance}, broadcast=True)

    @socketio.on("reward_get_balance")
    def reward_get_balance(data={}):
        if not validate_csrf(data):
            return
        from src.services import rewards
        from src.services.acp import count_unread_child_messages
        session_id = data.get("session_id")
        unread = count_unread_child_messages(session_id) if session_id else 0
        emit("reward_balance", {"balance": rewards.get_balance(), "unread_child_messages": unread})

    @socketio.on("reward_get_ledger")
    def reward_get_ledger(data={}):
        if not validate_csrf(data):
            return
        from src.services import rewards
        limit = min(data.get("limit", 50), 200)
        emit("reward_ledger", {"ledger": rewards.get_ledger(limit)})

    # --- Git dirty indicator ---
    _last_dirty = [None]
    _git_check_started = [False]

    def _git_dirty_check():
        import time
        repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        while True:
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=repo_dir, capture_output=True, text=True, timeout=5
                )
                dirty = bool(result.stdout.strip())
                if dirty != _last_dirty[0]:
                    _last_dirty[0] = dirty
                    socketio.emit("git_dirty", {"dirty": dirty})
            except Exception:
                pass
            time.sleep(15)

    @socketio.on("request_git_status")
    def handle_git_status_request(data=None):
        if not _git_check_started[0]:
            _git_check_started[0] = True
            socketio.start_background_task(_git_dirty_check)
        # Send current state immediately to requesting client
        if _last_dirty[0] is not None:
            emit("git_dirty", {"dirty": _last_dirty[0]})
