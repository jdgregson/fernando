import ctypes
import grp
import json
import mimetypes
import os
import pwd
import stat
import sys
import threading
import time
import uuid
from pathlib import Path


SESSIONS_FILE = Path(__file__).resolve().parents[2] / 'data' / 'file_browser_sessions.json'
_lock = threading.Lock()


class StatxTimestamp(ctypes.Structure):
    _fields_ = [('seconds', ctypes.c_int64), ('nanoseconds', ctypes.c_uint32), ('reserved', ctypes.c_int32)]


class Statx(ctypes.Structure):
    _fields_ = [
        ('mask', ctypes.c_uint32), ('block_size', ctypes.c_uint32), ('attributes', ctypes.c_uint64),
        ('links', ctypes.c_uint32), ('uid', ctypes.c_uint32), ('gid', ctypes.c_uint32),
        ('mode', ctypes.c_uint16), ('spare', ctypes.c_uint16), ('inode', ctypes.c_uint64),
        ('size', ctypes.c_uint64), ('blocks', ctypes.c_uint64), ('attributes_mask', ctypes.c_uint64),
        ('accessed', StatxTimestamp), ('created', StatxTimestamp), ('changed', StatxTimestamp),
        ('modified', StatxTimestamp), ('remaining', ctypes.c_ubyte * 128),
    ]


def creation_time(path, metadata):
    if sys.platform != 'linux':
        return getattr(metadata, 'st_birthtime', None)
    result = Statx()
    libc = ctypes.CDLL(None, use_errno=True)
    statx = libc.statx
    statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(Statx)]
    statx.restype = ctypes.c_int
    if statx(-100, os.fsencode(path), 0x100, 0x800, ctypes.byref(result)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), path)
    return result.created.seconds + result.created.nanoseconds / 1_000_000_000 if result.mask & 0x800 else None


def properties(path):
    metadata = os.lstat(path)
    types = {stat.S_IFREG: 'Regular file', stat.S_IFDIR: 'Directory', stat.S_IFLNK: 'Symbolic link',
             stat.S_IFIFO: 'Named pipe', stat.S_IFSOCK: 'Socket', stat.S_IFBLK: 'Block device', stat.S_IFCHR: 'Character device'}
    try:
        owner = pwd.getpwuid(metadata.st_uid).pw_name
    except KeyError:
        owner = None
    try:
        group = grp.getgrgid(metadata.st_gid).gr_name
    except KeyError:
        group = None
    return {
        'name': os.path.basename(path) or '/', 'path': path, 'location': os.path.dirname(path),
        'type': types.get(stat.S_IFMT(metadata.st_mode), 'Unknown'), 'directory': stat.S_ISDIR(metadata.st_mode),
        'mime': mimetypes.guess_type(path)[0] if stat.S_ISREG(metadata.st_mode) else None,
        'size': metadata.st_size, 'allocated': metadata.st_blocks * 512,
        'created': creation_time(path, metadata), 'modified': metadata.st_mtime,
        'accessed': metadata.st_atime, 'changed': metadata.st_ctime,
        'owner': owner, 'uid': metadata.st_uid, 'group': group, 'gid': metadata.st_gid,
        'mode': stat.filemode(metadata.st_mode), 'permissions': format(stat.S_IMODE(metadata.st_mode), '04o'),
        'inode': str(metadata.st_ino), 'device': str(metadata.st_dev), 'links': metadata.st_nlink,
        'hidden': os.path.basename(path).startswith('.'),
        'target': os.readlink(path) if stat.S_ISLNK(metadata.st_mode) else None,
    }


def directory_totals(path):
    root = os.lstat(path)
    if not stat.S_ISDIR(root.st_mode):
        raise NotADirectoryError(path)
    pending = [path]
    seen = {(root.st_dev, root.st_ino)}
    totals = {'files': 0, 'directories': 0, 'symlinks': 0, 'other': 0, 'size': 0, 'allocated': root.st_blocks * 512, 'complete': True}
    deadline = time.monotonic() + 3
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if time.monotonic() >= deadline or sum(totals[key] for key in ('files', 'directories', 'symlinks', 'other')) >= 100000:
                    totals['complete'] = False
                    return totals
                metadata = entry.stat(follow_symlinks=False)
                directory = stat.S_ISDIR(metadata.st_mode)
                key = 'directories' if directory else 'symlinks' if stat.S_ISLNK(metadata.st_mode) else 'files' if stat.S_ISREG(metadata.st_mode) else 'other'
                totals[key] += 1
                identity = (metadata.st_dev, metadata.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                totals['allocated'] += metadata.st_blocks * 512
                if directory:
                    pending.append(entry.path)
                else:
                    totals['size'] += metadata.st_size
    return totals


def list_sessions():
    with _lock:
        sessions = json.loads(SESSIONS_FILE.read_text()) if SESSIONS_FILE.exists() else []
        return [{**session, 'home': os.path.expanduser('~')} for session in sessions]


def update_session(session_id=None, name=None):
    with _lock:
        sessions = json.loads(SESSIONS_FILE.read_text()) if SESSIONS_FILE.exists() else []
        if session_id is None:
            session = {'id': uuid.uuid4().hex, 'name': name or f'Files-{len(sessions) + 1}'}
            sessions.append(session)
        else:
            session = next((item for item in sessions if item['id'] == session_id), None)
            if session is None:
                raise FileNotFoundError('File browser session does not exist')
            if name is None:
                sessions.remove(session)
            else:
                session['name'] = name
        temporary = SESSIONS_FILE.with_suffix('.tmp')
        temporary.write_text(json.dumps(sessions))
        os.replace(temporary, SESSIONS_FILE)
        return session
