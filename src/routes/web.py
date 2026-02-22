from flask import Blueprint, render_template, Response, request
import requests
from src.services.tmux import tmux_service
from src.services.docker import docker_service

bp = Blueprint('web', __name__)

@bp.route('/')
def index():
    sessions = tmux_service.list_sessions()
    return render_template('index.html', sessions=sessions)

@bp.route('/kasm/', defaults={'path': ''})
@bp.route('/kasm/<path:path>')
def kasm_proxy(path):
    if not docker_service.start_kasm():
        return "Kasm desktop is starting, please wait...", 503
    
    try:
        url = f'https://localhost:6901/{path}'
        headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'connection', 'upgrade']}
        
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
            auth=('kasm_user', 'password')
        )
        
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers]
        
        content = resp.content
        content_type = resp.headers.get('content-type', '')
        
        # Rewrite paths in HTML/JS/CSS
        if 'text/html' in content_type or 'javascript' in content_type or 'text/css' in content_type:
            content = content.decode('utf-8', errors='ignore')
            # Fix absolute paths
            content = content.replace('="/', '="/kasm/')
            content = content.replace("='/", "='/kasm/")
            content = content.replace('url(/', 'url(/kasm/')
            # Fix WebSocket paths in JS
            content = content.replace('new WebSocket("wss://" + ', 'new WebSocket("wss://" + window.location.host + "/kasm" + ')
            content = content.replace('new WebSocket("ws://" + ', 'new WebSocket("ws://" + window.location.host + "/kasm" + ')
            content = content.replace('new WebSocket(("https:" === ', 'new WebSocket(("https:" === window.location.protocol ? "wss://" : "ws://") + window.location.host + "/kasm" + ')
            content = content.encode('utf-8')
        
        return Response(content, resp.status_code, response_headers)
    except Exception as e:
        return f"Kasm desktop error: {str(e)}", 503

@bp.route('/history/<session_name>')
def session_history(session_name):
    history = tmux_service.get_history(session_name)
    return render_template('history.html', session=session_name, history=history)
