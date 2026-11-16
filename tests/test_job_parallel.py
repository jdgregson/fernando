import json
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from src.services.jobs import JobController, JobStore, identifier


MODEL = {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}


class Manager:
    default_on_event = None

    def __init__(self):
        self.sessions = {}
        self.deliveries = []

    def create_session(self, **kwargs):
        sid = identifier()[:8]
        session = SimpleNamespace(id=sid, _is_prompting=False, _turn_finishing=False, _reloading=False,
                                  _retry_pending=False, ready=True, is_loaded=True, history=[])
        session.has_unfinished_turn = lambda: session._is_prompting
        self.sessions[sid] = session
        return sid

    def get_session(self, sid):
        return self.sessions.get(sid)

    def rename_session(self, sid, name):
        self.sessions[sid].name = name

    def list_archived(self):
        return []

    def deliver_scheduled_prompt(self, sid, prompt, initial=False):
        session = self.sessions[sid]
        if session._is_prompting:
            return 'skipped_busy'
        if initial and any(event.get('type') == 'user_prompt' for event in session.history):
            return 'already_started'
        session.history.append({'type': 'user_prompt', 'text': prompt, 'ts': time.time()})
        session._is_prompting = True
        self.deliveries.append({'sid': sid, 'prompt': prompt, 'initial': initial})
        return 'delivered'

    def finish(self, sid):
        session = self.sessions[sid]
        session._is_prompting = False
        session.history.append({'result': {'stopReason': 'end_turn'}, 'ts': time.time()})


def job_fixture(tmp_path, count=6, max_workers=5, reuse_workers=False, dependencies=None):
    store = JobStore(tmp_path / 'jobs.sqlite3')
    scope = '\n'.join('Test item ' + str(i) + '.' for i in range(count))
    job = store.create('Parallel fixture', scope, MODEL, MODEL, max_workers=max_workers, reuse_workers=reuse_workers)
    jid = job['id']
    for role, sid in [('planner', 'planner'), ('plan_reviewer', 'plan-reviewer')]:
        with store.connect() as db:
            db.execute("INSERT INTO assignments(id,job_id,role,session_id,state,created) VALUES(?,?,?,?,'active',?)", (sid, jid, role, sid, time.time()))
        if role == 'planner':
            specs = [{'key': 'R' + str(i), 'source': 'Test item ' + str(i) + '.', 'work': 'Execute this test',
                      'acceptance': 'Recorded observation', 'preconditions': 'Fixture available', 'access': 'Fixture only',
                      'evidence': 'Original tool inputs and outputs', 'controls': 'Fixture control',
                      'dependencies': (dependencies or {}).get(i, [])} for i in range(count)]
            store.agent(sid, 'plan', {'requirements': specs}, [])
        else:
            store.agent(sid, 'review', {'verdict': 'accepted', 'reasons': 'Complete fixture scope'}, [])
    controller = JobController(store)
    controller.manager = Manager()
    return store, controller, jid


def active(store, jid):
    return [item for item in store.get(jid)['assignments'] if item['state'] in ('reserved', 'active')]


def submit(store, controller, assignment, finish=True):
    sid = assignment['session_id']
    event_id = identifier()
    history = controller.manager.sessions[sid].history
    history.append({'event_id': event_id, 'ts': time.time(), 'params': {'update': {
        'sessionUpdate': 'tool_call_update', 'toolCallId': event_id, 'status': 'completed',
        'rawInput': {'command': 'fixture operation'}, 'rawOutput': {'observed': True},
    }}})
    store.agent(sid, 'submit', {'conclusion': 'Fixture observed', 'limitations': 'Fixture only', 'outcome': 'Recorded',
                               'versions': ['fixture-v1'], 'evidence_refs': [event_id]}, history)
    if finish:
        controller.manager.finish(sid)


def review(store, controller, assignment, verdict):
    sid = assignment['session_id']
    attempt = next(item for item in reversed(store.get(assignment['job_id'])['attempts']) if item['requirement_id'] == assignment['requirement_id'])
    for ref in attempt['evidence']:
        offset = 0
        while offset is not None:
            offset = store.agent(sid, 'evidence', {'event_id': ref, 'offset': offset}, [])['next_offset']
    store.agent(sid, 'review', {'verdict': verdict, 'reasons': 'Address this specific failed criterion', 'inspected_evidence': attempt['evidence']}, [])
    controller.manager.finish(sid)


def test_default_five_tasks_start_without_waiting_for_each_other(tmp_path):
    store, controller, jid = job_fixture(tmp_path)
    controller.tick()
    assert len(active(store, jid)) == 5
    assert len(controller.manager.sessions) == 5
    controller.tick()
    assert len(controller.manager.deliveries) == 5
    assert all(session._is_prompting for session in controller.manager.sessions.values())
    controller.tick()
    assert len(controller.manager.sessions) == 5
    assert sum(item['state'] == 'pending' for item in store.get(jid)['requirements']) == 1


def test_dependency_waits_for_acceptance_not_just_worker_submission(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=3, dependencies={2: ['R0']})
    controller.tick()
    controller.tick()
    assert len(active(store, jid)) == 2
    first = active(store, jid)[0]
    submit(store, controller, first)
    controller.tick()
    controller.tick()
    assert store.get(jid)['requirements'][2]['state'] == 'pending'
    reviewer = next(item for item in active(store, jid) if item['role'] == 'reviewer')
    review(store, controller, reviewer, 'accepted')
    controller.tick()
    assert store.get(jid)['requirements'][2]['state'] == 'running'


def test_settling_turn_and_review_keep_their_task_slot(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=3, max_workers=2)
    controller.tick()
    controller.tick()
    first = active(store, jid)[0]
    submit(store, controller, first, finish=False)
    controller.tick()
    assert len(controller.manager.sessions) == 2
    assert store.get(jid)['requirements'][2]['state'] == 'pending'
    controller.manager.finish(first['session_id'])
    controller.tick()
    assert len(active(store, jid)) == 2
    assert any(item['role'] == 'reviewer' for item in active(store, jid))
    assert store.get(jid)['requirements'][2]['state'] == 'pending'


def test_blocked_task_frees_a_slot_for_independent_work(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=3, max_workers=2)
    controller.tick()
    controller.tick()
    first = active(store, jid)[0]
    store.agent(first['session_id'], 'operator', {'request': 'Need fixture input'}, [])
    controller.manager.finish(first['session_id'])
    controller.tick()
    assert len(active(store, jid)) == 2
    assert store.get(jid)['requirements'][0]['state'] == 'blocked_operator'
    assert store.get(jid)['requirements'][2]['state'] == 'running'


def test_worker_reuse_is_same_test_three_uses_and_reviewers_remain_fresh(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=1, reuse_workers=True)
    controller.tick()
    controller.tick()
    original = active(store, jid)[0]['session_id']
    reviewers = set()
    for number in range(3):
        worker = active(store, jid)[0]
        assert worker['session_id'] == original
        assert bool(worker['reused']) == (number > 0)
        with pytest.raises(PermissionError):
            store.agent(original, 'review', {'verdict': 'accepted', 'reasons': 'Self approval'}, [])
        submit(store, controller, worker)
        controller.tick()
        controller.tick()
        reviewer = active(store, jid)[0]
        assert reviewer['role'] == 'reviewer'
        assert reviewer['session_id'] != original and reviewer['session_id'] not in reviewers
        reviewers.add(reviewer['session_id'])
        review(store, controller, reviewer, 'needs_rework')
        controller.tick()
        if number < 2:
            assert controller.manager.deliveries[-1]['sid'] == original
            assert not controller.manager.deliveries[-1]['initial']
            assert 'Address this specific failed criterion' in controller.manager.deliveries[-1]['prompt']
    assert len(store.get(jid)['attempts']) == 3
    assert not active(store, jid)
    request = store.get(jid)['operator_requests'][0]
    store.operator(jid, 'answer', {'request_id': request['id'], 'answer': 'Use a new worker to retry'})
    controller.tick()
    assert active(store, jid)[0]['session_id'] != original


def test_reuse_disabled_keeps_fresh_workers(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=1)
    controller.tick()
    controller.tick()
    first = active(store, jid)[0]
    submit(store, controller, first)
    controller.tick()
    controller.tick()
    review(store, controller, active(store, jid)[0], 'needs_rework')
    controller.tick()
    assert active(store, jid)[0]['session_id'] != first['session_id']


def test_parallel_rework_reuses_each_tests_own_worker(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=2, max_workers=2, reuse_workers=True)
    controller.tick()
    controller.tick()
    workers = active(store, jid)
    identities = {item['requirement_id']: item['session_id'] for item in workers}
    for worker in workers:
        submit(store, controller, worker)
    controller.tick()
    controller.tick()
    for reviewer in active(store, jid):
        review(store, controller, reviewer, 'needs_rework')
    controller.tick()
    assert {item['requirement_id']: item['session_id'] for item in active(store, jid)} == identities
    assert len(set(identities.values())) == 2


def test_reused_dispatch_receipt_prevents_duplicate_prompt_after_restart(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=1, reuse_workers=True)
    controller.tick()
    controller.tick()
    submit(store, controller, active(store, jid)[0])
    controller.tick()
    controller.tick()
    review(store, controller, active(store, jid)[0], 'needs_rework')
    controller.tick()
    assignment = active(store, jid)[0]
    count = len(controller.manager.deliveries)
    with store.connect() as db:
        db.execute("UPDATE assignments SET state='reserved',delivered=NULL WHERE id=?", (assignment['id'],))
    reopened = JobController(JobStore(store.path))
    reopened.manager = controller.manager
    reopened.tick()
    assert len(controller.manager.deliveries) == count
    assert active(store, jid)[0]['state'] == 'active'


def test_migration_preserves_referenced_assignment_history(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=1)
    with store.connect() as db:
        db.execute('DELETE FROM reviews WHERE job_id=?', (jid,))
        db.execute('DELETE FROM assignments WHERE job_id=?', (jid,))
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TABLE assignments')
        db.execute("CREATE TABLE assignments(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),requirement_id TEXT,role TEXT NOT NULL,session_id TEXT UNIQUE,state TEXT NOT NULL DEFAULT 'reserved',prompt TEXT NOT NULL DEFAULT '',created REAL NOT NULL,delivered REAL,finished REAL,continuations INTEGER NOT NULL DEFAULT 0)")
        rid = db.execute('SELECT id FROM requirements WHERE job_id=?', (jid,)).fetchone()[0]
        db.execute("INSERT INTO assignments(rowid,id,job_id,requirement_id,role,session_id,state,created) VALUES(42,'old',?,?,'worker','original-session','finished',1)", (jid, rid))
        db.execute("INSERT INTO attempts(id,job_id,requirement_id,assignment_id,conclusion,limitations,versions,outcome,evidence,created) VALUES('attempt',?,?,'old','Claim','None','[]','Finding','[]',2)", (jid, rid))
        db.execute("INSERT INTO evidence VALUES('event',?,'original-session','{}','hash')", (jid,))
        db.execute("INSERT INTO evidence_reads VALUES('old','event',0,2)")
    reopened = JobStore(store.path)
    with reopened.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute("SELECT rowid FROM assignments WHERE id='old'").fetchone()[0] == 42
        assert db.execute("SELECT assignment_id FROM attempts WHERE id='attempt'").fetchone()[0] == 'old'
        assert db.execute("SELECT count(*) FROM evidence_reads WHERE assignment_id='old'").fetchone()[0] == 1
        db.execute("INSERT INTO assignments(id,job_id,requirement_id,role,session_id,state,created) VALUES('new',?,?,'worker','original-session','active',3)", (jid, rid))
    assert len(reopened.get(jid)['assignments']) == 2
    with pytest.raises(sqlite3.IntegrityError):
        with reopened.connect() as db:
            db.execute("INSERT INTO assignments(id,job_id,role,session_id,state,created) VALUES('duplicate',?,'planner','original-session','active',4)", (jid,))


def test_actual_worker_processes_are_dispatched_concurrently(tmp_path):
    store, controller, jid = job_fixture(tmp_path)
    processes = []
    original = controller.manager.deliver_scheduled_prompt

    def deliver(sid, prompt, initial=False):
        result = original(sid, prompt, initial)
        if result == 'delivered':
            code = "import sys\nimport time\nfrom pathlib import Path\nroot = Path(sys.argv[1])\n(root / sys.argv[2]).write_text('started')\nwhile not (root / 'release').exists():\n    time.sleep(.01)"
            processes.append(subprocess.Popen([sys.executable, '-c', code, str(tmp_path), sid]))
        return result

    controller.manager.deliver_scheduled_prompt = deliver
    try:
        controller.tick()
        controller.tick()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and sum((tmp_path / sid).exists() for sid in controller.manager.sessions) < 5:
            time.sleep(.01)
        assert len(processes) == 5
        assert all((tmp_path / sid).read_text() == 'started' for sid in controller.manager.sessions)
        assert all(process.poll() is None for process in processes)
        controller.tick()
        assert len(processes) == 5
    finally:
        (tmp_path / 'release').write_text('release')
        for process in processes:
            process.wait(timeout=5)


def test_finished_submission_cannot_receive_a_stale_continuation(tmp_path):
    store, controller, jid = job_fixture(tmp_path, count=1)
    controller.tick()
    controller.tick()
    worker = active(store, jid)[0]
    original = controller.manager.get_session
    submitted = False

    def get_session(sid):
        nonlocal submitted
        if sid == worker['session_id'] and not submitted:
            submitted = True
            submit(store, controller, worker)
        return original(sid)

    controller.manager.get_session = get_session
    count = len(controller.manager.deliveries)
    controller.tick()
    assert len(controller.manager.deliveries) == count
    assert store.get(jid)['requirements'][0]['state'] == 'review_pending'


def test_resume_missing_context_does_not_block_other_tasks(tmp_path):
    from unittest.mock import Mock
    from src.services.job_sessions import JobSessions
    store, controller, jid = job_fixture(tmp_path, count=2)
    controller.tick()
    controller.tick()
    assignments = active(store, jid)
    store.operator(jid, 'pause', {})
    controller.manager.sessions.clear()
    controller.manager.restore_session = Mock(side_effect=[False, True])
    controller.manager.reload_session = Mock()
    resumed = JobSessions(store, controller).perform(jid, 'resume', {})
    assert resumed['paused'] == 0
    assert resumed['state'] == 'running'
    assert resumed['requirements'][0]['state'] == 'blocked_operator'
    assert resumed['requirements'][1]['state'] == 'running'
    controller.manager.reload_session.assert_called_once_with(assignments[1]['session_id'])


@pytest.mark.parametrize('options', [{'max_workers': 0}, {'max_workers': -1}, {'max_workers': True}, {'max_workers': 2.5}, {'reuse_workers': 'yes'}])
def test_execution_settings_are_validated(tmp_path, options):
    with pytest.raises(ValueError):
        JobStore(tmp_path / 'jobs.sqlite3').create('Invalid', 'Scope', MODEL, MODEL, **options)
