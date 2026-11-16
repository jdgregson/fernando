import ctypes
import errno
import os
import socket
import struct
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import unix_peer


def test_native_unix_peer_credentials(tmp_path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(tmp_path / 'peer.sock'))
        listener.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(listener.getsockname())
            connection, _ = listener.accept()
            with connection:
                assert unix_peer.peer_credentials(connection) == (os.getpid(), os.geteuid(), os.getegid())


def darwin_library(monkeypatch, result=0):
    def getpeereid(fd, uid, gid):
        assert fd == 17
        ctypes.cast(uid, ctypes.POINTER(ctypes.c_uint))[0] = 501
        ctypes.cast(gid, ctypes.POINTER(ctypes.c_uint))[0] = 20
        if result:
            ctypes.set_errno(errno.EACCES)
        return result

    function = Mock(side_effect=getpeereid)
    library = Mock(return_value=SimpleNamespace(getpeereid=function))
    monkeypatch.setattr(unix_peer.sys, 'platform', 'darwin')
    monkeypatch.delattr(socket, 'SO_PEERCRED', raising=False)
    monkeypatch.setattr(unix_peer.ctypes, 'CDLL', library)
    connection = Mock()
    connection.fileno.return_value = 17
    connection.getsockopt.return_value = struct.pack('=i', 1234)
    return connection, library, function


def test_macos_uses_peer_pid_and_libsystem_credentials_without_linux_constant(monkeypatch):
    connection, library, function = darwin_library(monkeypatch)
    assert unix_peer.peer_credentials(connection) == (1234, 501, 20)
    library.assert_called_once_with('/usr/lib/libSystem.B.dylib', use_errno=True)
    connection.getsockopt.assert_called_once_with(0, 0x002, 4)
    assert function.restype is ctypes.c_int
    assert function.argtypes == [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]


def test_macos_credential_failure_does_not_attempt_identity_lookup(monkeypatch):
    connection, _, _ = darwin_library(monkeypatch, -1)
    with pytest.raises(OSError) as error:
        unix_peer.peer_credentials(connection)
    assert error.value.errno == errno.EACCES
    connection.getsockopt.assert_not_called()


@pytest.mark.parametrize('pid', [0, -1])
def test_macos_rejects_missing_peer_pid(monkeypatch, pid):
    connection, _, _ = darwin_library(monkeypatch)
    connection.getsockopt.return_value = struct.pack('=i', pid)
    with pytest.raises(PermissionError):
        unix_peer.peer_credentials(connection)


def test_unsupported_platform_is_rejected(monkeypatch):
    monkeypatch.setattr(unix_peer.sys, 'platform', 'unsupported')
    connection = Mock()
    with pytest.raises(OSError) as error:
        unix_peer.peer_credentials(connection)
    assert error.value.errno == errno.ENOTSUP
    connection.getsockopt.assert_not_called()


def test_native_process_ancestry_accepts_owner_and_rejects_sibling():
    children = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) for _ in range(2)]
    try:
        owner = SimpleNamespace(id='owner', proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None))
        manager = SimpleNamespace(sessions={'owner': owner})
        assert unix_peer.owning_session(manager, children[0].pid) == 'owner'
        manager.sessions = {'sibling': SimpleNamespace(id='sibling', proc=children[1])}
        with pytest.raises(PermissionError):
            unix_peer.owning_session(manager, children[0].pid)
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)


def test_exited_agent_cannot_authorize_a_caller():
    session = SimpleNamespace(id='dead', proc=SimpleNamespace(pid=os.getpid(), poll=lambda: 0))
    with pytest.raises(PermissionError):
        unix_peer.owning_session(SimpleNamespace(sessions={'dead': session}), os.getpid())
