import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
import socketio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import groups
from src.services.jobs import store
from src.services.service_boundary import runtime_url


def main():
    os.umask(0o077)
    base = runtime_url()
    key = Path('/tmp/fernando-api-key').read_text().strip()
    http = requests.Session()
    http.trust_env = False
    headers = {'X-API-Key': key}
    prefix = 'job-session-check-' + uuid.uuid4().hex[:8]
    state = {'csrf': None, 'groups': [], 'jobs': {}, 'inventory': set()}
    client = socketio.Client(reconnection=False, http_session=http, websocket_extra_options={'suppress_origin': True})
    client.on('connected', lambda data: state.update(csrf=data['csrf_token']))
    client.on('group_created', lambda data: state['groups'].append(data['group']) if data['group']['name'].startswith(prefix) else None)
    client.on('job_changed', lambda data: state['jobs'].update({row['id']: row for row in data['jobs'] if row['name'].startswith(prefix)}))
    client.on('sessions_list', lambda data: state.update(inventory={row['id'] for row in data.get('job_sessions', [])}))
    created_groups = []
    jid = None
    checks = []

    def api(path, data=None):
        response = http.get(base + path, headers=headers, timeout=10) if data is None else http.post(base + path, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()

    def wait(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        raise TimeoutError('Expected live session state was not observed')

    def command(name, data):
        client.call(name, {**data, 'csrf_token': state['csrf']}, timeout=10)

    try:
        client.connect(base + '?api_key=' + quote(key), transports=['websocket'], wait_timeout=15)
        wait(lambda: state['csrf'])
        for number in range(2):
            command('group_create', {'name': prefix + '-' + str(number), 'color': '#336699'})
            wait(lambda: len(state['groups']) > number)
            created_groups.append(state['groups'][number]['id'])
        job = api('/api/jobs/drafts', {'group_id': created_groups[0]})
        jid = job['id']
        values = {**job['draft'], 'name': prefix, 'scope': 'Unsent verification fixture. Do not execute work.'}
        job = api('/api/jobs/' + jid + '/draft', {'draft': values, 'revision': 0})
        wait(lambda: state['jobs'].get(jid, {}).get('name') == prefix and jid in state['inventory'])
        checks.append('draft creation and cross-connection inventory update')
        assert groups.get_session_groups()['job:' + jid] == created_groups[0]
        api('/api/jobs/' + jid + '/operator', {'action': 'close'})
        wait(lambda: state['jobs'].get(jid, {}).get('view_open') == 0 and jid not in state['inventory'])
        closed = api('/api/jobs/' + jid)
        assert closed['draft'] == values and closed['state'] == 'draft' and closed['paused'] == 1
        assert any(row['id'] == jid for row in api('/api/jobs')['jobs'])
        api('/api/jobs/' + jid + '/operator', {'action': 'open'})
        wait(lambda: state['jobs'].get(jid, {}).get('view_open') == 1 and jid in state['inventory'])
        checks.append('view close/reopen retains the Job without ACP archival')
        api('/api/jobs/' + jid + '/operator', {'action': 'rename', 'name': prefix + '-renamed'})
        wait(lambda: state['jobs'].get(jid, {}).get('name') == prefix + '-renamed')
        checks.append('rename broadcasts authoritative metadata')
        command('group_move_session', {'session_key': 'job:' + jid, 'group_id': created_groups[1]})
        assert api('/api/jobs/' + jid)['group_id'] == created_groups[1]
        assert groups.get_session_groups()['job:' + jid] == created_groups[1]
        checks.append('real WebSocket group move updates dispatch membership')
        command('group_delete', {'group_id': created_groups[1]})
        created_groups.pop()
        assert api('/api/jobs/' + jid)['group_id'] is None
        assert 'job:' + jid not in groups.get_session_groups()
        checks.append('group deletion retains Job in Ungrouped')
        command('group_move_session', {'session_key': 'job:' + jid, 'group_id': created_groups[0]})
        command('group_archive', {'group_id': created_groups[0]})
        wait(lambda: api('/api/jobs/' + jid)['group_suspended'])
        assert api('/api/jobs/' + jid)['state'] == 'draft'
        command('group_restore', {'group_id': created_groups[0]})
        wait(lambda: not api('/api/jobs/' + jid)['group_suspended'])
        assert api('/api/jobs/' + jid)['draft']['scope'] == values['scope']
        checks.append('group metadata archive/restore preserves application data')
        for action in ('archive', 'restore', 'fork', 'sleep', 'wake'):
            response = http.post(base + '/api/jobs/' + jid + '/operator', headers=headers, json={'action': action}, timeout=10)
            assert response.status_code == 400
        checks.append('ACP chat lifecycle actions are not Job operations')
        assert http.get(base + '/api/jobs', timeout=10).status_code == 401
        for path in ('job-session.js', 'job-panel.js', 'panes.js'):
            response = http.get(base + '/static/js/' + path, headers=headers, timeout=10)
            response.raise_for_status()
            assert response.content == Path('src/static/js', path).read_bytes()
        checks.append('live assets match source and unauthenticated API access is denied')
    finally:
        if jid:
            current = api('/api/jobs/' + jid)
            assert current['state'] == 'draft' and not current['assignments']
            api('/api/jobs/' + jid + '/operator', {'action': 'close'})
            wait(lambda: state['jobs'].get(jid, {}).get('view_open') == 0 and jid not in state['inventory'])
            command('group_move_session', {'session_key': 'job:' + jid, 'group_id': None})
            with store.connect() as db:
                db.execute('DELETE FROM job_events WHERE job_id=?', (jid,))
                db.execute('DELETE FROM jobs WHERE id=?', (jid,))
        for gid in created_groups:
            assert not any(value == gid for value in groups.get_session_groups().values())
            command('group_delete', {'group_id': gid})
        if client.connected:
            client.disconnect()
        http.close()
    report = {'checks': checks, 'fixtures_removed': True}
    Path('/tmp/opencode/job-common-live-0cfd3675.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
