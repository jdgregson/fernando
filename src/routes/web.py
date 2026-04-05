from flask import Blueprint, render_template, Response, request, current_app
import json
import os
import requests
import msal
from src.services.tmux import tmux_service
from src.services.docker import docker_service
from src.services.acp import acp_manager

bp = Blueprint("web", __name__)


def _check_api_key():
    key = request.headers.get("X-API-Key") or request.form.get("api_key") or request.args.get("api_key")
    try:
        with open("/tmp/fernando-api-key") as f:
            return key == f.read().strip()
    except Exception:
        return False

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ms_config_dir = os.path.join(_project_root, "data", "microsoft")
_ms_config_file = os.path.join(_ms_config_dir, "config.json")
_ms_token_file = os.path.join(_ms_config_dir, "tokens.json")

def _auth_page(title, message):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{background:#252526;color:#3465a3;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}}
.c{{text-align:center}}h2{{font-weight:400}}</style></head>
<body><div class="c"><h2>{title}</h2><p>{message}</p></div></body></html>"""

from src.microsoft_scopes import SCOPES, REDIRECT_URI


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
    if not _check_api_key():
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}
    from src import socketio
    socketio.emit("mutating", {})
    return json.dumps({"ok": True}), 200, {"Content-Type": "application/json"}


@bp.route("/api/auth_check")
def api_auth_check():
    """API key validation endpoint."""
    if _check_api_key():
        return "", 200
    return "", 401


@bp.route("/api/rename_chat", methods=["POST"])
def api_rename_chat():
    if not _check_api_key():
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}
    data = request.get_json(force=True)
    sid = data.get("session_id")
    name = data.get("name", "")
    if not sid or not name:
        return json.dumps({"error": "Missing session_id or name"}), 400, {"Content-Type": "application/json"}
    acp_manager.rename_session(sid, name)
    return json.dumps({"ok": True}), 200, {"Content-Type": "application/json"}


@bp.route("/kasm/", defaults={"path": ""})
@bp.route("/kasm/<path:path>")
def kasm_proxy(path):
    if not _check_api_key():
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}
    if not docker_service.is_kasm_running():
        return "Kasm desktop is not running. Use the restart button in the sidebar.", 503

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
            api_key = request.args.get("api_key", "")
            ak = f"?api_key={api_key}" if api_key else ""
            # Fix absolute paths and append API key for static resources
            content = content.replace('="/kasm/', f'="/kasm/')  # no-op, paths already rewritten below
            content = content.replace('="/', f'="/kasm/')
            content = content.replace("='/", f"='/kasm/")
            content = content.replace("url(/", f"url(/kasm/")
            # Append api_key to all /kasm/ hrefs and srcs in HTML attributes
            if api_key:
                import re
                content = re.sub(r'(=["\'])/kasm/([^"\']*?)(["\'])', lambda m: f'{m.group(1)}/kasm/{m.group(2)}{"&" if "?" in m.group(2) else "?"}api_key={api_key}{m.group(3)}', content)
            # Inject a fetch/XHR interceptor to add api_key to all same-origin requests under /kasm/
            if "text/html" in content_type and api_key:
                inject = (
                    f'<script>'
                    f'(function(){{'
                    f'const ak="api_key={api_key}";'
                    f'function addKey(u){{if(typeof u!=="string")return u;if(u.startsWith("/kasm/")||u.startsWith("./")||u.startsWith("assets/")||(u.indexOf("://")===-1&&!u.startsWith("data:"))){{u+=(u.includes("?")?"&":"?")+ak}}return u}}'
                    f'const _fetch=window.fetch;'
                    f'window.fetch=function(u,o){{return _fetch.call(this,addKey(u),o)}};'
                    f'const _open=XMLHttpRequest.prototype.open;'
                    f'XMLHttpRequest.prototype.open=function(m,u){{return _open.call(this,m,addKey(u))}};'
                    f'const _WS=window.WebSocket;'
                    f'window.WebSocket=function(u,p){{if(typeof u==="string"&&u.includes("/websockify")){{u+=(u.includes("?")?"&":"?")+ak}}return p!==undefined?new _WS(u,p):new _WS(u)}};'
                    f'window.WebSocket.prototype=_WS.prototype;'
                    f'window.WebSocket.CONNECTING=_WS.CONNECTING;'
                    f'window.WebSocket.OPEN=_WS.OPEN;'
                    f'window.WebSocket.CLOSING=_WS.CLOSING;'
                    f'window.WebSocket.CLOSED=_WS.CLOSED;'
                    f'}})()'
                    f'</script>'
                )
                content = content.replace('<head>', '<head>' + inject, 1)
            # Fix WebSocket paths — add API key to websockify path setting
            if api_key:
                content = content.replace(
                    'value="websockify"',
                    f'value="websockify?api_key={api_key}"',
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
    return render_template("chat.html", acp_session_id=session_id, api_key=api_key,
                           home_dir=os.path.expanduser("~"),
                           agent_cwd=os.path.expanduser("~/fernando"))


@bp.route("/api/files/<path:filepath>")
def serve_file(filepath):
    """Serve files, caching images per-session for persistence."""
    if not _check_api_key():
        return "Unauthorized", 401
    import hashlib
    import mimetypes
    import shutil
    from flask import request, send_file

    session_id = request.args.get("session")
    home = os.path.realpath(os.path.expanduser("~"))
    cache_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "image_cache")

    # Resolve the requested path
    if filepath.startswith("tmp/"):
        full_path = os.path.realpath("/" + filepath)
    else:
        full_path = os.path.realpath(os.path.join(home, filepath))

    mime = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    is_image = mime.startswith("image/")

    # For images with a session, check cache first then copy on serve
    if is_image and session_id:
        ext = os.path.splitext(full_path)[1]
        file_hash = hashlib.sha256(full_path.encode()).hexdigest()[:16]
        session_cache = os.path.join(cache_dir, session_id)
        cached_path = os.path.join(session_cache, file_hash + ext)
        if os.path.isfile(cached_path):
            return send_file(cached_path, mimetype=mime)
        # Validate source path before caching
        allowed = [os.path.join(home, d) for d in ("Documents", "Downloads", "Desktop", "uploads", "fernando/data/desktop", "fernando/data/image_cache")]
        allowed.append("/tmp")
        if not any(full_path.startswith(d + "/") or full_path == d for d in allowed):
            return "Forbidden", 403
        if os.path.isfile(full_path):
            os.makedirs(session_cache, exist_ok=True)
            shutil.copy2(full_path, cached_path)
            return send_file(cached_path, mimetype=mime)
        return "Not found", 404

    # Non-image or no session: serve directly with path validation
    allowed = [os.path.join(home, d) for d in ("Documents", "Downloads", "Desktop", "uploads", "fernando/data/desktop", "fernando/data/image_cache")]
    allowed.append("/tmp")
    if not any(full_path.startswith(d + "/") or full_path == d for d in allowed):
        return "Forbidden", 403
    if not os.path.isfile(full_path):
        return "Not found", 404
    response = send_file(full_path, mimetype=mime)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@bp.route("/api/upload", methods=["POST"])
def upload_file():
    """Handle file uploads from chat UI. Saves to ~/uploads/ and returns the path."""
    from flask import jsonify
    if not _check_api_key():
        return jsonify(error="Unauthorized"), 401
    import uuid
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file"), 400
    upload_dir = os.path.join(os.path.expanduser("~"), "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex[:8]}_{os.path.basename(f.filename)}"
    dest = os.path.join(upload_dir, safe_name)
    f.save(dest)
    return jsonify(path=dest, name=f.filename)


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
        redirect_uri=config.get("redirect_uri", REDIRECT_URI),
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
