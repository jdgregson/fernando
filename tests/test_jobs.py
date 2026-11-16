import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from src.services.jobs import JobController, JobStore, guidance


MODEL = {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}


@pytest.fixture
def ledger(tmp_path):
    return JobStore(tmp_path / 'jobs.sqlite3')


def create(ledger):
    return ledger.create('Fixture', 'Test the target. Preserve the original data.', MODEL, MODEL)['id']


def assign(ledger, jid, role, rid=None, sid=None):
    sid = sid or role + str(time.time_ns())
    with ledger.connect() as db:
        db.execute("INSERT INTO assignments(id,job_id,requirement_id,role,session_id,state,created,delivered) VALUES(?,?,?,?,?,'active',?,?)", (sid, jid, rid, role, sid, time.time(), time.time()))
    return sid


def spec(key='R1', source='Test the target.', dependencies=None):
    return {'key': key, 'source': source, 'work': source, 'acceptance': 'Actual target responds as expected', 'preconditions': 'Target available', 'access': 'Fixture only', 'evidence': 'Original invocation and result', 'controls': 'Reachability succeeds and invalid input fails', 'dependencies': dependencies or []}


def plan(ledger, jid, specs=None):
    planner = assign(ledger, jid, 'planner')
    ledger.agent(planner, 'plan', {'requirements': specs or [spec(), spec('R2', 'Preserve the original data.', ['R1'])]}, [])
    reviewer = assign(ledger, jid, 'plan_reviewer')
    ledger.agent(reviewer, 'review', {'verdict': 'accepted', 'reasons': 'Both original obligations covered'}, [])
    return ledger.get(jid)['requirements']


def event(ref='e1', output='Actual observation', status='completed'):
    return {'event_id': ref, 'ts': time.time(), 'params': {'update': {'sessionUpdate': 'tool_call_update', 'toolCallId': 'call1', 'status': status, 'rawOutput': output}}}


def submit(ledger, jid, rid, history=None):
    worker = assign(ledger, jid, 'worker', rid)
    history = history or [event()]
    ledger.agent(worker, 'submit', {'conclusion': 'Performed requested operation', 'limitations': 'Fixture only', 'outcome': 'Defect found', 'versions': ['fixture SHA256 abc'], 'evidence_refs': [item['event_id'] for item in history]}, history)
    return worker


def review(ledger, jid, rid, verdict='accepted', history=None):
    reviewer = assign(ledger, jid, 'reviewer', rid)
    attempt = next(item for item in reversed(ledger.get(jid)['attempts']) if item['requirement_id'] == rid)
    for ref in attempt['evidence']:
        offset = 0
        while offset is not None:
            offset = ledger.agent(reviewer, 'evidence', {'event_id': ref, 'offset': offset}, [])['next_offset']
    ledger.agent(reviewer, 'review', {'verdict': verdict, 'reasons': 'Checked original events against criteria', 'inspected_evidence': attempt['evidence']}, history or [])
    return reviewer


def test_independent_plan_review_required(ledger):
    jid = create(ledger)
    sid = assign(ledger, jid, 'planner')
    ledger.agent(sid, 'plan', {'requirements': [spec()]}, [])
    assert ledger.get(jid)['requirements'] == []
    with pytest.raises(PermissionError):
        ledger.agent(sid, 'review', {'verdict': 'accepted', 'reasons': 'I finished'}, [])
    assert ledger.get(jid)['state'] == 'plan_review'


@pytest.mark.parametrize('change', [lambda value: value.update(source='Invented obligation'), lambda value: value.update(dependencies=['missing']), lambda value: value.update(dependencies=['R1']), lambda value: value.pop('controls')])
def test_invalid_decomposition_rejected(ledger, change):
    jid = create(ledger)
    sid = assign(ledger, jid, 'planner')
    value = spec()
    change(value)
    with pytest.raises(ValueError):
        ledger.agent(sid, 'plan', {'requirements': [value]}, [])
    assert ledger.get(jid)['state'] == 'planning'


def test_worker_cannot_review_or_waive(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    sid = assign(ledger, jid, 'worker', rid)
    with pytest.raises(PermissionError):
        ledger.agent(sid, 'review', {'verdict': 'accepted', 'reasons': 'Finished'}, [])
    with pytest.raises(ValueError):
        ledger.agent(sid, 'waive', {'reason': 'Unnecessary'}, [])
    assert ledger.get(jid)['state'] != 'complete'


def test_submission_requires_real_own_tool_events(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    worker = assign(ledger, jid, 'worker', rid)
    for refs in ([], ['foreign'], ['message']):
        with pytest.raises(ValueError):
            ledger.agent(worker, 'submit', {'conclusion': 'Done', 'limitations': 'None', 'outcome': 'Pass', 'versions': ['v1'], 'evidence_refs': refs}, [{'event_id': 'message', 'type': 'agent_message'}])
    assert ledger.get(jid)['attempts'] == []


def test_evidence_immutable_and_paginated(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    observation = event(output='x' * 40000)
    submit(ledger, jid, rid, [observation])
    reviewer = assign(ledger, jid, 'reviewer', rid)
    observation['params']['update']['rawOutput'] = 'Changed'
    raw = ''
    offset = 0
    while offset is not None:
        page = ledger.agent(reviewer, 'evidence', {'event_id': 'e1', 'offset': offset}, [])
        raw += page['text']
        offset = page['next_offset']
    assert len(json.loads(raw)['params']['update']['rawOutput']) == 40000
    assert hashlib.sha256(raw.encode()).hexdigest() == page['sha256']


def test_review_requires_actual_full_evidence_read(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    submit(ledger, jid, rid, [event(output='x' * 20000)])
    reviewer = assign(ledger, jid, 'reviewer', rid)
    data = {'verdict': 'accepted', 'reasons': 'Fine', 'inspected_evidence': ['e1']}
    with pytest.raises(ValueError, match='Read every page'):
        ledger.agent(reviewer, 'review', data, [])
    ledger.agent(reviewer, 'evidence', {'event_id': 'e1'}, [])
    with pytest.raises(ValueError, match='Read every page'):
        ledger.agent(reviewer, 'review', data, [])
    ledger.agent(reviewer, 'evidence', {'event_id': 'e1', 'offset': 16000}, [])
    ledger.agent(reviewer, 'review', data, [])
    assert ledger.get(jid)['requirements'][0]['state'] == 'accepted'


def test_cross_job_evidence_denied(ledger):
    first = create(ledger)
    rid = plan(ledger, first)[0]['id']
    submit(ledger, first, rid)
    second = create(ledger)
    reader = assign(ledger, second, 'planner')
    with pytest.raises(ValueError, match='not found'):
        ledger.agent(reader, 'evidence', {'event_id': 'e1'}, [])


def test_acceptance_is_not_security_outcome(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    submit(ledger, jid, rid)
    review(ledger, jid, rid)
    job = ledger.get(jid)
    assert job['requirements'][0]['state'] == 'accepted'
    assert job['attempts'][0]['outcome'] == 'Defect found'
    assert job['state'] != 'complete'


class Manager:
    default_on_event = None

    def __init__(self):
        self.sessions = {}
        self.deliveries = []

    def create_session(self, **kwargs):
        sid = str(len(self.sessions))
        self.sessions[sid] = SimpleNamespace(id=sid, _is_prompting=False, _turn_finishing=False, _reloading=False, _retry_pending=False, ready=True, is_loaded=True, history=[], has_unfinished_turn=lambda: False)
        return sid

    def rename_session(self, sid, name):
        pass

    def get_session(self, sid):
        return self.sessions.get(sid)

    def deliver_scheduled_prompt(self, sid, prompt, initial=False):
        self.deliveries.append((sid, prompt))
        return 'delivered'


def test_rejected_attempt_dispatches_fresh_worker(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    submit(ledger, jid, rid)
    review(ledger, jid, rid, 'needs_rework')
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    job = ledger.get(jid)
    assert job['requirements'][0]['state'] == 'running'
    assert job['assignments'][-1]['role'] == 'worker'
    controller.tick()
    assert 'Checked original events' in controller.manager.deliveries[0][1]


def test_only_all_accepted_or_user_waived_complete(ledger):
    jid = create(ledger)
    first, second = plan(ledger, jid)
    submit(ledger, jid, first['id'])
    review(ledger, jid, first['id'])
    ledger.operator(jid, 'waive', {'requirement_id': second['id'], 'reason': 'User explicitly drops this obligation'})
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    assert ledger.get(jid)['state'] == 'complete'
    assert ledger.get(jid)['requirements'][1]['waiver']


def test_restart_preserves_unfinished_rework_and_evidence(ledger):
    jid = create(ledger)
    first = plan(ledger, jid)[0]
    submit(ledger, jid, first['id'])
    review(ledger, jid, first['id'], 'needs_rework')
    reopened = JobStore(ledger.path)
    assert reopened.get(jid) == ledger.get(jid)
    controller = JobController(reopened)
    controller.manager = Manager()
    controller.tick()
    assert reopened.get(jid)['assignments'][-1]['role'] == 'worker'


def test_blocked_work_does_not_block_independent_requirement(ledger):
    jid = create(ledger)
    first, second = plan(ledger, jid, [spec(), spec('R2', 'Preserve the original data.')])
    worker = assign(ledger, jid, 'worker', first['id'])
    ledger.agent(worker, 'operator', {'request': 'Need target access'}, [])
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    assert ledger.get(jid)['assignments'][-1]['requirement_id'] == second['id']


def test_final_message_cannot_complete_and_continuations_are_bounded(ledger):
    jid = create(ledger)
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    controller.tick()
    session = controller.manager.sessions['0']
    for _ in range(3):
        session.history.append({'result': {'stopReason': 'end_turn'}, 'ts': time.time() + 1})
        controller.tick()
    job = ledger.get(jid)
    assert job['state'] == 'blocked_operator'
    assert len(controller.manager.deliveries) == 3
    assert job['operator_requests'][0]['answer'] is None


def test_three_rejections_escalate(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    for number in range(3):
        submit(ledger, jid, rid, [event('event' + str(number))])
        review(ledger, jid, rid, 'needs_rework')
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    job = ledger.get(jid)
    assert job['requirements'][0]['state'] == 'blocked_operator'
    assert len(job['operator_requests']) == 1
    ledger.operator(jid, 'answer', {'request_id': job['operator_requests'][0]['id'], 'answer': 'Use the corrected target address'})
    controller.tick()
    assert ledger.get(jid)['assignments'][-1]['role'] == 'worker'


def test_pause_prevents_dispatch(ledger):
    jid = create(ledger)
    ledger.operator(jid, 'pause', {})
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    assert not controller.manager.sessions
    ledger.operator(jid, 'resume', {})
    controller.tick()
    assert len(controller.manager.sessions) == 1


def test_review_guidance_covers_methodology_and_scope(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    job = ledger.get(jid)
    prompt = guidance(job, {'role': 'reviewer', 'requirement_id': rid})
    for phrase in ('redirect', 'printed success', 'short-circuiting', 'algorithm rejection', 'without inventing scope', 'nonce validation'):
        assert phrase in prompt


def test_submission_includes_unselected_invocation_context(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    worker = assign(ledger, jid, 'worker', rid)
    invocation = event('input')
    invocation['params']['update'] = {'sessionUpdate': 'tool_call', 'toolCallId': 'call1', 'rawInput': {'command': 'false && important-command'}}
    output = event('output', 'No command output')
    ledger.agent(worker, 'submit', {'conclusion': 'Executed', 'limitations': 'None', 'outcome': 'Pass', 'versions': ['fixture1'], 'evidence_refs': ['output']}, [invocation, output])
    assert ledger.get(jid)['attempts'][0]['evidence'] == ['input', 'output']


def test_cancelled_assignment_requires_operator_decision(ledger):
    jid = create(ledger)
    controller = JobController(ledger)
    controller.manager = Manager()
    controller.tick()
    controller.tick()
    controller.manager.sessions['0'].history.append({'type': 'prompt_cancelled', 'ts': time.time() + 1})
    controller.tick()
    assert ledger.get(jid)['state'] == 'blocked_operator'
    assert len(controller.manager.deliveries) == 1


def test_controller_waits_for_submission_turn_to_finish(ledger):
    jid = create(ledger)
    rid = plan(ledger, jid)[0]['id']
    worker = submit(ledger, jid, rid)
    controller = JobController(ledger)
    controller.manager = Manager()
    previous = SimpleNamespace(id=worker, _is_prompting=True, _turn_finishing=False, history=[])
    controller.manager.sessions[worker] = previous
    controller.tick()
    assert len(controller.manager.sessions) == 1
    previous._is_prompting = False
    controller.tick()
    assert ledger.get(jid)['assignments'][-1]['role'] == 'reviewer'
