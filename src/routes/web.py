from flask import Blueprint, render_template, Response, request, current_app, make_response
import json
import os
import threading
import time
import requests
import msal
from src.services.pty_service import pty_service
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
    sessions = pty_service.list_sessions()
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


@bp.route("/api/spawn_subagent", methods=["POST"])
def api_spawn_subagent():
    """Create an ACP chat session and send a task as the first prompt."""
    if not _check_api_key():
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}
    data = request.get_json(force=True)
    task = data.get("task", "")
    name = data.get("name", "")
    if not task:
        return json.dumps({"error": "Missing task"}), 400, {"Content-Type": "application/json"}
    on_event = acp_manager.default_on_event
    session_id = acp_manager.create_session(on_event=on_event)
    if name:
        acp_manager.rename_session(session_id, name)

    def _send_when_ready():
        for _ in range(120):
            session = acp_manager.get_session(session_id)
            if session and session.ready:
                session.send_prompt(task)
                return
            time.sleep(1)
    threading.Thread(target=_send_when_ready, daemon=True).start()

    return json.dumps({"session_id": session_id}), 200, {"Content-Type": "application/json"}


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


@bp.route("/notes/<notebook>/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
@bp.route("/notes/<notebook>/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def notes_proxy(notebook, path):
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

    # Validate notebook name and get port
    import re as _re
    if not _re.match(r'^[a-z0-9][a-z0-9_-]{0,62}$', notebook):
        return json.dumps({"error": "Invalid notebook name"}), 400, {"Content-Type": "application/json"}

    from src.services.notebooks import get_notebook_port
    port = get_notebook_port(notebook)
    if not port:
        return json.dumps({"error": f"Notebook '{notebook}' is not running"}), 503, {"Content-Type": "application/json"}

    base_href = f"/notes/{notebook}/"

    try:
        url = f"http://localhost:{port}/{path}"
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
            content = content.replace('<base href="/"', f'<base href="{base_href}"', 1)
            sw_kill = "<script>Object.defineProperty(navigator,'serviceWorker',{get:()=>({register:()=>Promise.resolve(),ready:Promise.resolve(),addEventListener:()=>{},removeEventListener:()=>{},controller:null})});</script>"
            # iOS PWA iframes: IndexedDB is completely unavailable.
            # iOS PWA iframes: Safari blocks IndexedDB in iframes. Use fake-indexeddb,
            # a pure JS in-memory implementation of the full IndexedDB API.
            # Only installs if native IndexedDB doesn't work. Data persists to
            # localStorage via fake-idb-persist.js so it survives iframe reloads.
            idb_fix = """<script src="//""" + request.host + """/static/js/fake-idb-bundle.js"></script>
<script>
window.alert=function(){var a=[].slice.call(arguments);console.log('[SB alert]',a.join(' '))};
(function(){
  var works=false;
  try{var t=indexedDB.open('__test');if(t&&typeof t.addEventListener==='function')works=true;try{indexedDB.deleteDatabase('__test')}catch(e){}}catch(e){}
  if(works)return;
  var f=window.__fakeIDB;if(!f)return;
  window.indexedDB=f.fakeIndexedDB;
  window.IDBCursor=f.IDBCursor;window.IDBCursorWithValue=f.IDBCursorWithValue;
  window.IDBDatabase=f.IDBDatabase;window.IDBFactory=f.IDBFactory;
  window.IDBIndex=f.IDBIndex;window.IDBKeyRange=f.IDBKeyRange;
  window.IDBObjectStore=f.IDBObjectStore;window.IDBOpenDBRequest=f.IDBOpenDBRequest;
  window.IDBRequest=f.IDBRequest;window.IDBTransaction=f.IDBTransaction;
  window.IDBVersionChangeEvent=f.IDBVersionChangeEvent;
  if(typeof globalThis!=='undefined'){globalThis.indexedDB=f.fakeIndexedDB;globalThis.IDBKeyRange=f.IDBKeyRange}
})();
</script>
<script src="//""" + request.host + """/static/js/fake-idb-persist.js"></script>"""
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
  var nbName='""" + notebook + """';
  var lastPage='',bc=null;
  // Inject persistent CSS — SilverBullet wipes <style> tags from <head> on boot
  function ensureStyles(){
    if(document.getElementById('f-styles'))return;
    var s=document.createElement('style');s.id='f-styles';
    s.textContent='#sb-main .sb-panel{flex:1 1 50%!important;min-width:0!important}#sb-main #sb-editor{flex:1 1 50%!important;min-width:0!important}#sb-main .cm-editor .sb-header-inside{text-indent:0!important}.cm-editor .cm-content{font-size:14px!important}.sb-top{font-size:13px!important}.sb-top .sb-mini-editor .cm-content{font-size:13px!important}#f-bc{font-size:13px!important}#sb-root #sb-top .main .inner{max-width:100%}#sb-root #sb-main .cm-editor .cm-content{padding:20px 20px}@media (max-width:600px){#sb-root #sb-main .sb-panel{flex:0 0 100%!important;min-width:100%!important}#sb-root #sb-top .main .inner .wrapper{padding:0 10px}#sb-root #sb-main .cm-editor .cm-content{padding:10px 10px!important}}.panel[style="flex: 1 1 0%;"]{display:none}';
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
    if(parts.length<=1){el.style.display='none';bc.style.display='flex';bc.innerHTML='';
      if(val==='index'){var lbl=document.createElement('span');lbl.textContent=nbName;lbl.style.cssText='color:#d4d4d4';bc.appendChild(lbl)}
      else{var home=document.createElement('a');home.textContent=nbName;home.href='#';home.style.cssText='color:#5a9fd4;text-decoration:none;cursor:pointer';home.onclick=function(e){e.preventDefault();nav('index')};bc.appendChild(home);var sep=document.createElement('span');sep.textContent=' / ';sep.style.cssText='color:#6a7a8a;margin:0 2px';bc.appendChild(sep);var lbl=document.createElement('span');lbl.textContent=val;lbl.style.cssText='color:#d4d4d4';bc.appendChild(lbl)}
      return}
    el.style.display='none';bc.style.display='flex';bc.innerHTML='';
    var home=document.createElement('a');home.textContent=nbName;home.href='#';
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
/* Mobile: graph panel goes full-width */
@media (max-width: 600px) {
  #sb-root #sb-main .sb-panel { flex: 0 0 100% !important; min-width: 100% !important; }
  #sb-root #sb-top .main .inner .wrapper { padding: 0 10px; }
  #sb-root #sb-top .main .inner { max-width: 100%; }
  #sb-root #sb-main .cm-editor .cm-content { padding: 5px 10px; }
}
.panel[style="flex: 1 1 0%;"] { display: none; }
</style>"""
            content = content.replace('<html ', '<html data-theme="dark" style="background:#0d2848" ', 1)
            content = content.replace("<head>", "<head><style>html,body{background:#0d2848!important}</style>" + idb_fix + sw_kill + focus_script + breadcrumb_script + toc_refresh_script + graph_btn_script, 1)
            content = content.encode("utf-8")

        response = Response(content, resp.status_code, response_headers)
        if api_key_valid and request.args.get("api_key"):
            response.set_cookie("notes_auth", request.args["api_key"], httponly=True, samesite="Strict", path=f"/notes/{notebook}/")
        return response
    except Exception as e:
        return f"Notes error: {str(e)}", 503


# --- Jupyter Notebook Proxy ---
@bp.route("/jupyter/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
@bp.route("/jupyter/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def jupyter_proxy(path):
    # Auth: API key in URL (initial load) OR jupyter_auth cookie (sub-resources)
    api_key_valid = _check_api_key()
    cookie_valid = False
    if not api_key_valid:
        cookie = request.cookies.get("jupyter_auth")
        if cookie:
            try:
                with open("/tmp/fernando-api-key") as f:
                    cookie_valid = cookie == f.read().strip()
            except Exception:
                pass
    if not api_key_valid and not cookie_valid:
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}

    from src.services.jupyter import is_running, get_port, start
    if not is_running():
        ok, err = start()
        if not ok:
            return json.dumps({"error": f"Jupyter not available: {err}"}), 503, {"Content-Type": "application/json"}

    port = get_port()

    # Map /jupyter/X to Jupyter's /nbclassic/X for tree/notebook views,
    # but pass /api/, /static/, /custom/, /nbextensions/, /kernelspecs/ paths through directly
    if path.startswith(("api/", "static/", "nbextensions/", "custom/", "custom-preload", "kernelspecs/")):
        upstream_path = path
    elif path == "" or path.startswith("tree") or path.startswith("notebooks/"):
        upstream_path = f"nbclassic/{path}" if path else "nbclassic/tree/"
    else:
        upstream_path = f"nbclassic/{path}"

    try:
        url = f"http://127.0.0.1:{port}/{upstream_path}"
        if request.query_string:
            qs = request.query_string.decode("utf-8")
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
            timeout=30,
        )

        # Handle redirects — rewrite Location header
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            if location.startswith("/nbclassic/"):
                location = location.replace("/nbclassic/", "/jupyter/", 1)
            elif location.startswith("/"):
                location = "/jupyter" + location
            flask_resp = make_response("", resp.status_code)
            flask_resp.headers["Location"] = location
            if api_key_valid:
                flask_resp.set_cookie("jupyter_auth", request.args.get("api_key", ""), httponly=True, samesite="Strict", path="/jupyter/")
            return flask_resp

        excluded_headers = ["content-encoding", "content-length", "transfer-encoding", "connection"]
        response_headers = [
            (k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers
        ]

        content = resp.content
        content_type = resp.headers.get("content-type", "")

        if "text/html" in content_type:
            content = content.decode("utf-8", errors="ignore")
            # Rewrite absolute paths to go through /jupyter/ proxy
            content = content.replace("'/nbclassic'", "'/jupyter'")
            content = content.replace('"/nbclassic/', '"/jupyter/')
            content = content.replace("'/nbclassic/", "'/jupyter/")
            content = content.replace('"/static/nbclassic/', '"/jupyter/static/nbclassic/')
            content = content.replace("'/static/nbclassic/", "'/jupyter/static/nbclassic/")
            content = content.replace('"/custom/', '"/jupyter/custom/')
            content = content.replace("'/custom/", "'/jupyter/custom/")
            # RequireJS paths use absolute paths without trailing slash
            content = content.replace("'/custom'", "'/jupyter/custom'")
            content = content.replace("'/custom-preload'", "'/jupyter/custom/custom-preload'")
            content = content.replace("'/nbextensions'", "'/jupyter/nbextensions'")
            content = content.replace("'/kernelspecs'", "'/jupyter/kernelspecs'")
            # Rewrite wsUrl to use the direct WS proxy path with api_key
            # Also intercept XHR/fetch to rewrite absolute /api/ paths to /jupyter/api/
            ak = request.args.get("api_key", "")
            intercept = (
                "<script>"
                # Store api_key in sessionStorage on initial load, read it back on subsequent pages
                "if('" + ak + "')sessionStorage.setItem('_jAk','" + ak + "');"
                "var _ak=sessionStorage.getItem('_jAk')||'';"
                # Rewrite function for URLs
                "function _jRw(u){"
                "if(typeof u!=='string')return u;"
                "if(u.startsWith('/api/'))return '/jupyter'+u;"
                "if(u.startsWith('/nbclassic/'))return '/jupyter/'+u.slice(11);"
                "if(u.startsWith('/nbextensions/'))return '/jupyter'+u;"
                "if(u.startsWith('/kernelspecs/'))return '/jupyter'+u;"
                "if(u.startsWith('/custom/'))return '/jupyter'+u;"
                "return u;}"
                # Patch XMLHttpRequest.open
                "var _xo=XMLHttpRequest.prototype.open;"
                "XMLHttpRequest.prototype.open=function(m,u){arguments[1]=_jRw(u);return _xo.apply(this,arguments)};"
                # Patch fetch
                "var _ft=window.fetch;"
                "window.fetch=function(u,o){if(typeof u==='string')u=_jRw(u);return _ft.call(this,u,o)};"
                # Patch WebSocket for kernel connections
                "var _WS=window.WebSocket;"
                "window.WebSocket=function(u,p){"
                "if(typeof u==='string'){"
                "u=u.replace('/nbclassic/','/jupyter-ws/nbclassic/');"
                "if(u.includes('/api/'))u=u.replace('/api/','/jupyter-ws/api/');"
                "u+=(u.includes('?')?'&':'?')+'api_key='+_ak;"
                "}"
                "return p!==undefined?new _WS(u,p):new _WS(u)};"
                "window.WebSocket.prototype=_WS.prototype;"
                "window.WebSocket.CONNECTING=_WS.CONNECTING;"
                "window.WebSocket.OPEN=_WS.OPEN;"
                "window.WebSocket.CLOSING=_WS.CLOSING;"
                "window.WebSocket.CLOSED=_WS.CLOSED;"
                # Prevent links from opening in new tabs — stay in iframe
                "var _wo=window.open;"
                "window.open=function(u,t,f){if(u&&typeof u==='string'){u=_jRw(u);window.location.href=u;return window}return _wo.call(this,u,t,f)};"
                "document.addEventListener('click',function(e){"
                "var a=e.target.closest('a');"
                "if(a&&a.target==='_blank')a.target='_self';"
                "},true);"
                # Autosave every 10s, suppress "Notebook saved" toast
                "(function(){var nb=window.Jupyter&&Jupyter.notebook;"
                "if(nb){"
                "nb._update_autosave_interval=function(){};"
                "nb.events.off('notebook_saved.Notebook');"
                "nb.set_autosave_interval(10000);}"
                "else{setTimeout(arguments.callee,1000);}})();"
                # Listen for cell commands from parent Fernando window via postMessage
                "window.addEventListener('message',function(e){"
                "if(!e.source||e.source!==window.parent)return;"
                "var d=e.data;if(!d||d.type!=='jupyter-cmd')return;"
                "function run(){"
                "var nb=window.Jupyter&&Jupyter.notebook;"
                "if(!nb){setTimeout(function(){run()},500);return;}"
                "if(d.action==='insert_and_run'){"
                "var cell=nb.insert_cell_below('code');"
                "cell.set_text(d.source);"
                "nb.select(nb.find_cell_index(cell));"
                "var h=function(_,data){if(data.cell===cell){"
                "nb.events.off('finished_execute.CodeCell',h);"
                "nb.save_notebook();}};"
                "nb.events.on('finished_execute.CodeCell',h);"
                "nb.execute_cell();"
                "}"
                "if(d.action==='insert'){"
                "var cell=nb.insert_cell_below(d.cell_type||'code');"
                "cell.set_text(d.source);"
                "nb.select(nb.find_cell_index(cell));"
                "}"
                "if(d.action==='edit_cell'){"
                "nb.select(d.index||0);"
                "var cell=nb.get_selected_cell();"
                "if(cell)cell.set_text(d.source);"
                "}"
                "if(d.action==='run_selected'){"
                "var sc=nb.get_selected_cell();"
                "var h2=function(_,data){if(data.cell===sc){"
                "nb.events.off('finished_execute.CodeCell',h2);"
                "nb.save_notebook();}};"
                "nb.events.on('finished_execute.CodeCell',h2);"
                "nb.execute_cell();"
                "}"
                "if(d.action==='run_cell'){"
                "nb.select(d.index||0);"
                "var sc2=nb.get_selected_cell();"
                "var h3=function(_,data){if(data.cell===sc2){"
                "nb.events.off('finished_execute.CodeCell',h3);"
                "nb.save_notebook();}};"
                "nb.events.on('finished_execute.CodeCell',h3);"
                "nb.execute_cell();"
                "}"
                "if(d.action==='run_all'){"
                "nb.execute_all_cells();"
                "nb.events.one('notebook_save_success.Notebook',function(){});"
                "var pending=nb.get_cells().filter(function(c){return c.cell_type==='code';}).length;"
                "var h4=function(){pending--;if(pending<=0){"
                "nb.events.off('finished_execute.CodeCell',h4);"
                "nb.save_notebook();}};"
                "nb.events.on('finished_execute.CodeCell',h4);"
                "}"
                "if(d.action==='get_cells'){"
                "var cells=nb.get_cells().map(function(c,i){"
                "return{index:i,type:c.cell_type,source:c.get_text(),outputs:c.output_area?c.output_area.outputs:[]};"
                "});"
                "window.parent.postMessage({type:'jupyter-resp',id:d.id,cells:cells},'*');"
                "}"
                "}"
                "run();"
                "});"
                # Focus messaging — tell parent Fernando when this pane is clicked
                "document.addEventListener('click',function(){window.parent.postMessage({type:'notes-focus'},'*')});"
                # Report current notebook name to parent for sidebar label
                "setInterval(function(){"
                "var name='Jupyter';"
                "var nb=window.Jupyter&&Jupyter.notebook;"
                "if(nb&&nb.notebook_name)name=nb.notebook_name.replace('.ipynb','');"
                "else if(document.title&&document.title!=='Home')name=document.title;"
                "window.parent.postMessage({type:'jupyter-name',name:name},'*');"
                "},1000);"
                "</script>"
            )
            content = content.replace("</head>", intercept + "</head>", 1)
            content = content.encode("utf-8")

        flask_resp = make_response(content, resp.status_code)
        for k, v in response_headers:
            flask_resp.headers[k] = v
        if api_key_valid:
            flask_resp.set_cookie("jupyter_auth", request.args.get("api_key", ""), httponly=True, samesite="Strict", path="/jupyter/")
        return flask_resp

    except Exception as e:
        return f"Jupyter error: {str(e)}", 503


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
