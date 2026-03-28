from flask import request
from flask_socketio import emit
import os
import select
import ssl
import websocket as ws_client
from src.services.tmux import tmux_service
from src.services.docker import docker_service
from src.services.subagent import subagent_service
from src.services.acp import acp_manager
import threading
import base64
import secrets
import logging

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
    def get_sessions(data=None):
        if data and not validate_csrf(data):
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
    def restart_desktop(data=None):
        if data and not validate_csrf(data):
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

    @socketio.on("list_subagents")
    def list_subagents(data=None):
        if data and not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.list_subagents()
        emit("subagents_list", {"subagents": result})

    @socketio.on("create_subagent")
    def create_subagent(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        try:
            result = subagent_service.create_subagent(
                data["task_id"],
                data["task"],
                data.get("context_path"),
                data.get("schedule"),
            )
            emit("subagent_created", result)
        except Exception as e:
            emit("subagent_error", {"error": str(e)})

    @socketio.on("get_subagent_status")
    def get_subagent_status(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.get_subagent_status(data["task_id"])
        emit("subagent_status", result)

    @socketio.on("terminate_subagent")
    def terminate_subagent(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.terminate_subagent(data["task_id"])
        emit("subagent_terminated", result)

    @socketio.on("delete_subagent")
    def delete_subagent(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.delete_subagent(data["task_id"])
        emit("subagent_deleted", result)

    @socketio.on("get_at_jobs")
    def get_at_jobs(data=None):
        if data and not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.get_at_jobs()
        emit("at_jobs", {"jobs": result})

    @socketio.on("get_cron_jobs")
    def get_cron_jobs(data=None):
        if data and not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.get_cron_jobs()
        emit("cron_jobs", {"jobs": result})

    @socketio.on("remove_at_job")
    def remove_at_job(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.remove_at_job(data["job_id"])
        emit("at_job_removed", result)

    @socketio.on("remove_cron_job")
    def remove_cron_job(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        result = subagent_service.remove_cron_job(data["task_id"])
        emit("cron_job_removed", result)

    # --- ACP Chat handlers ---

    acp_subscribers = {}  # fernando_session_id -> set of socket sids

    def acp_on_event(session_id, event):
        """Broadcast ACP events to subscribed websocket clients."""
        sids = acp_subscribers.get(session_id, set())
        for sid in sids:
            socketio.emit("acp_event", {"session_id": session_id, "event": event}, room=sid)

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
            return
        acp_sid = data.get("session_id")
        if acp_sid:
            acp_subscribers.setdefault(acp_sid, set()).add(request.sid)
            # Replay history for reconnecting clients
            session = acp_manager.get_session(acp_sid)
            if session:
                for evt in session.history:
                    emit("acp_event", {"session_id": acp_sid, "event": evt})
                if session.ready:
                    emit("acp_event", {"session_id": acp_sid, "event": {"type": "session_ready"}})

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

    @socketio.on("acp_close")
    def acp_close(data):
        if not validate_csrf(data):
            return
        acp_sid = data.get("session_id")
        if acp_sid:
            acp_subscribers.pop(acp_sid, None)
            acp_manager.destroy_session(acp_sid)

    @socketio.on("acp_rename")
    def acp_rename(data):
        if not validate_csrf(data):
            return
        acp_manager.rename_session(data.get("session_id"), data.get("name", ""))
