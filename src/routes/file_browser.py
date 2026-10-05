import ctypes
import errno
import fcntl
import hashlib
import mimetypes
import os
import shutil
import stat
from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from src.routes.web import _check_api_key
from src.services import file_browser, groups, settings


bp = Blueprint('file_browser', __name__)
TEXT_LIMIT = 8 * 1024 * 1024


@bp.before_request
def authenticate():
    if not _check_api_key():
        abort(401)
    if request.method != 'GET' and not request.headers.get('X-API-Key'):
        abort(401)


@bp.after_request
def response_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def payload():
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400, 'Expected a JSON object')
    return data


@bp.errorhandler(OSError)
def filesystem_error(error):
    status = 403 if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS) else 409 if isinstance(error, FileExistsError) else 404 if isinstance(error, FileNotFoundError) else 400
    return jsonify(error=str(error)), status


@bp.errorhandler(HTTPException)
def request_error(error):
    return jsonify(error=error.description), error.code


def path_value(value):
    if not isinstance(value, str) or not value.startswith('/') or '\0' in value:
        abort(400, 'An absolute filesystem path is required')
    return os.path.normpath(value)


def regular_file(path, flags=os.O_RDONLY):
    fd = os.open(path, flags | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        abort(400, 'Only regular files can be opened')
    return fd


def read_text_bytes(stream):
    content = stream.read(TEXT_LIMIT + 1)
    if len(content) > TEXT_LIMIT:
        abort(413, 'This file exceeds the 8 MiB text editor limit. Download it to open locally.')
    return content


@bp.get('/files/<session_id>')
def page(session_id):
    if not any(item['id'] == session_id for item in file_browser.list_sessions()):
        abort(404)
    return render_template('file_browser.html', api_key=Path('/tmp/fernando-api-key').read_text().strip(), home=os.path.expanduser('~'), default_view=settings.get('file_browser_view'), default_hidden=settings.get('file_browser_hidden'))


@bp.post('/api/file-browser/sessions')
def sessions():
    data = payload()
    name = data.get('name')
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 120):
        abort(400, 'Invalid session name')
    session = file_browser.update_session(data.get('id'), name)
    if not data.get('id'):
        groups.move_session_to_group('files:' + session['id'], data.get('group_id'))
    elif name is None:
        groups.move_session_to_group('files:' + session['id'], None)
    return jsonify(session)


@bp.get('/api/file-browser/list')
def listing():
    path = path_value(request.args.get('path', os.path.expanduser('~')))
    entries = []
    with os.scandir(path) as directory:
        for entry in directory:
            metadata = entry.stat(follow_symlinks=False)
            entries.append({'name': entry.name, 'path': entry.path, 'directory': entry.is_dir(), 'symlink': entry.is_symlink(), 'size': metadata.st_size, 'modified': metadata.st_mtime, 'mode': stat.filemode(metadata.st_mode)})
    return jsonify(path=path, parent=os.path.dirname(path), entries=entries)


@bp.get('/api/file-browser/properties')
def properties():
    return jsonify(file_browser.properties(path_value(request.args.get('path'))))


@bp.get('/api/file-browser/directory-totals')
def directory_totals():
    return jsonify(file_browser.directory_totals(path_value(request.args.get('path'))))


@bp.get('/api/file-browser/text')
def text():
    with os.fdopen(regular_file(path_value(request.args.get('path'))), 'rb') as stream:
        content = read_text_bytes(stream)
    if b'\0' in content:
        abort(415, 'This file contains binary data and cannot be safely edited as text.')
    try:
        decoded = content.decode('utf-8')
    except UnicodeDecodeError:
        abort(415, 'This file is not UTF-8 text and cannot be safely edited here.')
    return jsonify(text=decoded, revision=hashlib.sha256(content).hexdigest())


@bp.post('/api/file-browser/save')
def save():
    data = payload()
    if not isinstance(data.get('text'), str) or not isinstance(data.get('revision'), str):
        abort(400, 'Text and its original revision are required')
    content = data['text'].encode('utf-8')
    if len(content) > TEXT_LIMIT:
        abort(413)
    with os.fdopen(regular_file(path_value(data.get('path')), os.O_RDWR), 'r+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        current = read_text_bytes(stream)
        if hashlib.sha256(current).hexdigest() != data['revision']:
            abort(409, 'The file changed on disk. Reopen it before saving; your edits have not been written.')
        stream.seek(0)
        stream.write(content)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    return jsonify(revision=hashlib.sha256(content).hexdigest())


@bp.get('/api/file-browser/content')
def content():
    path = path_value(request.args.get('path'))
    stream = os.fdopen(regular_file(path), 'rb')
    mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    inline = request.args.get('inline') == '1' and (mime.startswith('image/') or mime == 'application/pdf')
    response = send_file(stream, mimetype=mime if inline else 'application/octet-stream', as_attachment=not inline, download_name=os.path.basename(path), conditional=False)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'self'" if inline and mime == 'application/pdf' else "sandbox; default-src 'none'; frame-ancestors 'self'"
    response.call_on_close(stream.close)
    return response


@bp.get('/api/file-browser/stl')
def stl():
    path = path_value(request.args.get('path'))
    query = urlencode({'path': path, 'api_key': Path('/tmp/fernando-api-key').read_text().strip()})
    return render_template('stl_viewer.html', stl_url='/api/file-browser/content?' + query, filename=os.path.basename(path))


@bp.post('/api/file-browser/upload')
def upload():
    directory = path_value(request.form.get('path'))
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename or uploaded.filename in ('.', '..') or '/' in uploaded.filename or '\\' in uploaded.filename or '\0' in uploaded.filename:
        abort(400, 'A plain filename is required')
    with open(os.path.join(directory, uploaded.filename), 'xb') as stream:
        shutil.copyfileobj(uploaded.stream, stream)
    return jsonify(ok=True)


@bp.post('/api/file-browser/operation')
def operation():
    data = payload()
    path = path_value(data.get('path'))
    action = data.get('action')
    if path == '/':
        abort(400, 'Select a file or directory, not the filesystem root')
    if action == 'mkdir':
        os.mkdir(path)
    elif action == 'create':
        with open(path, 'xb'):
            pass
    elif action == 'delete':
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    elif action in ('move', 'copy'):
        destination = path_value(data.get('destination'))
        if os.path.isdir(path) and not os.path.islink(path) and os.path.commonpath([os.path.realpath(path), os.path.realpath(destination)]) == os.path.realpath(path):
            abort(400, 'A folder cannot be placed inside itself')
        if action == 'move':
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(path), -100, os.fsencode(destination), 1) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), destination)
        elif os.path.isdir(path) and not os.path.islink(path):
            shutil.copytree(path, destination, symlinks=True)
        elif os.path.islink(path):
            os.symlink(os.readlink(path), destination)
        else:
            with os.fdopen(regular_file(path), 'rb') as source, open(destination, 'xb') as target:
                shutil.copyfileobj(source, target)
            shutil.copymode(path, destination)
    else:
        abort(400, 'Unknown file operation')
    return jsonify(ok=True)
