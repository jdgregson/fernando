from unittest.mock import Mock

import pytest

from src.services import groups
from src.services.jobs import JobController, JobStore, identifier
from src.services.job_sessions import JobSessions


MODEL = {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}


@pytest.fixture
def integration(tmp_path, monkeypatch):
    monkeypatch.setattr(groups, 'GROUPS_FILE', str(tmp_path / 'groups.json'))
    ledger = JobStore(tmp_path / 'jobs.sqlite3')
    controller = JobController(ledger)
    controller.manager = Mock(default_on_event=None)
    controller.manager.create_session.return_value = 'fixture-agent'
    controller.manager.get_session.return_value = None
    service = JobSessions(ledger, controller)
    return service, ledger, controller


def test_draft_is_a_persisted_session_without_execution(integration):
    service, ledger, controller = integration
    group = groups.create_group('Project')
    job = service.create_draft(MODEL, MODEL, group['id'])
    assert groups.get_session_groups()['job:' + job['id']] == group['id']
    assert ledger.list_sessions()[0]['id'] == job['id']
    assert job['state'] == 'draft'
    controller.tick()
    controller.manager.create_session.assert_not_called()
    values = {**job['draft'], 'name': 'Draft name', 'scope': 'Preserve this exact scope.'}
    saved = service.save_draft(job['id'], values, 0)
    reopened = JobStore(ledger.path).get(job['id'])
    assert reopened['draft'] == values
    assert saved['draft_revision'] == 1
    assert reopened['state'] == 'draft'


def test_stale_draft_edits_cannot_overwrite_another_view(integration):
    service, ledger, controller = integration
    job = service.create_draft(MODEL, MODEL)
    first = {**job['draft'], 'name': 'First view', 'scope': 'First scope'}
    service.save_draft(job['id'], first, 0)
    with pytest.raises(ValueError, match='another view'):
        service.save_draft(job['id'], {**first, 'scope': 'Second view'}, 0)
    assert ledger.get(job['id'])['draft'] == first


def test_submit_starts_the_existing_draft_once(integration):
    service, ledger, controller = integration
    job = service.create_draft(MODEL, MODEL)
    values = {**job['draft'], 'name': 'Start me', 'scope': 'Do this work'}
    started = service.save_draft(job['id'], values, 0, submit=True)
    assert started['id'] == job['id']
    assert started['state'] == 'planning' and started['paused'] == 0
    with pytest.raises(ValueError, match='not an editable draft'):
        service.save_draft(job['id'], values, 0, submit=True)
    assert len(ledger.list()) == 1


def test_close_pauses_dispatch_and_reopen_requires_explicit_resume(integration):
    service, ledger, controller = integration
    job = service.create(name='Running Job', scope='Do this work', worker=MODEL, reviewer=MODEL)
    closed = service.perform(job['id'], 'close', {})
    assert closed['state'] == job['state'] and closed['paused'] == 1
    assert closed['scope'] == job['scope']
    assert ledger.list_sessions() == []
    assert ledger.list()[0]['id'] == job['id']
    controller.tick()
    controller.manager.create_session.assert_not_called()
    opened = service.perform(job['id'], 'open', {})
    assert opened['view_open'] == 1
    assert len(opened['assignments']) == 0
    assert opened['paused'] == 1
    controller.manager.archive_session.assert_not_called()
    service.perform(job['id'], 'resume', {})
    controller.tick()
    controller.manager.create_session.assert_called_once()


def test_close_stops_real_owned_processes_and_preserves_unrelated_session(integration, tmp_path, monkeypatch):
    import subprocess
    import sys
    import threading
    from src.services import acp
    service, ledger, controller = integration
    monkeypatch.setattr(acp, 'ARCHIVED_FILE', str(tmp_path / 'archived.json'))
    monkeypatch.setattr(acp, 'LINEAGE_FILE', str(tmp_path / 'lineage.json'))
    manager = acp.ACPManager.__new__(acp.ACPManager)
    manager._lock = threading.Lock()
    manager.sessions = {}
    manager._save = Mock()
    controller.manager = manager
    job = service.create(name='Owned processes', scope='Fixture only', worker=MODEL, reviewer=MODEL)
    processes = []
    try:
        for sid in ('11111111', '22222222', '33333333', '44444444'):
            session = acp.ACPSession(sid)
            session._save_history = Mock()
            session.proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            processes.append(session.proc)
            manager.sessions[sid] = session
        acp.set_parent('33333333', '11111111', kind='subagent')
        with ledger.connect() as db:
            for sid, state in (('11111111', 'active'), ('22222222', 'finished')):
                db.execute('INSERT INTO assignments(id,job_id,role,session_id,state,created) VALUES(?,?,?,?,?,0)', (sid, job['id'], 'worker', sid, state))
        result = service.perform(job['id'], 'close', {})
        assert result['paused'] == 1 and result['view_open'] == 0
        assert all(process.poll() is not None for process in processes[:3])
        assert processes[3].poll() is None
        assert set(manager.sessions) == {'44444444'}
        archived = acp._load_archived_map()
        assert set(archived) == {'11111111', '22222222', '33333333'}
        assert all(info['job_id'] == job['id'] for info in archived.values())
        assert ledger.get(job['id'])['assignments'][0]['state'] == 'active'
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_resume_restores_the_interrupted_agent_only(integration):
    from types import SimpleNamespace
    service, ledger, controller = integration
    job = service.create(name='Resume', scope='Fixture only', worker=MODEL, reviewer=MODEL)
    with ledger.connect() as db:
        db.execute("INSERT INTO assignments(id,job_id,role,session_id,state,created) VALUES('assignment',?,'planner','11111111','active',0)", (job['id'],))
    live = {'11111111': SimpleNamespace(display_name='Planner')}
    controller.manager.get_session.side_effect = live.get
    controller.manager.archive_session.side_effect = lambda sid, **kwargs: live.pop(sid)
    service.perform(job['id'], 'close', {})
    controller.manager.archive_session.assert_called_once()
    with pytest.raises(ValueError, match='Reopen'):
        service.perform(job['id'], 'resume', {})
    service.perform(job['id'], 'open', {})
    controller.manager.restore_session.assert_not_called()
    controller.manager.restore_session.return_value = True
    service.perform(job['id'], 'resume', {})
    controller.manager.restore_session.assert_called_once_with('11111111', on_event=None, sleeping=True)
    controller.manager.reload_session.assert_called_once_with('11111111')


def test_rootless_subagents_have_explicit_origin_without_a_fake_parent(tmp_path, monkeypatch):
    from src.services import acp
    monkeypatch.setattr(acp, 'LINEAGE_FILE', str(tmp_path / 'lineage.json'))
    acp.set_parent('11111111', None, kind='subagent')
    lineage = acp._load_lineage()
    assert set(lineage) == {'11111111'}
    assert lineage['11111111']['kind'] == 'subagent'
    assert lineage['11111111']['parent'] is None


def test_rename_and_group_move_preserve_work_and_existing_agent(integration):
    service, ledger, controller = integration
    first, second = groups.create_group('First'), groups.create_group('Second')
    job = service.create(name='Job', scope='Do this work', worker=MODEL, reviewer=MODEL, group_id=first['id'])
    controller.tick()
    previous = ledger.get(job['id'])['assignments']
    service.perform(job['id'], 'rename', {'name': 'Renamed'})
    service.move(job['id'], second['id'])
    moved = ledger.get(job['id'])
    assert moved['name'] == 'Renamed'
    assert moved['assignments'] == previous
    assert moved['group_id'] == second['id']
    assert groups.get_session_groups()['job:' + job['id']] == second['id']
    controller.manager.archive_session.assert_not_called()
    controller.manager.reload_session.assert_not_called()
    service.move(job['id'], None)
    assert ledger.get(job['id'])['group_id'] is None
    assert 'job:' + job['id'] not in groups.get_session_groups()


def test_group_delete_retains_job_and_clears_dispatch_context(integration):
    service, ledger, controller = integration
    group = groups.create_group('Delete group only')
    job = service.create(name='Retain me', scope='Do this work', worker=MODEL, reviewer=MODEL, group_id=group['id'])
    groups.delete_group(group['id'])
    service.synchronize_groups()
    assert ledger.get(job['id'])['group_id'] is None
    controller.tick()
    assert controller.manager.create_session.call_args.kwargs['group_id'] is None


def test_group_archive_waits_for_context_without_job_archival(integration):
    service, ledger, controller = integration
    group = groups.create_group('Project')
    job = service.create(name='Job', scope='Do this work', worker=MODEL, reviewer=MODEL, group_id=group['id'])
    groups.archive_group(group['id'], set())
    service.synchronize_groups()
    controller.tick()
    waiting = ledger.get(job['id'])
    assert waiting['state'] == 'planning' and waiting['paused'] == 0
    assert waiting['group_suspended']
    controller.manager.create_session.assert_not_called()
    groups.restore_group(group['id'])
    service.synchronize_groups()
    controller.tick()
    assert not ledger.get(job['id'])['group_suspended']
    controller.manager.create_session.assert_called_once()


def test_draft_original_upload_is_retained_and_not_reassigned(integration):
    service, ledger, controller = integration
    job = service.create_draft(MODEL, MODEL)
    source = identifier()
    with ledger.connect() as db:
        db.execute('INSERT INTO job_sources VALUES(?,NULL,?,?,?,?)', (source, 'scope.md', 'hash', 'Original scope', b'Original scope'))
    values = {**job['draft'], 'name': 'Uploaded', 'scope': 'Original scope', 'source_id': source, 'source_name': 'scope.md'}
    saved = service.save_draft(job['id'], values, 0)
    assert saved['uploaded_source']['filename'] == 'scope.md'
    other = service.create_draft(MODEL, MODEL)
    with pytest.raises(ValueError, match='not available'):
        service.save_draft(other['id'], {**values, 'name': 'Other'}, 0)
    assert ledger.get(job['id'])['uploaded_source']['filename'] == 'scope.md'


@pytest.mark.parametrize('action', ['archive', 'restore', 'delete', 'fork', 'sleep', 'wake'])
def test_acp_chat_actions_are_not_job_session_actions(integration, action):
    service, ledger, controller = integration
    job = service.create_draft(MODEL, MODEL)
    with pytest.raises(ValueError, match='Unknown operator action'):
        service.perform(job['id'], action, {})
    assert ledger.get(job['id'])['state'] == 'draft'
