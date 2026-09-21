import threading
import unittest
from unittest.mock import Mock, patch

from src.services import acp


class RestartLifecycleTests(unittest.TestCase):
    def test_late_old_process_eof_does_not_end_replacement_session(self):
        session = acp.ACPSession("aaaaaaaa", on_event=Mock())
        old, replacement = Mock(), Mock()
        old.poll.return_value = None
        session.proc = old
        session._alive = True
        session._is_prompting = True

        def late_eof(_):
            session.proc = replacement
            return b""

        old.stdout.read1.side_effect = late_eof
        with patch.object(acp.select, "select", return_value=([old.stdout], [], [])):
            session._read_loop(old)
        self.assertIs(session.proc, replacement)
        self.assertTrue(session._alive)
        self.assertTrue(session._is_prompting)
        replacement.stdout.read1.assert_not_called()
        session.on_event.assert_not_called()

    def test_unload_detaches_and_joins_readers_before_reuse(self):
        session = acp.ACPSession("aaaaaaaa")
        process = Mock()
        session.proc = process
        session._reader_thread = Mock()
        session._stderr_thread = Mock()
        session._reader_thread.is_alive.return_value = False
        session._stderr_thread.is_alive.return_value = False
        process.terminate.side_effect = lambda: self.assertIsNone(session.proc)
        with patch.object(session, "_save_history"):
            session.unload()
        session._reader_thread.join.assert_called_once()
        session._stderr_thread.join.assert_called_once()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close.assert_called_once()
        self.assertFalse(session._alive)

    def test_failed_reload_keeps_same_chat_and_history_available_for_retry(self):
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager._lock = threading.Lock()
        manager._save = Mock()
        manager._save_pid_map = Mock()
        manager._broadcast_sessions_list = Mock()
        session = acp.ACPSession("aaaaaaaa", on_event=Mock())
        session.acp_session_id = "same-native-conversation"
        session.history = [
            {"type": "user_prompt", "text": "preserve this conversation"}
        ]
        session._reloading = True
        session._recording = False
        session._broadcasting = False
        manager.sessions = {session.id: session}
        with (
            patch.object(session, "load", side_effect=RuntimeError("fixture timeout")),
            patch.object(session, "_save_history"),
            self.assertLogs(acp.logger, level="ERROR"),
        ):
            manager._load_existing(session.id, session, session.acp_session_id)
        self.assertIs(manager.get_session(session.id), session)
        self.assertEqual(session.acp_session_id, "same-native-conversation")
        self.assertEqual(session.history[0]["text"], "preserve this conversation")
        self.assertFalse(session._reloading)
        self.assertFalse(session.ready)
        self.assertTrue(session._recording)
        self.assertTrue(session._broadcasting)
        with patch.object(acp.threading, "Thread") as thread:
            self.assertTrue(manager.reload_session(session.id))
            thread.return_value.start.assert_called_once()
