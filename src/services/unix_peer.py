import ctypes
import errno
import os
import socket
import struct
import sys

import psutil


def peer_credentials(connection):
    if sys.platform == 'linux':
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if sys.platform == 'darwin':
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        getpeereid = libc.getpeereid
        getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        getpeereid.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        pid, = struct.unpack('=i', connection.getsockopt(0, 0x002, 4))
        if pid <= 0:
            raise PermissionError('Unix peer has no valid process identity')
        return pid, uid.value, gid.value
    raise OSError(errno.ENOTSUP, 'Unix peer authentication requires Linux or macOS')


def owning_session(manager, pid):
    process = psutil.Process(pid)
    for _ in range(32):
        for session in list(manager.sessions.values()):
            if session.proc and session.proc.poll() is None and session.proc.pid == process.pid:
                return session.id
        process = process.parent()
        if process is None or process.pid <= 1:
            break
    raise PermissionError('Caller is not a live assigned ACP session')
