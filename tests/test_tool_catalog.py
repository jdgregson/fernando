import unittest
from unittest.mock import AsyncMock, patch
from src.services import mcp_client


class ToolCatalogTests(unittest.TestCase):
    def test_discovery_never_returns_a_previous_schema(self):
        configs = {'disabled': {'command': 'fixture'}}
        discover = AsyncMock(side_effect=[[{'description': 'old'}], [{'description': 'new'}]])
        with patch.object(mcp_client, '_load_server_configs', return_value=configs), patch.object(mcp_client, '_list_tools_from_server', discover):
            self.assertEqual(mcp_client.list_tools('disabled'), [{'description': 'old'}])
            self.assertEqual(mcp_client.list_tools('disabled'), [{'description': 'new'}])
            self.assertEqual(discover.await_count, 2)

    def test_failure_is_not_replaced_with_cached_tools(self):
        with patch.object(mcp_client, '_load_server_configs', return_value={'fixture': {}}), patch.object(mcp_client, '_list_tools_from_server', AsyncMock(side_effect=RuntimeError('offline'))):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                mcp_client.list_tools('fixture')

    def test_registry_includes_non_global_servers(self):
        config = {'servers': {'enabled': {'global': True}, 'disabled': {'global': False}}}
        with patch('src.services.context_templates.get_config', return_value=config), patch('src.services.context_templates.server_config', return_value={'command': 'fixture'}):
            self.assertEqual(mcp_client.list_servers(), ['enabled', 'disabled'])
