import io
import json
import os
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, request

from src.routes import jobs as routes
from src.services.jobs import JobStore, JobController, JobHandler, JobServer
from src.services.job_sessions import JobSessions


MODEL = {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    ledger = JobStore(tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(routes, 'store', ledger)
    monkeypatch.setattr(routes, 'session_service', JobSessions(ledger, JobController(ledger)))
    from src.services import groups
    monkeypatch.setattr(groups, 'GROUPS_FILE', str(tmp_path / 'groups.json'))
    monkeypatch.setattr(routes, '_check_api_key', lambda: (request.headers.get('X-API-Key') or request.args.get('api_key')) == 'fixture-key')
    monkeypatch.setattr(routes, 'get_provider_settings', lambda: {'providers': [{'id': 'kiro', 'name': 'Kiro', 'model': MODEL['model'], 'enabled': True}]})
    app = Flask(__name__, template_folder=str(Path('src/templates').resolve()))
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    return app.test_client(), ledger


def test_api_authentication(client):
    browser, ledger = client
    for url in ('/api/jobs', '/jobs/new', '/api/jobs/unknown', '/api/jobs/unknown/evidence/event'):
        assert browser.get(url).status_code == 401
    assert browser.post('/api/jobs?api_key=fixture-key', json={}).status_code == 401
    assert browser.post('/api/jobs/document?api_key=fixture-key').status_code == 401
    assert browser.post('/api/jobs/unknown/operator', json={}).status_code == 401
    assert ledger.list() == []


def test_upload_retains_exact_original_and_reviewed_scope(client):
    browser, ledger = client
    headers = {'X-API-Key': 'fixture-key'}
    raw = b'# Scope\n\nPreserve every obligation.\n'
    response = browser.post('/api/jobs/document', data={'document': (io.BytesIO(raw), 'scope.md')}, headers=headers)
    assert response.status_code == 200
    document = response.json
    response = browser.post('/api/jobs', json={'name': 'Uploaded', 'scope': document['scope'], 'source_id': document['source_id'], 'worker': MODEL, 'reviewer': MODEL}, headers=headers)
    assert response.status_code == 201
    job = response.json
    assert job['uploaded_source']['filename'] == 'scope.md'
    assert browser.get('/api/jobs/' + job['id'] + '/source', headers=headers).data == raw
    assert job['state'] == 'planning'
    assert browser.get('/api/jobs/' + job['id'], headers=headers).json['scope'] == document['scope']


def test_document_failures_and_docx_runs(client):
    from docx import Document
    browser, ledger = client
    headers = {'X-API-Key': 'fixture-key'}
    for name, raw in [('broken.docx', b'not a zip'), ('bad.txt', b'\xff'), ('image.png', b'PNG')]:
        assert browser.post('/api/jobs/document', data={'document': (io.BytesIO(raw), name)}, headers=headers).status_code == 400
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run('Pre')
    paragraph.add_run('serve data.').bold = True
    document.add_table(rows=1, cols=1).cell(0, 0).text = 'Check integrity.'
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    response = browser.post('/api/jobs/document', data={'document': (output, 'scope.docx')}, headers=headers)
    assert response.json['scope'] == 'Preserve data.\nCheck integrity.'


def test_no_new_styles_in_job_template(client):
    browser, ledger = client
    page = browser.get('/jobs/new?api_key=fixture-key')
    assert page.status_code == 200
    assert b'/static/css/index.css' in page.data
    assert b'<style' not in page.data
    assert b'style=' not in page.data


def test_ipc_derives_role_from_process_not_arguments(tmp_path):
    ledger = JobStore(tmp_path / 'jobs.sqlite3')
    job = ledger.create('Fixture', 'Do work', MODEL, MODEL)
    with ledger.connect() as db:
        db.execute("INSERT INTO assignments(id,job_id,role,session_id,state,created) VALUES('assignment',?,'worker','worker-session','active',0)", (job['id'],))
    controller = JobController(ledger)
    session = SimpleNamespace(id='worker-session', proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), history=[])
    controller.manager = SimpleNamespace(sessions={'worker-session': session}, get_session=lambda sid: session)
    path = str(tmp_path / 'jobs.sock')
    server = JobServer(path, JobHandler)
    server.controller = controller
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(path)
            connection.sendall(json.dumps({'action': 'review', 'session_id': 'reviewer', 'role': 'reviewer', 'data': {'verdict': 'accepted', 'reasons': 'I am reviewer'}}).encode() + b'\n')
            response = json.loads(connection.makefile('rb').readline())
        assert 'Only the assigned independent reviewer' in response['error']
        controller.manager.sessions = {}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(path)
            connection.sendall(b'{"action":"status","session_id":"worker-session"}\n')
            response = json.loads(connection.makefile('rb').readline())
        assert 'not a live assigned ACP' in response['error']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
