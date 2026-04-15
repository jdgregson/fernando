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
    from html import escape
    t, m = escape(str(title)), escape(str(message))
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{background:#252526;color:#3465a3;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}}
.c{{text-align:center}}h2{{font-weight:400}}</style></head>
<body><div class="c"><h2>{t}</h2><p>{m}</p></div></body></html>"""

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
    # Auth: API key in URL (initial load) OR kasm_auth cookie (sub-resources)
    api_key_valid = _check_api_key()
    cookie_valid = False
    if not api_key_valid:
        cookie = request.cookies.get("kasm_auth")
        if cookie:
            try:
                with open("/tmp/fernando-api-key") as f:
                    cookie_valid = cookie == f.read().strip()
            except Exception:
                pass
    if not api_key_valid and not cookie_valid:
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
            # Fix absolute paths to go through /kasm/ proxy
            content = content.replace('="/', '="/kasm/')
            content = content.replace("='/", "='/kasm/")
            content = content.replace("url(/", "url(/kasm/")
            # Inject WebSocket interceptor
            if "text/html" in content_type and api_key:
                # CSS overrides: link to external stylesheet
                style = '<link rel="stylesheet" href="/static/css/kasm-overrides.css">'
                inject = (
                    f'<script>'
                    f'(function(){{'
                    f'const ak="api_key={api_key}";'
                    f'const _WS=window.WebSocket;'
                    f'window.WebSocket=function(u,p){{if(typeof u==="string"&&u.includes("websockify")){{u=u.replace("/kasm/websockify","/websockify");u+=(u.includes("?")?"&":"?")+ak}}return p!==undefined?new _WS(u,p):new _WS(u)}};'
                    f'window.WebSocket.prototype=_WS.prototype;'
                    f'window.WebSocket.CONNECTING=_WS.CONNECTING;'
                    f'window.WebSocket.OPEN=_WS.OPEN;'
                    f'window.WebSocket.CLOSING=_WS.CLOSING;'
                    f'window.WebSocket.CLOSED=_WS.CLOSED;'
                    f'}})()'
                    f'</script>'
                )
                content = content.replace('<head>', '<head>' + style + inject, 1)
            content = content.encode("utf-8")

        response = Response(content, resp.status_code, response_headers)
        # Set auth cookie on first authenticated request (API key in URL)
        if api_key_valid and request.args.get("api_key"):
            response.set_cookie("kasm_auth", request.args["api_key"], httponly=True, samesite="Strict", path="/kasm/")
        return response
    except Exception as e:
        return f"Kasm desktop error: {str(e)}", 503


@bp.route("/notes/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
@bp.route("/notes/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def notes_proxy(path):
    # Auth: API key in URL (initial load) OR notes_auth cookie (sub-resources)
    api_key_valid = _check_api_key()
    cookie_valid = False
    if not api_key_valid:
        cookie = request.cookies.get("notes_auth")
        if cookie:
            try:
                with open("/tmp/fernando-api-key") as f:
                    cookie_valid = cookie == f.read().strip()
            except Exception:
                pass
    if not api_key_valid and not cookie_valid:
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}

    try:
        url = f"http://localhost:3001/{path}"
        if request.query_string:
            qs = request.query_string.decode("utf-8")
            # Strip api_key from forwarded query string
            import re
            qs = re.sub(r'(^|&)api_key=[^&]*', '', qs).lstrip('&')
            if qs:
                url += f"?{qs}"

        headers = {
            k: v for k, v in request.headers
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
            timeout=10,
        )

        excluded_headers = ["content-encoding", "content-length", "transfer-encoding", "connection"]
        response_headers = [
            (k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers
        ]

        content = resp.content
        content_type = resp.headers.get("content-type", "")

        # SilverBullet uses <base href="/"> for relative paths — rewrite to /notes/
        # Also disable service worker registration (fails behind reverse proxy, not needed)
        if "text/html" in content_type:
            content = content.decode("utf-8", errors="ignore")
            content = content.replace('<base href="/"', '<base href="/notes/"', 1)
            sw_kill = "<script>Object.defineProperty(navigator,'serviceWorker',{get:()=>({register:()=>Promise.resolve(),ready:Promise.resolve(),addEventListener:()=>{},removeEventListener:()=>{},controller:null})});</script>"
            focus_script = "<script>document.addEventListener('click',()=>window.parent.postMessage({type:'notes-focus'},'*'));</script>"
            # Clickable breadcrumbs for SilverBullet.
            #
            # SilverBullet's developers made the page path in the top bar an editable
            # CodeMirror text field. Not a link. Not a breadcrumb. A text editor. For a
            # file path. Their idea of navigation is: click the path, delete part of it,
            # type a new path, and press Enter. Like an animal.
            #
            # Hansel and Gretel dropped breadcrumbs to RETRACE THEIR STEPS, not to
            # rename the forest. Every file manager, every IDE, every browser, every OS
            # since the 1990s has understood that path segments are clickable navigation.
            # SilverBullet decided that was too intuitive and made them editable instead.
            #
            # Since we can't fix their judgment, we fix their UI: when the current page
            # is in a subdirectory, we hide their CodeMirror abomination entirely and
            # replace it with actual clickable HTML links. Clicking the last segment
            # (current page name) reveals the original editor for the rare case where
            # you actually want to type a path like a caveman.
            breadcrumb_script = """<script>
(function(){
  var lastPage='',bc=null;
  // Inject persistent CSS — SilverBullet wipes <style> tags from <head> on boot
  function ensureStyles(){
    if(document.getElementById('f-styles'))return;
    var s=document.createElement('style');s.id='f-styles';
    s.textContent='#sb-main .sb-panel{flex:1 1 50%!important;min-width:0!important}#sb-main #sb-editor{flex:1 1 50%!important;min-width:0!important}#sb-main .cm-editor .sb-header-inside{text-indent:0!important}';
    document.head.appendChild(s);
  }
  function nav(path){
    if(window.client&&client.navigate){client.navigate({path:path+'.md'},false,false)}
  }
  function update(){
    ensureStyles();
    var el=document.querySelector('.sb-mini-editor');
    if(!el)return;
    var val=(el.querySelector('.cm-line')||{}).textContent||'';
    if(!val||val===lastPage)return;
    lastPage=val;
    var parts=val.split('/');
    if(!bc){bc=document.createElement('div');bc.id='f-bc';
      bc.style.cssText='display:none;align-items:center;gap:0;height:100%;padding:0 4px;font:14px/1 ui-sans-serif,system-ui,sans-serif';
      el.parentNode.insertBefore(bc,el)}
    if(parts.length<=1){bc.style.display='none';el.style.display='';return}
    el.style.display='none';bc.style.display='flex';bc.innerHTML='';
    var home=document.createElement('a');home.textContent='Notes';home.href='#';
    home.style.cssText='color:#5a9fd4;text-decoration:none;cursor:pointer';
    home.onclick=function(e){e.preventDefault();nav('index')};
    bc.appendChild(home);
    var hsep=document.createElement('span');hsep.textContent=' / ';hsep.style.cssText='color:#6a7a8a;margin:0 2px';bc.appendChild(hsep);
    for(var i=0;i<parts.length;i++){
      if(i>0){var sep=document.createElement('span');sep.textContent=' / ';sep.style.cssText='color:#6a7a8a;margin:0 2px';bc.appendChild(sep)}
      var a=document.createElement('a');a.textContent=parts[i];a.href='#';
      if(i<parts.length-1){a.style.cssText='color:#5a9fd4;text-decoration:none;cursor:pointer';
        (function(p){a.onclick=function(e){e.preventDefault();nav(p)}})(parts.slice(0,i+1).join('/')+'/index');
      } else {a.style.cssText='color:#d4d4d4;text-decoration:none;cursor:default';
        a.onclick=function(e){e.preventDefault()};
      }
      bc.appendChild(a);
    }
  }
  setInterval(update,300);
})();
</script>"""
            # Force TOC/widget refresh when page content changes externally.
            # SilverBullet's TOC widget only updates when you type in the editor.
            # External writes via the /.fs/ API change the content but don't trigger
            # a widget refresh, leaving the TOC stale. We watch the editor content
            # and call index.refreshWidgets when it changes.
            toc_refresh_script = """<script>
(function(){
  var lastContent='',timer=null;
  function check(){
    if(!window.client)return;
    var ed=client.editorView;
    if(!ed)return;
    var c=ed.state.sliceDoc(0,2000);
    if(lastContent&&c!==lastContent){
      clearTimeout(timer);
      timer=setTimeout(function(){
        client.clientSystem.localSyscall('system.invokeFunction',['index.refreshWidgets']).catch(function(){});
      },500);
    }
    lastContent=c;
  }
  setInterval(check,1000);
})();
</script>"""
            # Graph view toggle button — adds a clickable button to the SB top bar
            graph_btn_script = """<script>
(function(){
  function addBtn(){
    var actions=document.querySelector('.sb-actions');
    if(!actions||document.getElementById('f-graph-btn'))return;
    var btn=document.createElement('button');btn.id='f-graph-btn';btn.type='button';
    btn.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><line x1="6.7" y1="7.5" x2="10.5" y2="16.5"/><line x1="17.3" y1="7.5" x2="13.5" y2="16.5"/><line x1="14" y1="18" x2="17" y2="18"/><line x1="19" y1="8" x2="19" y2="16"/></svg>';
    btn.title='Toggle Graph View';
    btn.onclick=function(e){e.preventDefault();e.stopPropagation();
      if(window.client){client.runCommandByName('Atlas: Toggle Graph View').catch(function(err){console.error('Atlas toggle failed',err)})}};
    actions.prepend(btn);
  }
  setInterval(addBtn,1000);
})();
</script>
<style>
/* Force RHS panel to 50% width and prevent overflow */
#sb-main { overflow: hidden !important; }
#sb-main .sb-panel { flex: 1 1 50% !important; min-width: 0 !important; }
#sb-main #sb-editor { flex: 1 1 50% !important; min-width: 0 !important; overflow: hidden !important; --editor-width: 100% !important; }
#sb-main #sb-editor .cm-editor { overflow: hidden !important; }
#sb-main #sb-editor .cm-scroller { overflow-x: hidden !important; }
#sb-main #sb-editor .cm-content { overflow-wrap: break-word !important; word-break: break-word !important; }
#sb-main #sb-editor .cm-line { overflow-wrap: break-word !important; word-break: break-word !important; }
</style>"""
            content = content.replace("<head>", "<head>" + sw_kill + focus_script + breadcrumb_script + toc_refresh_script + graph_btn_script, 1)
            content = content.encode("utf-8")

        response = Response(content, resp.status_code, response_headers)
        if api_key_valid and request.args.get("api_key"):
            response.set_cookie("notes_auth", request.args["api_key"], httponly=True, samesite="Strict", path="/notes/")
        return response
    except Exception as e:
        return f"Notes error: {str(e)}", 503


@bp.route("/chat/<session_id>")
def chat_page(session_id):
    if not _check_api_key():
        return "Unauthorized", 401
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
        allowed = [os.path.join(home, d) for d in ("Documents", "Downloads", "Desktop", "uploads", "fernando/data/desktop", "fernando/data/image_cache", "fernando/data/file_cache")]
        allowed.append("/tmp")
        if not any(full_path.startswith(d + "/") or full_path == d for d in allowed):
            return "Forbidden", 403
        if os.path.isfile(full_path):
            os.makedirs(session_cache, exist_ok=True)
            shutil.copy2(full_path, cached_path)
            return send_file(cached_path, mimetype=mime)
        return "Not found", 404

    # Non-image or no session: serve directly with path validation
    allowed = [os.path.join(home, d) for d in ("Documents", "Downloads", "Desktop", "uploads", "fernando/data/desktop", "fernando/data/image_cache", "fernando/data/file_cache")]
    allowed.append("/tmp")
    if not any(full_path.startswith(d + "/") or full_path == d for d in allowed):
        return "Forbidden", 403
    if not os.path.isfile(full_path):
        return "Not found", 404
    response = send_file(full_path, mimetype=mime)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Content-Disposition"] = f"attachment; filename=\"{os.path.basename(full_path)}\""
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
