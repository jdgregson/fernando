from flask import request
from flask_socketio import emit
import os
import select
import ssl
import websocket as ws_client
from src.services.tmux import tmux_service
from src.services.docker import docker_service
from src.services.subagent import subagent_service
import threading
import base64
import secrets

# Store CSRF tokens per session
csrf_tokens = {}


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
        emit("sessions_list", {"sessions": sessions})

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

        print(f"Attaching session {session_name} to terminal {terminal}, sid={sid}")

        # Clean up any existing session for this terminal
        tmux_service.cleanup_session(sid)

        master = tmux_service.attach_session(session_name, sid)

        print(f"Master fd: {master}")

        def read_output():
            print(f"Starting read_output loop for {sid}")
            while tmux_service.has_session(sid):
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        output = os.read(master, 10240)
                        if output:
                            decoded = output.decode("utf-8", errors="ignore")
                            print(
                                f"Sending {len(decoded)} chars to terminal {terminal}"
                            )
                            socketio.emit(
                                "output",
                                {"terminal": terminal, "data": decoded},
                                room=client_sid,
                            )
                    except Exception as e:
                        print(f"Read error: {e}")
                        break
            print(f"Exiting read_output loop for {sid}")

        socketio.start_background_task(read_output)

    @socketio.on("create_session")
    def create_session(data):
        if not validate_csrf(data):
            emit("error", {"message": "Invalid CSRF token"})
            return
        session_type = data.get("type", "shell")
        name = tmux_service.create_session_with_type(session_type)
        emit("session_created", {"name": name})

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
        csrf_tokens.pop(request.sid, None)
        tmux_service.cleanup_session(f"{request.sid}_1")
        tmux_service.cleanup_session(f"{request.sid}_2")

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
