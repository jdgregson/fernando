from flask import Blueprint, render_template, Response, request
import json
import os
import requests
import msal
from src.services.tmux import tmux_service
from src.services.docker import docker_service
from src.services.acp import acp_manager

bp = Blueprint("web", __name__)

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ms_config_dir = os.path.join(_project_root, "data", "microsoft")
_ms_config_file = os.path.join(_ms_config_dir, "config.json")
_ms_token_file = os.path.join(_ms_config_dir, "tokens.json")

def _auth_page(title, message):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{background:#252526;color:#3465a3;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}}
.c{{text-align:center}}h2{{font-weight:400}}</style></head>
<body><div class="c"><h2>{title}</h2><p>{message}</p></div></body></html>"""

SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
]


@bp.route("/")
def index():
    sessions = tmux_service.list_sessions()
    # Read API key
    try:
        with open("/tmp/fernando-api-key", "r") as f:
            api_key = f.read().strip()
    except:
        api_key = ""
    return render_template("index.html", sessions=sessions, api_key=api_key)


@bp.route("/api/mutating", methods=["POST"])
def api_mutating_notify():
    from src import socketio
    socketio.emit("mutating", {})
    return json.dumps({"ok": True}), 200, {"Content-Type": "application/json"}


@bp.route("/kasm/", defaults={"path": ""})
@bp.route("/kasm/<path:path>")
def kasm_proxy(path):
    if not docker_service.start_kasm():
        return "Kasm desktop is starting, please wait...", 503

    # Read VNC password
    try:
        with open("/tmp/fernando-vnc-password", "r") as f:
            vnc_password = f.read().strip()
    except:
        return "VNC password not found", 500

    try:
        url = f"https://localhost:6901/{path}"
        headers = {
            k: v
            for k, v in request.headers
            if k.lower() not in ["host", "connection", "upgrade"]
        }

        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            stream=False,
            timeout=5,
            verify=False,
            auth=("kasm_user", vnc_password),
        )

        excluded_headers = [
            "content-encoding",
            "content-length",
            "transfer-encoding",
            "connection",
        ]
        response_headers = [
            (k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers
        ]

        content = resp.content
        content_type = resp.headers.get("content-type", "")

        # Rewrite paths in HTML/JS/CSS
        if (
            "text/html" in content_type
            or "javascript" in content_type
            or "text/css" in content_type
        ):
            content = content.decode("utf-8", errors="ignore")
            # Fix absolute paths
            content = content.replace('="/', '="/kasm/')
            content = content.replace("='/", "='/kasm/")
            content = content.replace("url(/", "url(/kasm/")
            # Fix WebSocket paths in JS
            content = content.replace(
                'new WebSocket("wss://" + ',
                'new WebSocket("wss://" + window.location.host + "/kasm" + ',
            )
            content = content.replace(
                'new WebSocket("ws://" + ',
                'new WebSocket("ws://" + window.location.host + "/kasm" + ',
            )
            content = content.replace(
                'new WebSocket(("https:" === ',
                'new WebSocket(("https:" === window.location.protocol ? "wss://" : "ws://") + window.location.host + "/kasm" + ',
            )
            content = content.encode("utf-8")

        return Response(content, resp.status_code, response_headers)
    except Exception as e:
        return f"Kasm desktop error: {str(e)}", 503


@bp.route("/chat/<session_id>")
def chat_page(session_id):
    try:
        with open("/tmp/fernando-api-key", "r") as f:
            api_key = f.read().strip()
    except:
        api_key = ""
    return render_template("chat.html", acp_session_id=session_id, api_key=api_key)


@bp.route("/files/<path:filepath>")
def serve_file(filepath):
    """Serve files from the home directory (for displaying screenshots etc.)."""
    import mimetypes
    from flask import send_file
    full_path = os.path.join(os.path.expanduser("~"), filepath)
    full_path = os.path.realpath(full_path)
    home = os.path.realpath(os.path.expanduser("~"))
    if not full_path.startswith(home + "/"):
        return "Forbidden", 403
    if not os.path.isfile(full_path):
        return "Not found", 404
    mime = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return send_file(full_path, mimetype=mime)


@bp.route("/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return _auth_page("Missing authorization code", ""), 400

    try:
        with open(_ms_config_file) as f:
            config = json.load(f)
    except Exception:
        return "Microsoft not configured", 500

    msal_app = msal.ConfidentialClientApplication(
        config["client_id"],
        authority=f"https://login.microsoftonline.com/{config['tenant_id']}",
        client_credential=config.get("client_secret"),
    )

    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=config.get("redirect_uri", "http://localhost:8080/auth/callback"),
    )

    if "access_token" in result:
        tokens = {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token", ""),
        }
        os.makedirs(_ms_config_dir, mode=0o700, exist_ok=True)
        with open(_ms_token_file, "w") as f:
            json.dump(tokens, f, indent=2)
        os.chmod(_ms_token_file, 0o600)
        return _auth_page("Authenticated successfully!", "You can close this tab.")
    else:
        error = result.get("error_description", result.get("error", "Unknown error"))
        return _auth_page("Authentication failed", error), 400
