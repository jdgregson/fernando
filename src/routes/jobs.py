import hashlib
import io
import json
import zipfile
from pathlib import Path

from flask import Blueprint, render_template, request, send_file

from src.routes.web import _check_api_key
from src.services.jobs import store, text, identifier
from src.services.job_sessions import session_service
from src.services.settings import get_provider_settings


bp = Blueprint('jobs', __name__)


@bp.before_request
def authenticate():
    if (request.method != 'GET' and not request.headers.get('X-API-Key')) or not _check_api_key():
        return {'error': 'Unauthorized'}, 401


@bp.errorhandler(ValueError)
@bp.errorhandler(UnicodeDecodeError)
@bp.errorhandler(zipfile.BadZipFile)
def invalid(error):
    return {'error': str(error)}, 400


@bp.get('/jobs/<job_id>')
def panel(job_id):
    job = store.get(job_id) if job_id != 'new' else None
    return render_template('jobs.html', job_id=job_id, job=job, api_key=request.args.get('api_key') or request.headers.get('X-API-Key'), providers=get_provider_settings()['providers'])


def selection(value):
    if not isinstance(value, dict):
        raise ValueError('Select a provider and model')
    enabled = {item['id'] for item in get_provider_settings()['providers'] if item['enabled']}
    if value.get('backend') not in enabled:
        raise ValueError('Selected provider is disabled')
    return {'backend': value['backend'], 'model': text(value.get('model'), 200)}


@bp.get('/api/jobs')
def listing():
    return {'jobs': store.list()}


@bp.post('/api/jobs')
def create():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('Expected a job object')
    job = session_service.create(name=data.get('name'), scope=data.get('scope'), worker=selection(data.get('worker')), reviewer=selection(data.get('reviewer')), group_id=data.get('group_id'), source_id=data.get('source_id'), max_workers=data.get('max_workers', 5), reuse_workers=data.get('reuse_workers', False))
    return job, 201


@bp.post('/api/jobs/drafts')
def create_draft():
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('Expected a draft object')
    provider = next((item for item in get_provider_settings()['providers'] if item['enabled']), None)
    if not provider:
        raise ValueError('Enable an agent provider before creating a Job')
    model = {'backend': provider['id'], 'model': provider['model']}
    return session_service.create_draft(model, model, data.get('group_id')), 201


@bp.post('/api/jobs/<job_id>/draft')
def save_draft(job_id):
    data = request.get_json()
    if not isinstance(data, dict) or not isinstance(data.get('draft'), dict):
        raise ValueError('Expected draft contents and revision')
    values = dict(data['draft'])
    values['worker'] = selection(values.get('worker'))
    values['reviewer'] = selection(values.get('reviewer'))
    return session_service.save_draft(job_id, values, data.get('revision'), submit=data.get('submit') is True)


@bp.get('/api/jobs/<job_id>')
def detail(job_id):
    return store.get(job_id)


@bp.post('/api/jobs/<job_id>/operator')
def operator(job_id):
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError('Expected an operator action object')
    return session_service.perform(job_id, data.get('action'), data)


@bp.get('/api/jobs/<job_id>/evidence/<event_id>')
def evidence(job_id, event_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM evidence WHERE job_id=? AND id=?', (job_id, event_id)).fetchone()
        if not row:
            raise ValueError('Evidence not found')
        return {'id': row['id'], 'session_id': row['session_id'], 'sha256': row['sha256'], 'event': json.loads(row['event'])}


@bp.get('/api/jobs/<job_id>/source')
def source(job_id):
    with store.connect() as db:
        row = db.execute('SELECT filename,original FROM job_sources WHERE job_id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Uploaded source not found')
        return send_file(io.BytesIO(row['original']), as_attachment=True, download_name=Path(row['filename']).name, mimetype='application/octet-stream')


@bp.post('/api/jobs/document')
def document():
    uploaded = request.files.get('document')
    if not uploaded:
        raise ValueError('Choose a document')
    raw = uploaded.stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Documents must be at most 4 MiB')
    extension = Path(uploaded.filename).suffix.lower()
    if extension in ('.txt', '.md', '.markdown'):
        content = raw.decode('utf-8-sig')
    elif extension == '.docx':
        from docx import Document
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 16 * 1024 * 1024:
                raise ValueError('Expanded document exceeds 16 MiB')
        doc = Document(io.BytesIO(raw))
        content = '\n'.join(''.join(node.text or '' for node in paragraph.iter() if node.tag.endswith('}t')) for paragraph in doc.element.body.iter() if paragraph.tag.endswith('}p'))
    else:
        raise ValueError('Supported documents: UTF-8 TXT, Markdown, and DOCX. Review the extracted scope before creating the job.')
    content = text(content, 200000)
    source_id = identifier()
    digest = hashlib.sha256(raw).hexdigest()
    with store.connect() as db:
        db.execute('INSERT INTO job_sources VALUES(?,NULL,?,?,?,?)', (source_id, uploaded.filename, digest, content, raw))
    return {'scope': content, 'name': uploaded.filename, 'sha256': digest, 'source_id': source_id, 'review_required': True}
