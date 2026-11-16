import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.jobs import identifier, store
from src.services.service_boundary import runtime_url


def main():
    os.umask(0o077)
    output = Path('/tmp/opencode') / ('job-parallel-live-' + identifier()[:8])
    output.mkdir()
    targets = [output / ('input-' + str(number) + '.txt') for number in range(3)]
    contents = ['parallel-fixture-' + str(number) + '\n' for number in range(3)]
    for target, content in zip(targets[1:], contents[1:]):
        target.write_text(content)
    model = {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}
    lines = [f'Read exactly {path}, confirm bytes equal {content!r}, and record its SHA-256.' for path, content in zip(targets, contents)]
    scope = '\n'.join(lines) + '\nThese are independent, read-only tasks. If an exact target file is missing, immediately request it with job_operator. Do not create files or search for substitutes. No extra checks or changes are required.'
    jid = identifier()
    requirement_ids = [identifier() for _ in targets]
    with store.connect() as db:
        now = time.time()
        db.execute("INSERT INTO jobs(id,name,scope,source_name,source_sha256,worker,reviewer,state,plan_accepted,revision,created,updated,paused,max_workers,reuse_workers) VALUES(?,?,?,?,?,?,?,'running',1,1,?,?,1,2,1)",
                   (jid, 'Parallel and reuse verification', scope, 'Controlled concurrency fixture', hashlib.sha256(scope.encode()).hexdigest(), json.dumps(model), json.dumps(model), now, now))
        for rid, line in zip(requirement_ids, lines):
            spec = {'key': rid[:8], 'source': line, 'work': line, 'acceptance': line,
                    'preconditions': 'The exact file must exist. If absent, use job_operator immediately; do not create it or search for substitutes.',
                    'access': 'Read only the assigned file', 'evidence': 'Original tool invocation and output showing bytes and SHA-256',
                    'controls': 'Compare the actual bytes with the exact expected literal. No additional controls are required.', 'dependencies': [], 'dependency_ids': []}
            db.execute('INSERT INTO requirements(id,job_id,spec) VALUES(?,?,?)', (rid, jid, json.dumps(spec)))
    http = requests.Session()
    http.trust_env = False
    headers = {'X-API-Key': Path('/tmp/fernando-api-key').read_text().strip()}
    base = runtime_url()

    def api(path, data=None):
        response = http.get(base + path, headers=headers, timeout=20) if data is None else http.post(base + path, headers=headers, json=data, timeout=20)
        response.raise_for_status()
        return response.json()

    peak = 0
    overlap = []
    answered = False
    report = None
    print('LIVE_JOB', jid, 'EVIDENCE', output, flush=True)
    try:
        api('/api/jobs/' + jid + '/operator', {'action': 'resume'})
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            job = api('/api/jobs/' + jid)
            active = [item for item in job['assignments'] if item['state'] in ('reserved', 'active')]
            assert len({item['requirement_id'] for item in active}) <= 2, active
            dispatched = [item for item in active if item['state'] == 'active']
            if len(dispatched) > peak:
                peak = len(dispatched)
                overlap.append({'observed_at': time.time(), 'assignments': [{'id': item['id'], 'requirement_id': item['requirement_id'], 'role': item['role'], 'session_id': item['session_id']} for item in dispatched]})
                print('CONCURRENT_ASSIGNMENTS', peak, flush=True)
            missing = next((item for item in job['operator_requests'] if item['requirement_id'] == requirement_ids[0] and item['answer'] is None), None)
            third_started = any(item['requirement_id'] == requirement_ids[2] and item['role'] == 'worker' and item['delivered'] is not None for item in job['assignments'])
            if missing and third_started and not answered:
                targets[0].write_text(contents[0])
                api('/api/jobs/' + jid + '/operator', {'action': 'answer', 'request_id': missing['id'], 'answer': 'The requested exact input file is now present. Continue the original read-only check.'})
                answered = True
                print('OPERATOR_INPUT_SUPPLIED_AFTER_INDEPENDENT_TASK_STARTED', flush=True)
            if job['state'] == 'complete':
                workers = [item for item in job['assignments'] if item['role'] == 'worker' and item['requirement_id'] == requirement_ids[0]]
                reviewers = [item for item in job['assignments'] if item['role'] == 'reviewer']
                worker_ids = {item['session_id'] for item in job['assignments'] if item['role'] == 'worker'}
                assert peak == 2 and answered
                assert len(workers) >= 2 and workers[0]['session_id'] == workers[1]['session_id'] and workers[1]['reused']
                assert len({item['session_id'] for item in reviewers}) == len(reviewers)
                assert not worker_ids & {item['session_id'] for item in reviewers}
                assert all(item['state'] == 'accepted' for item in job['requirements'])
                assert all(target.read_text() == content for target, content in zip(targets, contents))
                report = {'job_id': jid, 'max_workers': 2, 'peak_active': peak, 'overlap': overlap, 'reused_worker': workers[0]['session_id'],
                          'independent_work_proceeded_while_blocked': True, 'reviewers_independent': True, 'outcome': 'complete'}
                (output / 'result.json').write_text(json.dumps(report, indent=2))
                (output / 'ledger.json').write_text(json.dumps(job, indent=2))
                print('PARALLEL_AND_REUSE_PASSED', json.dumps(report), flush=True)
                return
            if job['state'] == 'blocked_operator' or any(item['answer'] is None and item['requirement_id'] != requirement_ids[0] for item in job['operator_requests']):
                raise RuntimeError('Unexpected operator blocker: ' + json.dumps(job['operator_requests']))
            time.sleep(.25)
        raise TimeoutError('Live parallel verification exceeded ten minutes')
    finally:
        closed = api('/api/jobs/' + jid + '/operator', {'action': 'close'})
        (output / 'closed-ledger.json').write_text(json.dumps(closed, indent=2))
        with store.connect() as db:
            db.execute('DELETE FROM evidence_reads WHERE assignment_id IN (SELECT id FROM assignments WHERE job_id=?)', (jid,))
            for table in ('reviews', 'attempts', 'operator_requests', 'job_events', 'evidence', 'job_sources', 'assignments', 'requirements'):
                db.execute(f'DELETE FROM {table} WHERE job_id=?', (jid,))
            db.execute('DELETE FROM jobs WHERE id=?', (jid,))
        http.close()
        print('FIXTURE_CLOSED_AND_REMOVED', flush=True)


if __name__ == '__main__':
    main()
