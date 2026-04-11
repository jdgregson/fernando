from flask import request
from flask_socketio import emit
import os
import select
import ssl
import subprocess
import websocket as ws_client
from src.services.tmux import tmux_service
from src.services.docker import docker_service
from src.services.acp import acp_manager
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
        sessions = tmux_service.list_sessions()
        chat_sessions = acp_manager.list_sessions()
        emit("sessions_list", {"sessions": sessions, "chat_sessions": chat_sessions})

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
        sid = f"{request.sid}_{terminal}"
        client_sid = request.sid  # Capture this before background task

        logger.info(f"Attaching session {session_name} to terminal {terminal}, sid={sid}")

        # Track this terminal sid for cleanup on disconnect
        if client_sid in socket_terminals:
            socket_terminals[client_sid].add(sid)

        # Clean up any existing session for this terminal (handles reconnect case)
        tmux_service.cleanup_session(sid)

        master = tmux_service.attach_session(session_name, sid)

        def read_output():
            logger.info(f"Starting read_output loop for {sid}")
            while tmux_service.has_session(sid):
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        output = os.read(master, 65536)
                        if output:
                            decoded = output.decode("utf-8", errors="ignore")
                            socketio.emit(
                                "output",
                                {"terminal": terminal, "data": decoded},
                                room=client_sid,
                            )
                    except Exception as e:
                        logger.info(f"Read loop ending for {sid}: {e}")
                        break
            logger.info(f"Exiting read_output loop for {sid}")

        socketio.start_background_task(read_output)

    @socketio.on("create_session")
    def create_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_type = data.get("type", "shell")
        name = tmux_service.create_session_with_type(session_type)
        emit("session_created", {"name": name, "switch": True})

    @socketio.on("input")
    def handle_input(data):
        if not validate_csrf(data):
            return
        terminal = data.get("terminal", 1)
        sid = f"{request.sid}_{terminal}"
        tmux_service.write_input(sid, data["data"])

    @socketio.on("resize")
    def handle_resize(data):
        if not validate_csrf(data):
            return
        terminal = data.get("terminal", 1)
        sid = f"{request.sid}_{terminal}"
        tmux_service.resize_terminal(sid, data["rows"], data["cols"])

    @socketio.on("rename_session")
    def rename_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        try:
            old_name = data["old_name"]
            new_name = data["new_name"]
            tmux_service.rename_session(old_name, new_name)
            emit("session_renamed", {"old_name": old_name, "new_name": new_name}, broadcast=True)
        except Exception as e:
            emit("error", {"message": str(e)})

    @socketio.on("close_session")
    def close_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_name = data["session"]
        tmux_service.kill_session(session_name)
        emit("session_closed", {"session": session_name}, broadcast=True)

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = request.sid
        logger.info(f"Client disconnected: sid={sid}")
        csrf_tokens.pop(sid, None)
        # Clean up all terminal sessions tracked for this socket
        terminal_sids = socket_terminals.pop(sid, set())
        for tsid in terminal_sids:
            logger.info(f"Cleaning up terminal session on disconnect: {tsid}")
            tmux_service.cleanup_session(tsid)
        # Also clean up the default _1 and _2 in case they weren't tracked
        tmux_service.cleanup_session(f"{sid}_1")
        tmux_service.cleanup_session(f"{sid}_2")

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

    # --- Automation handlers (unified subagents + workflows) ---

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

    acp_subscribers = {}  # fernando_session_id -> set of socket sids

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

    # Restore persisted chat sessions on startup
    acp_manager.restore_sessions(lambda sid: acp_on_event)

    @socketio.on("acp_create")
    def acp_create(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_id = acp_manager.create_session(on_event=acp_on_event)
        emit("acp_created", {"session_id": session_id})

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
                offset = data.get("history_offset", 0)
                history = session.history[offset:]
                # Collapse consecutive agent_message_chunk text events into single events
                collapsed = []
                text_buf = ""
                for evt in history:
                    su = ((evt.get("params") or {}).get("update") or {}).get("sessionUpdate", "")
                    content = ((evt.get("params") or {}).get("update") or {}).get("content") or {}
                    if su == "agent_message_chunk" and content.get("type") == "text":
                        text_buf += content["text"]
                    else:
                        if text_buf:
                            collapsed.append({"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}})
                            text_buf = ""
                        collapsed.append(evt)
                if text_buf:
                    collapsed.append({"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}})
                for evt in collapsed:
                    emit("acp_event", {"session_id": acp_sid, "event": evt})
                # Tell client the actual history length and next live sequence number
                next_seq = acp_event_seq.get(acp_sid, 0)
                logger.info(f"acp_subscribe: sending sync_seq={next_seq} history_len={len(session.history)} ready={session.ready}")
                emit("acp_event", {"session_id": acp_sid, "event": {"type": "sync_seq", "seq": next_seq, "history_length": len(session.history)}})
                if session.ready:
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_ready"}})
            else:
                # Archived session — replay from history file as read-only preview
                from src.services.acp import load_history_file
                history = load_history_file(acp_sid)
                if history:
                    collapsed = []
                    text_buf = ""
                    for evt in history:
                        su = ((evt.get("params") or {}).get("update") or {}).get("sessionUpdate", "")
                        content = ((evt.get("params") or {}).get("update") or {}).get("content") or {}
                        if su == "agent_message_chunk" and content.get("type") == "text":
                            text_buf += content["text"]
                        else:
                            if text_buf:
                                collapsed.append({"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}})
                                text_buf = ""
                            collapsed.append(evt)
                    if text_buf:
                        collapsed.append({"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text_buf}}}})
                    for evt in collapsed:
                        emit("acp_event", {"session_id": acp_sid, "event": evt})
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "sync_seq", "seq": 0, "history_length": len(history)}})
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "archived_preview"}})

    @socketio.on("acp_prompt")
    def acp_prompt(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            session.send_prompt(data.get("text", ""))

    @socketio.on("acp_cancel")
    def acp_cancel(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            session.cancel()

    @socketio.on("acp_stall_info")
    def acp_stall_info(data):
        if not validate_csrf(data):
            return
        session = acp_manager.get_session(data.get("session_id"))
        if session:
            emit("acp_stall_info", {"session_id": data["session_id"], **session.get_stall_info()})

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

    @socketio.on("acp_list_archived")
    def acp_list_archived(data):
        if not validate_csrf(data):
            return
        emit("acp_archived_list", {"sessions": acp_manager.list_archived()})

    @socketio.on("acp_restore")
    def acp_restore(data):
        if not validate_csrf(data):
            return
        sid = data.get("session_id")
        if sid:
            ok = acp_manager.restore_session(sid, on_event=acp_on_event)
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
        acp_manager.rename_session(data.get("session_id"), data.get("name", ""))

    # --- Workflow handlers ---

    def _automation_dispatch(action, rule, message):
        """Called by the email poller when an inbound message matches a rule."""
        logger.info(f"Automation dispatch: action={action} rule={rule.get('id') if rule else 'default'} from={message.get('from')} subject={message.get('subject','')[:60]}")
        if action in ("dispatch", "summary"):
            result = _execute_rule(rule, inbound_message=message)
            record_history(rule, message, action, result)

    automation_manager.start(on_dispatch=_automation_dispatch)
