import unittest
from unittest.mock import Mock, patch

from src.services.acp import ACPSession
from src.services.mcp_startup import MCPStartup


class MCPStartupTests(unittest.TestCase):
    def setUp(self):
        self.publish = Mock()
        self.state = MCPStartup(self.publish)
        self.generation = self.state.begin(['desktop', 'chat'])

    def test_only_configured_failures_are_visible(self):
        self.state.update(self.generation, 'chat', 'connected')
        self.state.update(self.generation, 'not-configured', 'failed', 'ignored')
        self.assertEqual(self.state.snapshot()['failures'], [])
        self.state.update(self.generation, 'desktop', 'failed', 'connection closed: initialize response')
        self.assertEqual(self.state.snapshot()['failures'], [{
            'name': 'desktop', 'status': 'failed', 'error': 'connection closed: initialize response',
        }])

    def test_duplicate_failure_is_not_announced_twice(self):
        self.state.update(self.generation, 'desktop', 'failed', 'closed')
        revision = self.state.snapshot()['revision']
        self.state.update(self.generation, 'desktop', 'failed', 'closed')
        self.assertEqual(self.state.snapshot()['revision'], revision)

    def test_missing_confirmation_is_visible_and_late_success_clears_it(self):
        self.state.update(self.generation, 'chat', 'connected')
        self.state.unconfirmed(self.generation, 'No startup confirmation')
        self.assertEqual([v['name'] for v in self.state.snapshot()['failures']], ['desktop'])
        self.state.update(self.generation, 'desktop', 'connected')
        self.assertEqual(self.state.snapshot()['failures'], [])

    def test_old_startup_timer_and_status_cannot_corrupt_new_launch(self):
        generation = self.state.begin(['chat'])
        self.state.update(generation, 'chat', 'connected')
        self.state.unconfirmed(self.generation, 'Old timeout')
        self.state.update(self.generation, 'chat', 'failed', 'Old failure')
        self.assertEqual(self.state.snapshot()['failures'], [])

    def test_opencode_failed_auth_disabled_and_missing_servers(self):
        generation = self.state.begin(['desktop', 'chat', 'auth', 'disabled', 'missing'])
        self.state.opencode(generation, {
            'desktop': {'status': 'failed', 'error': 'MCP error -32000: Connection closed'},
            'chat': {'status': 'connected'}, 'auth': {'status': 'needs_auth'},
            'disabled': {'status': 'disabled'},
        })
        self.assertEqual([v['name'] for v in self.state.snapshot()['failures']],
                         ['desktop', 'auth', 'disabled', 'missing'])

    def test_failure_snapshot_is_independent_of_chat_history(self):
        self.state.update(self.generation, 'desktop', 'failed', 'closed')
        snapshot = self.state.snapshot()
        snapshot['failures'][0]['error'] = 'changed externally'
        self.assertEqual(self.state.snapshot()['failures'][0]['error'], 'closed')

    def test_real_kiro_notification_survives_load_replay_suppression(self):
        session = ACPSession('aaaaaaaa', on_event=Mock())
        session.mcp_startup.begin(['desktop'])
        session._recording = False
        session._broadcasting = False
        session._dispatch({
            'jsonrpc': '2.0', 'method': '_kiro.dev/mcp/server_init_failure',
            'params': {'sessionId': 'native-session', 'serverName': 'desktop',
                       'error': 'connection closed: initialize response'},
        })
        event = session.on_event.call_args.args[1]
        self.assertEqual(event['type'], 'mcp_status')
        self.assertEqual(event['failures'][0]['name'], 'desktop')
        self.assertEqual(session.history, [])

    def test_opencode_status_query_failure_is_not_silent(self):
        session = ACPSession('aaaaaaaa', on_event=Mock(), backend='opencode')
        session.mcp_startup.begin(['desktop'])
        with patch.object(session.provider, 'request', side_effect=RuntimeError('HTTP unavailable')):
            session._check_mcp_startup()
        self.assertIn('HTTP unavailable', session.mcp_startup.snapshot()['failures'][0]['error'])

    def test_kiro_missing_event_deadline_is_scheduled(self):
        session = ACPSession('aaaaaaaa', on_event=Mock())
        session.mcp_startup.begin(['desktop'])
        with patch('src.services.acp.threading.Timer') as timer:
            session._check_mcp_startup()
        callback = timer.call_args.args[1]
        callback(*timer.call_args.kwargs['args'])
        self.assertEqual(session.mcp_startup.snapshot()['failures'][0]['status'], 'unconfirmed')
