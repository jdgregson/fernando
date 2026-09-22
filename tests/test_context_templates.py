import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.services import acp, context_templates as context, groups


class ContextFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, key, value in (
            (context, "ROOT", self.root),
            (context, "HOME", self.root / "home"),
            (context, "STORE", self.root / "context.json"),
            (context, "RUNTIME", self.root / "runtime"),
            (groups, "GROUPS_FILE", str(self.root / "groups.json")),
            (acp, "SESSIONS_FILE", str(self.root / "sessions.json")),
            (acp, "ARCHIVED_FILE", str(self.root / "archived.json")),
        ):
            p = patch.object(module, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.patch_env = patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")}
        )
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.doc = self.root / "rules.md"
        self.doc.write_text("Only the chosen context.")
        self.config = {
            "revision": 0,
            "documents": {
                "rules": {"name": "Rules", "path": str(self.doc), "global": True}
            },
            "servers": {
                "base": {
                    "global": True,
                    "kiro": {"command": "python3", "args": ["base.py"]},
                },
                "extra": {
                    "global": False,
                    "kiro": {"command": "python3", "args": ["extra.py"]},
                },
            },
            "templates": {
                "dev": {
                    "name": "Development",
                    "documents": ["rules"],
                    "servers": ["extra"],
                }
            },
        }
        context._write(context.STORE, self.config)
        self.group = groups.create_group("Development", template_ids=["dev"])


class ContextTests(ContextFixture, unittest.TestCase):
    def test_initial_prompts_validate_persist_and_follow_template_order(self):
        self.config['templates']['dev']['initial_prompt'] = 'First\nmessage'
        self.config['templates']['next'] = {
            'name': 'Next', 'documents': [], 'servers': [], 'initial_prompt': 'Second'
        }
        context.save_config(self.config)
        groups.set_templates(self.group['id'], ['next', 'dev'])
        for backend in ('kiro', 'opencode'):
            self.assertEqual(context.resolve(self.group['id'], backend)['initial_prompt'], 'Second\n\nFirst\nmessage')
            self.assertEqual(context.resolve(None, backend)['initial_prompt'], '')
        for invalid in (None, 42, [], {}):
            config = context.get_config()
            config['templates']['dev']['initial_prompt'] = invalid
            with self.assertRaisesRegex(ValueError, 'initial prompt must be text'):
                context.save_config(config)
        config = context.get_config()
        config['templates']['dev']['initial_prompt'] = ' \n '
        config['templates']['next'].pop('initial_prompt')
        context.save_config(config)
        self.assertEqual(context.resolve(self.group['id'], 'kiro')['initial_prompt'], '')

    def test_only_fresh_eligible_launches_receive_template_prompt(self):
        self.config['templates']['dev']['initial_prompt'] = 'Begin work'
        context.save_config(self.config)
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager.sessions = {}
        manager._lock = threading.Lock()
        manager._on_status_change = None
        manager._save = Mock()
        manager._save_pid_map = Mock()
        for backend in ('kiro', 'opencode'):
            for group_id, enabled, expected in (
                (self.group['id'], True, 'Begin work'),
                (self.group['id'], False, None),
                (None, True, None),
            ):
                with self.subTest(backend=backend, group=group_id, enabled=enabled):
                    with patch.object(acp.threading, 'Thread') as thread:
                        sid = manager.create_session(backend=backend, group_id=group_id, use_template_prompt=enabled)
                    session = manager.sessions[sid]
                    session.start = Mock()
                    session._load_history = Mock()
                    session.send_prompt = Mock()
                    manager._start_new(*thread.call_args.kwargs['args'])
                    if expected:
                        session.send_prompt.assert_called_once_with(expected, initial_only=True)
                    else:
                        session.send_prompt.assert_not_called()
                    session.send_prompt.reset_mock()
                    manager._start_new(sid, session)
                    session.send_prompt.assert_not_called()

    def test_initial_prompt_ignores_startup_events_but_never_overrides_existing_work(self):
        for prior, busy, expected in (
            ([{'method': 'session/update'}], False, True),
            ([{'type': 'user_prompt', 'text': 'My instructions'}], False, False),
            ([{'type': 'continuation', 'text': 'Resume'}], False, False),
            ([], True, False),
        ):
            session = acp.ACPSession('aaaaaaaa')
            session.acp_session_id = 'native-session'
            session.ready = True
            session.history = copy.deepcopy(prior)
            session._is_prompting = busy
            session._save_history = Mock()
            session._send = Mock()
            session.send_prompt('Begin work', initial_only=True)
            self.assertEqual(session._send.call_count, int(expected))
            session._is_prompting = False
            if expected:
                session.send_prompt('Begin work', initial_only=True)
                session._send.assert_called_once()

    def test_subagent_launch_excludes_template_prompt_and_keeps_task(self):
        from flask import Flask
        from src.routes import web

        app = Flask(__name__)
        with (
            app.test_request_context('/api/spawn_subagent', json={'task': 'Specific task', 'group_id': self.group['id']}),
            patch.object(web, '_check_api_key', return_value=True),
            patch.object(web, 'acp_manager') as manager,
            patch.object(web.threading, 'Thread') as thread,
        ):
            manager.create_session.return_value = 'aaaaaaaa'
            web.api_spawn_subagent()
            self.assertFalse(manager.create_session.call_args.kwargs['use_template_prompt'])
            thread.call_args.kwargs['target']()
            manager.get_session.return_value.send_prompt.assert_called_once_with('Specific task')

    def test_group_move_blocks_work_and_restarts_idle_or_sleeping_chats(self):
        for backend in ("kiro", "opencode"):
            for state in ("working", "reloading", "idle", "sleeping"):
                with self.subTest(backend=backend, state=state):
                    groups.move_session_to_group("chat:aaaaaaaa", None)
                    manager = acp.ACPManager.__new__(acp.ACPManager)
                    manager._lock = threading.Lock()
                    manager._on_sessions_change = None
                    manager._save = Mock()
                    manager._save_pid_map = Mock()
                    session = acp.ACPSession("aaaaaaaa", backend=backend)
                    session.acp_session_id = "same-native-session"
                    session.history = [{"type": "user_prompt", "text": "keep this"}]
                    session.ready = state != "sleeping"
                    session._is_prompting = state == "working"
                    session._reloading = state == "reloading"
                    if state != "sleeping":
                        session.proc = Mock()
                        session.proc.poll.return_value = None
                    session.unload = Mock()
                    manager.sessions = {session.id: session}
                    with patch.object(acp.threading, "Thread") as thread:
                        if state in ("working", "reloading"):
                            with self.assertRaisesRegex(ValueError, "Cannot move"):
                                manager.move_chat_to_group(session.id, self.group["id"])
                            self.assertIsNone(
                                groups.get_session_groups().get("chat:aaaaaaaa")
                            )
                            session.unload.assert_not_called()
                            thread.assert_not_called()
                        else:
                            self.assertTrue(
                                manager.move_chat_to_group(session.id, self.group["id"])
                            )
                            session.unload.assert_called_once()
                            self.assertEqual(
                                groups.get_session_groups()["chat:aaaaaaaa"],
                                self.group["id"],
                            )
                            self.assertEqual(
                                thread.call_args.kwargs["args"],
                                (session.id, session, "same-native-session"),
                            )
                            thread.return_value.start.assert_called_once()
                            self.assertFalse(session.ready)
                            self.assertTrue(session._reloading)
                            self.assertEqual(
                                session.context_snapshot["group_id"], self.group["id"]
                            )
                    self.assertEqual(
                        session.history, [{"type": "user_prompt", "text": "keep this"}]
                    )

    def test_every_process_launch_refreshes_group_globals_and_file_contents(self):
        for backend in ("kiro", "opencode"):
            for previous in (None, {"servers": {"obsolete": {}}, "documents": []}):
                with self.subTest(backend=backend, legacy=previous is None):
                    context._write(context.STORE, self.config)
                    self.doc.write_text("initial rules")
                    session = acp.ACPSession("aaaaaaaa", backend=backend)
                    session.acp_session_id = "existing-native-conversation"
                    session.context_snapshot = copy.deepcopy(previous)
                    groups.move_session_to_group("chat:aaaaaaaa", self.group["id"])
                    with (
                        patch.object(context, "prepare", return_value=({}, [])),
                        patch.object(
                            acp.subprocess,
                            "Popen",
                            side_effect=RuntimeError("launch boundary"),
                        ),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "launch boundary"):
                            session._spawn_and_init()
                        self.assertEqual(
                            set(session.context_snapshot["servers"]), {"base", "extra"}
                        )
                        groups.move_session_to_group("chat:aaaaaaaa", None)
                        current = context.get_config()
                        current["servers"]["base"]["global"] = False
                        current["servers"]["extra"]["global"] = True
                        context._write(context.STORE, current)
                        self.doc.write_text("fresh rules")
                        with self.assertRaisesRegex(RuntimeError, "launch boundary"):
                            session._spawn_and_init()
                    self.assertEqual(
                        set(session.context_snapshot["servers"]), {"extra"}
                    )
                    self.assertIsNone(session.context_snapshot["group_id"])
                    self.assertEqual(
                        session.context_snapshot["documents"][0]["content"],
                        "fresh rules",
                    )
                    self.assertEqual(
                        session.acp_session_id, "existing-native-conversation"
                    )

    def test_settings_and_group_actions_require_csrf_and_return_acknowledgements(self):
        from flask import Flask, request
        from src.routes import websocket

        handlers = {}

        class Socket:
            def on(self, event):
                def register(function):
                    handlers[event] = function
                    return function

                return register

        with (
            patch.object(websocket, "acp_manager", Mock()),
            patch.object(websocket.automation_manager, "start"),
            patch.object(websocket, "emit") as emit,
        ):
            websocket.register_handlers(Socket())
            app = Flask(__name__)
            with app.test_request_context("/"):
                request.sid = "context-test"
                with patch.dict(websocket.csrf_tokens, {"context-test": "valid"}):
                    for event in (
                        "context_save",
                        "group_set_templates",
                        "acp_apply_context",
                    ):
                        self.assertIn("error", handlers[event]({"csrf_token": "wrong"}))
                    saved = handlers["context_save"](
                        {"csrf_token": "valid", "config": self.config}
                    )
                    self.assertEqual(saved["revision"], 1)
                    result = handlers["group_set_templates"](
                        {
                            "csrf_token": "valid",
                            "group_id": self.group["id"],
                            "template_ids": [],
                        }
                    )
                    self.assertEqual(result["group"]["template_ids"], [])
                    emit.assert_called_with("group_updated", result, broadcast=True)
                    bad = handlers["group_set_templates"](
                        {
                            "csrf_token": "valid",
                            "group_id": self.group["id"],
                            "template_ids": ["missing"],
                        }
                    )
                    self.assertIn("error", bad)
                    websocket.acp_manager.move_chat_to_group.side_effect = ValueError(
                        "Cannot move while working"
                    )
                    emit.reset_mock()
                    handlers["group_move_session"](
                        {
                            "csrf_token": "valid",
                            "session_key": "chat:aaaaaaaa",
                            "group_id": self.group["id"],
                        }
                    )
                    emit.assert_called_once_with(
                        "group_move_failed", {"message": "Cannot move while working"}
                    )

    def test_selection_deduplication_and_immutable_snapshot(self):
        selected = context.resolve(self.group["id"], "opencode")
        plain = context.resolve(None, "kiro")
        self.assertEqual(list(selected["servers"]), ["base", "extra"])
        self.assertEqual(list(plain["servers"]), ["base"])
        self.assertEqual(len(selected["documents"]), 1)
        self.assertEqual(
            selected["servers"]["extra"]["command"], ["python3", "extra.py"]
        )
        self.doc.write_text("Updated rules")
        self.config["servers"]["extra"]["kiro"]["args"] = ["changed.py"]
        context.save_config(self.config)
        self.assertEqual(
            selected["documents"][0]["content"], "Only the chosen context."
        )
        self.assertEqual(selected["servers"]["extra"]["command"][-1], "extra.py")
        self.assertEqual(
            context.resolve(None, "kiro")["documents"][0]["content"], "Updated rules"
        )

    def test_registration_survives_disabling_and_stale_writes_fail(self):
        from src.services import mcp_client

        mcp_client.set_server_enabled("base", False)
        self.assertIn("base", mcp_client._load_server_configs())
        self.assertNotIn("base", context.resolve(None, "kiro")["servers"])
        with self.assertRaisesRegex(ValueError, "changed elsewhere"):
            context.save_config(self.config)

    def test_invalid_selections_and_missing_documents_fail_before_launch(self):
        with self.assertRaises(ValueError):
            groups.set_templates(self.group["id"], ["missing"])
        with self.assertRaises(ValueError):
            context.resolve("missing", "kiro")
        self.doc.unlink()
        with self.assertRaises(OSError):
            context.resolve(self.group["id"], "kiro")

    def test_jsonc_preserves_urls_strings_and_comment_like_content(self):
        path = self.root / "config.jsonc"
        path.write_text(
            '{/* comment */ "url": "https://host/a//b", "text": "a,}/*x*/", // line\n"list": [1,],}'
        )
        self.assertEqual(
            context.read_jsonc(path),
            {"url": "https://host/a//b", "text": "a,}/*x*/", "list": [1]},
        )

    def test_per_process_configuration_leaves_originals_untouched(self):
        shared = self.root / "config/opencode/opencode.jsonc"
        shared.parent.mkdir(parents=True)
        shared.write_text(
            json.dumps(
                {
                    "permission": "allow",
                    "mcp": {"unwanted": {"enabled": True}},
                    "instructions": ["/unwanted.md"],
                }
            )
        )
        original = shared.read_bytes()
        a = context.resolve(self.group["id"], "opencode")
        b = context.resolve(None, "opencode")
        env_a, _ = context.prepare("aaaaaaaa", a, "opencode")
        env_b, _ = context.prepare("bbbbbbbb", b, "opencode")
        self.assertNotEqual(env_a["XDG_CONFIG_HOME"], env_b["XDG_CONFIG_HOME"])
        config_a = context.read_jsonc(env_a["OPENCODE_CONFIG"])
        config_b = context.read_jsonc(env_b["OPENCODE_CONFIG"])
        self.assertEqual(set(config_a["mcp"]), {"base", "extra"})
        self.assertEqual(set(config_b["mcp"]), {"base"})
        self.assertNotIn("/unwanted.md", config_a["instructions"])
        self.assertEqual(config_a["permission"], "allow")
        self.assertEqual(shared.read_bytes(), original)
        env_k, args = context.prepare("cccccccc", context.resolve(None, "kiro"), "kiro")
        home = Path(env_k["KIRO_HOME"])
        self.assertEqual(args, ["--agent", "fernando"])
        self.assertFalse(
            context.read_jsonc(home / "agents/fernando.json")["includeMcpJson"]
        )
        self.assertTrue(
            context.read_jsonc(home / "settings/cli.json")[
                "chat.disableInheritingDefaultResources"
            ]
        )

    def test_group_and_context_exist_before_start_thread(self):
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager.sessions = {}
        manager._lock = threading.Lock()
        manager._on_status_change = None
        with patch.object(acp.threading, "Thread") as thread:

            def start():
                session = next(iter(manager.sessions.values()))
                self.assertEqual(
                    groups.get_session_groups()["chat:" + session.id], self.group["id"]
                )
                self.assertEqual(
                    list(session.context_snapshot["servers"]), ["base", "extra"]
                )

            thread.return_value.start.side_effect = start
            manager.create_session(group_id=self.group["id"])

    def test_snapshot_survives_save_restore_archive_and_wake(self):
        manager = acp.ACPManager.__new__(acp.ACPManager)
        manager.sessions = {}
        manager._lock = threading.Lock()
        manager._on_status_change = None
        session = acp.ACPSession("aaaaaaaa", backend="opencode")
        session.acp_session_id = "ses_fixture"
        session.context_snapshot = context.resolve(self.group["id"], "opencode")
        manager.sessions[session.id] = session
        manager._save()
        original = copy.deepcopy(session.context_snapshot)
        context.save_config({**self.config, "documents": {}, "templates": {}})
        manager.sessions = {}
        with (
            patch.object(manager, "_recover_orphans"),
            patch.object(acp, "_pop_continuation", return_value=None),
            patch.object(acp.ACPSession, "_load_history"),
        ):
            manager.restore_sessions(lambda sid: None)
        restored = manager.sessions["aaaaaaaa"]
        self.assertEqual(restored.context_snapshot, original)
        with patch.object(acp.threading, "Thread") as thread:
            self.assertTrue(manager.reload_session(restored.id))
            self.assertIs(thread.call_args.kwargs["args"][1], restored)
        manager.archive_session(restored.id)
        with (
            patch.object(manager, "_recover_orphans"),
            patch.object(acp.threading, "Thread"),
        ):
            manager.restore_session(restored.id)
        self.assertEqual(manager.sessions[restored.id].context_snapshot, original)


@unittest.skipUnless(
    os.environ.get("RUN_CONTEXT_INTEGRATION") == "1",
    "Explicit native configuration check",
)
class NativeContextTests(ContextFixture, unittest.TestCase):
    def test_installed_harnesses_read_isolated_configuration(self):
        snapshot = context.resolve(None, "opencode")
        snapshot["servers"] = {}
        env, _ = context.prepare("dddddddd", snapshot, "opencode")
        result = subprocess.run(
            [acp.OPENCODE_CLI, "debug", "config"],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(context.ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config.get("mcp"), {})
        self.assertEqual(
            config["instructions"], [str(context.RUNTIME / "dddddddd/steering.md")]
        )
        env, _ = context.prepare("eeeeeeee", context.resolve(None, "kiro"), "kiro")
        result = subprocess.run(
            [acp.KIRO_CLI, "settings", "chat.disableInheritingDefaultResources"],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().startswith("true"), result.stdout)


if __name__ == "__main__":
    unittest.main()
