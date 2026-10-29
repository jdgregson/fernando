import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Blueprint, Flask, request


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("restart_guard", ROOT / "scripts/restart_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class RestartGuardTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / "src/routes/web.py").read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"api_mutating_notify", "api_working_agents"}]
        self.manager = SimpleNamespace(working_agent_count=Mock(return_value=2))
        self.authorized = True
        self.socket = Mock()
        bp = Blueprint("test", __name__)
        scope = {"bp": bp, "request": request, "json": json, "acp_manager": self.manager, "_check_api_key": lambda: self.authorized}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "web.py", "exec"), scope)
        app = Flask(__name__)
        app.register_blueprint(bp)
        self.client = app.test_client()

    def test_guard_auth_block_force_and_boundary(self):
        with patch.dict(sys.modules, {"src": SimpleNamespace(socketio=self.socket)}):
            self.authorized = False
            self.assertEqual(self.client.get("/api/agents/working").status_code, 401)
            self.assertEqual(self.client.post("/api/mutating", json={"force": True}).status_code, 401)
            self.authorized = True
            self.assertEqual(self.client.get("/api/agents/working").json, {"working_count": 2})
            self.assertEqual(self.client.post("/api/mutating", json={}).status_code, 409)
            self.assertEqual(self.client.post("/api/mutating", json={"force": "false"}).status_code, 400)
            self.socket.emit.assert_not_called()
            self.assertEqual(self.client.post("/api/mutating", json={"force": True}).status_code, 200)
            for count in (0, 1):
                self.manager.working_agent_count.return_value = count
                self.assertEqual(self.client.post("/api/mutating", json={}).status_code, 200)

    def test_script_preflight_and_force(self):
        for count, force in ((0, False), (1, False), (2, False), (2, True)):
            with self.subTest(count=count, force=force):
                connection = Mock()
                connection.getresponse.side_effect = [
                    SimpleNamespace(status=200, read=lambda: json.dumps({"working_count": count}).encode()),
                    SimpleNamespace(status=200, read=lambda: json.dumps({"ok": True, "working_count": count}).encode()),
                ]
                with patch.object(guard.http.client, "HTTPConnection", return_value=connection), patch.object(guard.Path, "read_text", return_value="test-key"), patch.object(sys, "argv", ["guard"] + (["--force"] if force else [])):
                    if count > 1 and not force:
                        with self.assertRaisesRegex(SystemExit, "2 agents"):
                            guard.main()
                        self.assertEqual(connection.request.call_count, 1)
                    else:
                        guard.main()
                        self.assertEqual(connection.request.call_count, 2)

    def test_old_server_cannot_trigger_restart_notification(self):
        connection = Mock()
        connection.getresponse.return_value = SimpleNamespace(status=404, read=lambda: b"Not found")
        with patch.object(guard.http.client, "HTTPConnection", return_value=connection), patch.object(guard.Path, "read_text", return_value="test-key"), patch.object(sys, "argv", ["guard", "--force"]):
            with self.assertRaisesRegex(SystemExit, "HTTP 404"):
                guard.main()
        self.assertEqual(connection.request.call_args.args[0], "GET")
        self.assertEqual(connection.request.call_count, 1)

    def test_count_excludes_idle_and_unread_sessions(self):
        tree = ast.parse((ROOT / "src/services/acp.py").read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "working_agent_count")
        scope = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "acp.py", "exec"), scope)
        manager = SimpleNamespace(_lock=threading.Lock(), sessions={str(i): SimpleNamespace(get_status=Mock(return_value=status)) for i, status in enumerate(("working", "idle", "unread", "working"))})
        self.assertEqual(scope["working_agent_count"](manager), 2)

    def test_blocked_tools_do_not_save_continuation_or_launch_process(self):
        tree = ast.parse((ROOT / "mcp_servers/system_mcp.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "call_tool")
        function.decorator_list = []
        function.returns = None
        process = Mock()
        process.run.return_value = SimpleNamespace(returncode=1, stderr="Restart blocked: 2 agents are currently working", stdout="")
        save = Mock()
        import os
        scope = {"subprocess": process, "os": os, "PROJECT_ROOT": str(ROOT), "json": json, "TextContent": SimpleNamespace, "save_continuation": save}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "system_mcp.py", "exec"), scope)
        for name in ("mutate", "reboot"):
            for force in (False, True):
                result = asyncio.run(scope["call_tool"](name, {"force": force, "continuation": "resume"}))
                self.assertEqual(json.loads(result[0].text)["status"], "blocked")
                self.assertEqual("--force" in process.run.call_args.args[0], force)
        process.Popen.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
