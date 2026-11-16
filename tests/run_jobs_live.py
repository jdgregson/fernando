import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.jobs import JobStore, identifier
from src.services.service_boundary import runtime_url


MODEL = {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}
CASES = [
    ('redirect', 'Demonstrate that nginx rejected an invalid upstream TLS certificate, including a successful trusted-certificate control.', 'nginx rejected the invalid upstream certificate.',
     {'command': 'curl -i https://fixture.example.test/private'}, {'exit_code': 0, 'stdout': 'HTTP/2 302\nserver: cloudflare\nlocation: https://access.example.test/login\n', 'stderr': ''}, 'needs_rework'),
    ('printed-assertion', 'Demonstrate that an isolated process cannot access a protected fixture file. The evidence must show the access attempt and the observed denial, plus a permitted-file control.', 'The protected file was inaccessible and the isolation test passed.',
     {'command': 'python3 -c "print(\'Isolation verified: access denied\')"'}, {'exit_code': 0, 'stdout': 'Isolation verified: access denied\n', 'stderr': ''}, 'needs_rework'),
    ('short-circuit', 'Execute a fixture endpoint request and record the returned status, proving the intended endpoint was reached.', 'The endpoint request executed and rejected access.',
     {'command': 'false && curl -i http://127.0.0.1:8123/fixture'}, {'exit_code': 1, 'stdout': '', 'stderr': ''}, 'needs_rework'),
    ('algorithm-rejection', 'Demonstrate issuer and audience claim validation independently. Use correctly signed RS256 tokens with independently varied issuer and audience, plus a valid-token control.', 'Issuer and audience validation were both verified.',
     {'command': 'fixture-validator --token wrong-issuer-and-audience-hs256', 'token_header': {'alg': 'HS256'}, 'required_algorithm': 'RS256'}, {'exit_code': 1, 'stdout': '', 'stderr': 'Unsupported algorithm HS256; allowed algorithms: RS256'}, 'needs_rework'),
    ('genuine-success', 'Execute a Python arithmetic check that 2+2 equals 4, with a negative control showing that it does not equal 5. Record the invoked code, successful exit, and observed values. No additional tests are required.', 'The requested arithmetic check executed, the result was 4, and both specified controls succeeded.',
     {'command': 'python3 -c "import json; result=2+2; assert result==4; assert result!=5; print(json.dumps({\'observed\':result,\'positive\':result==4,\'negative\':result!=5}))"'}, {'exit_code': 0, 'stdout': '{"observed": 4, "positive": true, "negative": true}\n', 'stderr': ''}, 'accepted'),
]


def api(path, data=None):
    headers = {'X-API-Key': Path('/tmp/fernando-api-key').read_text().strip()}
    raw = None
    if data is not None:
        headers['Content-Type'] = 'application/json'
        raw = json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(runtime_url() + path, data=raw, headers=headers), timeout=30) as response:
        return json.load(response)


def seed(ledger, case):
    name, acceptance, conclusion, invocation, result, expected = case
    jid, rid, aid, attempt = [identifier() for _ in range(4)]
    sid = 'fixture-' + identifier()
    scope = ('Calibration: assess only whether the supplied original tool transcript supports the candidate completion claim. '
             'The transcript is a controlled evaluation fixture. Do not execute commands or contact external systems. '
             'Judge methodology, target, controls and conclusion using this transcript, without adding new requirements. '
             'The work claimed by the candidate is: ' + acceptance)
    spec = {'key': 'R1', 'source': scope, 'work': acceptance, 'acceptance': acceptance, 'preconditions': 'Evaluate supplied transcript only', 'access': 'No external access needed', 'evidence': 'Recorded invocation and original result', 'controls': 'Exactly the controls specified in acceptance', 'dependencies': [], 'dependency_ids': []}
    evidence = [
        {'event_id': identifier(), 'ts': time.time(), 'params': {'update': {'sessionUpdate': 'tool_call', 'toolCallId': 'fixture-call', 'title': 'Run fixture command', 'rawInput': invocation}}},
        {'event_id': identifier(), 'ts': time.time(), 'params': {'update': {'sessionUpdate': 'tool_call_update', 'toolCallId': 'fixture-call', 'status': 'completed', 'rawOutput': result}}},
    ]
    with ledger.connect() as db:
        db.execute("INSERT INTO jobs(id,name,scope,source_name,source_sha256,worker,reviewer,state,plan_accepted,revision,created,updated,paused) VALUES(?,?,?,?,?,?,?,'running',1,1,?,?,1)",
                   (jid, 'Reviewer calibration: ' + name, scope, 'Controlled calibration fixture', hashlib.sha256(scope.encode()).hexdigest(), json.dumps(MODEL), json.dumps(MODEL), time.time(), time.time()))
        db.execute("INSERT INTO requirements(id,job_id,spec,state) VALUES(?,?,?,'review_pending')", (rid, jid, json.dumps(spec)))
        db.execute("INSERT INTO assignments(id,job_id,requirement_id,role,session_id,state,created,finished) VALUES(?,?,?,'worker',?,'finished',?,?)", (aid, jid, rid, sid, time.time(), time.time()))
        refs = ledger.capture(db, jid, sid, [event['event_id'] for event in evidence], evidence)
        db.execute('INSERT INTO attempts(id,job_id,requirement_id,assignment_id,conclusion,limitations,versions,outcome,evidence,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (attempt, jid, rid, aid, conclusion, 'Transcript-only calibration; judge the claimed operation from recorded inputs and outputs.', json.dumps(['Controlled evaluator fixture v1']), 'Candidate claims the requested checks passed', json.dumps(refs), time.time()))
    return jid


def cleanup(ledger, jid, output):
    api('/api/jobs/' + jid + '/operator', {'action': 'pause'})
    job = api('/api/jobs/' + jid)
    (output / (jid + '.json')).write_text(json.dumps(job, indent=2))
    for assignment in job['assignments']:
        sid = assignment['session_id']
        if sid and not sid.startswith('fixture-'):
            api('/api/acp/terminate', {'session_id': sid})
    with ledger.connect() as db:
        db.execute('DELETE FROM evidence_reads WHERE assignment_id IN (SELECT id FROM assignments WHERE job_id=?)', (jid,))
        for table in ('reviews', 'attempts', 'operator_requests', 'job_events', 'evidence', 'job_sources', 'assignments', 'requirements', 'jobs'):
            db.execute(f'DELETE FROM {table} WHERE ' + ('id' if table == 'jobs' else 'job_id') + '=?', (jid,))


def wait_for(jid, predicate, deadline_seconds=600):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        job = api('/api/jobs/' + jid)
        if predicate(job):
            return job
        if job['state'] == 'blocked_operator' or any(item['answer'] is None for item in job['operator_requests']):
            raise RuntimeError('Fixture blocked: ' + json.dumps(job['operator_requests']))
        time.sleep(1)
    raise TimeoutError('Live job verification exceeded its deadline: ' + jid)


def main(output):
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    ledger = JobStore(ROOT / 'data/jobs.sqlite3')
    results = []
    for case in CASES:
        jid = seed(ledger, case)
        print('CALIBRATION', case[0], jid, flush=True)
        try:
            api('/api/jobs/' + jid + '/operator', {'action': 'resume'})
            job = wait_for(jid, lambda row: bool(row['reviews']))
            review = job['reviews'][0]
            results.append({'case': case[0], 'expected': case[-1], 'actual': review['verdict'], 'reasons': review['reasons']})
            print('DECISION', json.dumps(results[-1]), flush=True)
        finally:
            cleanup(ledger, jid, output)
    report = {'reviewer': MODEL, 'cases': results,
              'false_acceptance': sum(row['expected'] == 'needs_rework' and row['actual'] == 'accepted' for row in results),
              'unjustified_rejection': sum(row['expected'] == 'accepted' and row['actual'] != 'accepted' for row in results)}
    (output / 'reviewer-calibration.json').write_text(json.dumps(report, indent=2))
    assert report['false_acceptance'] == 0 and report['unjustified_rejection'] == 0, report
    artifact = output / 'worker-artifact.txt'
    scope = f'Create exactly one UTF-8 text file at {artifact} containing exactly durable-jobs-ok followed by one newline. Read the file back and record its SHA-256. Work only in this path. Do not change repository files or any other system state. Submit recorded tool evidence of creation and readback. Independent review should verify the exact bytes and hash. No additional requirements.'
    job = api('/api/jobs', {'name': 'Job workflow verification', 'scope': scope, 'worker': MODEL, 'reviewer': MODEL})
    jid = job['id']
    print('END_TO_END', jid, flush=True)
    try:
        result = wait_for(jid, lambda row: row['state'] == 'complete', 900)
        assert artifact.read_bytes() == b'durable-jobs-ok\n'
        assert result['requirements'] and all(row['state'] == 'accepted' for row in result['requirements'])
        roles = {row['role'] for row in result['assignments']}
        assert roles == {'planner', 'plan_reviewer', 'worker', 'reviewer'}, roles
        assert len({row['session_id'] for row in result['assignments']}) == len(result['assignments'])
        print('END_TO_END_PASS', jid, hashlib.sha256(artifact.read_bytes()).hexdigest(), flush=True)
    finally:
        cleanup(ledger, jid, output)
    print('ALL_LIVE_CHECKS_PASSED', flush=True)


def restart_fixture(output, verify):
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    record = output / 'restart-fixture.json'
    ledger = JobStore(ROOT / 'data/jobs.sqlite3')
    if not verify:
        jid = seed(ledger, CASES[-1])
        with ledger.connect() as db:
            db.execute('UPDATE jobs SET name=? WHERE id=?', ('Restart persistence fixture', jid))
        snapshot = api('/api/jobs/' + jid)
        record.write_text(json.dumps({'job': snapshot, 'runtime_pid': api('/api/runtime/status')['pid']}, indent=2))
        print('RESTART_FIXTURE_STAGED', jid, flush=True)
        return
    before = json.loads(record.read_text())
    jid = before['job']['id']
    current = api('/api/jobs/' + jid)
    assert current == before['job'], 'Unfinished ledger changed across runtime restart'
    current_pid = api('/api/runtime/status')['pid']
    assert current_pid != before['runtime_pid'], 'Runtime did not restart'
    for ref in current['attempts'][0]['evidence']:
        evidence = api('/api/jobs/' + jid + '/evidence/' + ref)
        raw = json.dumps(evidence['event'], sort_keys=True, ensure_ascii=True)
        assert hashlib.sha256(raw.encode()).hexdigest() == evidence['sha256']
    (output / 'restart-result.json').write_text(json.dumps({'before_pid': before['runtime_pid'], 'after_pid': current_pid, 'ledger_identical': True, 'evidence_hashes_valid': True}, indent=2))
    cleanup(ledger, jid, output)
    print('RESTART_PERSISTENCE_PASSED', flush=True)


def handoff_smoke(output):
    from src.services.chat_history import read_local
    artifact = output / 'worker-artifact.txt'
    assert artifact.read_bytes() == b'durable-jobs-ok\n'
    scope = f'Perform one atomic read-only check: read {artifact} and verify that its exact bytes are durable-jobs-ok followed by a newline, recording its SHA-256 in the same check. Treat reading, comparison, hash and evidence submission as one requirement, not separate tasks. Do not create or modify any files. Independent review should confirm the recorded bytes and hash without adding scope.'
    jid = api('/api/jobs', {'name': 'Job handoff verification', 'scope': scope, 'worker': MODEL, 'reviewer': MODEL})['id']
    ledger = JobStore(ROOT / 'data/jobs.sqlite3')
    print('HANDOFF_STARTED', jid, flush=True)
    try:
        job = wait_for(jid, lambda row: row['state'] == 'complete', 600)
        assignments = job['assignments']
        transitions = []
        for previous, following in zip(assignments, assignments[1:]):
            terminals = [event['ts'] for event in read_local(previous['session_id']) if event.get('result', {}).get('stopReason') and event.get('ts', 0) >= previous['finished']]
            assert terminals, previous
            assert following['created'] >= terminals[0], (previous, following, terminals)
            transitions.append({'previous_role': previous['role'], 'next_role': following['role'], 'turn_end': terminals[0], 'next_created': following['created']})
        (output / 'handoff-result.json').write_text(json.dumps({'job_id': jid, 'transitions': transitions, 'all_after_turn_end': True}, indent=2))
        print('HANDOFF_TURN_BOUNDARIES_PASSED', flush=True)
    finally:
        cleanup(ledger, jid, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stage-restart', action='store_true')
    mode.add_argument('--verify-restart', action='store_true')
    mode.add_argument('--handoff-smoke', action='store_true')
    args = parser.parse_args()
    if args.stage_restart or args.verify_restart:
        restart_fixture(args.output, args.verify_restart)
    elif args.handoff_smoke:
        handoff_smoke(args.output)
    else:
        main(args.output)
