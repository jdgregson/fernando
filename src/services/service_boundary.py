import os

import requests
from flask import Response, request, stream_with_context

from src.config import get_config


RUNTIME_ENDPOINTS = frozenset({
    'web.api_working_agents', 'web.api_mutating_notify', 'web.api_auth_check',
    'web.api_test_acp_command', 'web.api_spawn_subagent', 'web.api_subagent_schedule',
    'web.api_send_continuation', 'web.api_chat_resume', 'web.api_acp_cancel', 'web.api_acp_sleep',
    'web.api_acp_wake', 'web.api_acp_terminate', 'web.api_acp_child_messages',
    'web.api_acp_read_child_messages', 'web.api_acp_read_parent_messages',
    'web.api_acp_agent_message', 'web.api_rename_chat', 'web.api_chat_context',
    'web.api_step_progress', 'web.api_cancel_pipeline', 'web.api_cancel_commands',
    'web.api_authorization_request', 'web.api_authorization_grant',
    'web.api_authorization_state', 'web.api_authorization_config',
    'web.api_authorization_revoke', 'web.api_mcp_call', 'web.jupyter_proxy',
    'web.auth_callback', 'runtime_status',
})
HOP_HEADERS = frozenset({
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length',
})


def runtime_url():
    return 'http://127.0.0.1:' + str(int(get_config('FLASK_PORT', '5000')))


def web_url():
    return 'http://127.0.0.1:' + str(int(get_config('WEB_PORT', '5001')))


def runtime_status():
    from pathlib import Path
    key = Path('/tmp/fernando-api-key').read_text().strip()
    with requests.Session() as client:
        client.trust_env = False
        response = client.get(runtime_url() + '/api/runtime/status', headers={'X-API-Key': key}, timeout=5)
        response.raise_for_status()
        return response.json()


def install_boundary(app, role):
    if role not in {'web', 'runtime'}:
        raise ValueError('Invalid service role')
    if runtime_url() == web_url():
        raise ValueError('WEB_PORT must differ from FLASK_PORT')
    app.config['FERNANDO_ROLE'] = role

    if role == 'web':
        @app.get('/api/web/ready')
        def web_ready():
            from src.routes.web import _check_api_key
            if not _check_api_key():
                return {'error': 'Unauthorized'}, 401
            return {'pid': os.getpid(), 'role': 'web'}

    if role == 'runtime':
        @app.get('/api/runtime/status')
        def runtime_status():
            from src.routes.web import _check_api_key
            from src.services.acp import acp_manager
            from src.services.pty_service import pty_service
            if not _check_api_key():
                return {'error': 'Unauthorized'}, 401
            return {'pid': os.getpid(), 'terminals': pty_service.list_sessions(), 'working_count': acp_manager.working_agent_count()}

    @app.before_request
    def route_to_owner():
        runtime_owned = request.endpoint in RUNTIME_ENDPOINTS or request.path == '/api/runtime/status'
        if runtime_owned == (role == 'runtime'):
            return None
        if request.headers.get('X-Fernando-Service-Hop'):
            return {'error': 'Service routing loop'}, 502
        destination = runtime_url() if runtime_owned else web_url()
        connection_headers = {name.strip().lower() for name in request.headers.get('Connection', '').split(',')}
        excluded = HOP_HEADERS | connection_headers
        headers = {name: value for name, value in request.headers if name.lower() not in excluded and name.lower() != 'x-fernando-service-hop'}
        headers['X-Fernando-Service-Hop'] = role
        headers['Host'] = request.host
        headers['Accept-Encoding'] = 'identity'
        from urllib.parse import quote
        path = quote(request.path, safe='/')
        if request.query_string:
            path += '?' + request.query_string.decode('ascii')
        client = requests.Session()
        client.trust_env = False
        try:
            upstream = client.request(
                request.method, destination + path,
                headers=headers, data=request.get_data(), stream=True,
                allow_redirects=False, timeout=(3, 300),
            )
        except requests.ConnectionError:
            client.close()
            return {'error': 'Web service is restarting' if role == 'runtime' else 'Runtime service is unavailable'}, 503
        response_connection_headers = {name.strip().lower() for name in upstream.headers.get('Connection', '').split(',')}
        response_headers = [(name, value) for name, value in upstream.raw.headers.items()
                            if name.lower() not in HOP_HEADERS | response_connection_headers]

        @stream_with_context
        def body():
            try:
                yield from upstream.raw.stream(65536, decode_content=False)
            finally:
                upstream.close()
                client.close()

        response = Response(body(), status=upstream.status_code, headers=response_headers)
        response.call_on_close(upstream.close)
        response.call_on_close(client.close)
        return response
