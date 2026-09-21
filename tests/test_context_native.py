"""Opt-in real ACP tests: local fake OpenCode model; explicitly opted-in Kiro turn."""

import copy
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from src.services import acp, chat_history, context_templates as context, groups


def configure_groups(root, stack):
    """Exercise the real resolver without reading or editing live group settings."""
    stack.enter_context(patch.object(context, "STORE", root / "context.json"))
    stack.enter_context(patch.object(groups, "GROUPS_FILE", str(root / "groups.json")))
    config = {"revision": 1, "documents": {}, "servers": {}, "templates": {}}
    for name in ("alpha", "beta"):
        path = root / (name + ".md")
        path.write_text("STEERING_MARKER_" + name)
        config["documents"][name] = {"name": name, "path": str(path), "global": False}
        config["servers"][name] = {
            "global": False,
            "kiro": {
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("context_mcp_fixture.py")), name],
            },
        }
        config["templates"][name] = {
            "name": name,
            "documents": [name],
            "servers": [name],
        }
    context._write(context.STORE, config)
    context._write(
        groups.GROUPS_FILE,
        {
            "groups": [
                {"id": name, "name": name, "template_ids": [name]}
                for name in ("alpha", "beta")
            ],
            "session_groups": {
                "chat:aaaaaaaa": "alpha",
                "chat:bbbbbbbb": "beta",
                "chat:cccccccc": "alpha",
            },
        },
    )


@unittest.skipUnless(
    os.environ.get("RUN_CONTEXT_INTEGRATION") == "1", "Explicit native ACP check"
)
class NativeACPContextTests(unittest.TestCase):
    def test_kiro_repeated_move_out_and_back(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            configure_groups(root, stack)
            stack.enter_context(patch.object(context, "RUNTIME", root / "runtime"))
            stack.enter_context(
                patch.object(chat_history, "HISTORY_DIR", root / "history")
            )
            stack.enter_context(patch.object(acp, "HISTORY_DIR", str(root / "history")))
            stack.enter_context(patch.object(acp.ACPSession, "_index_rag_background"))
            stack.enter_context(
                patch.dict(
                    os.environ, {"KIRO_TEST_SESSIONS_DIR": str(root / "sessions")}
                )
            )
            events = []
            session = acp.ACPSession(
                "cccccccc", on_event=lambda sid, event: events.append(event)
            )
            stack.callback(session.unload)
            session.start()
            session.ready = True
            native_id = session.acp_session_id
            manager = acp.ACPManager.__new__(acp.ACPManager)
            manager._lock = threading.Lock()
            manager.sessions = {session.id: session}
            manager._save = Mock()
            manager._save_pid_map = Mock()
            manager._broadcast_sessions_list = Mock()
            for destination in (None, "alpha", None, "alpha"):
                self.assertTrue(manager.move_chat_to_group(session.id, destination))
                deadline = time.time() + 20
                while session._reloading and time.time() < deadline:
                    time.sleep(0.1)
                self.assertTrue(session.ready, events)
                self.assertEqual(session.acp_session_id, native_id)
                response = session.execute_command("context")
                self.assertTrue(response.get("success"), response)
                mcp = [
                    g["name"]
                    for g in response["data"]["breakdown"]["tools"]["groups"]
                    if g["source"].startswith("mcp:")
                ]
                self.assertEqual(mcp, ["alpha"] if destination else [])
            self.assertFalse(
                any(e.get("type") in ("session_ended", "session_error") for e in events)
            )

    def test_two_opencode_chats_and_resume_have_distinct_tools_and_steering(self):
        received = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(body)
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if body.get("stream") else "application/json",
                )
                self.end_headers()
                if body.get("stream"):
                    for delta, finish in (
                        ({"role": "assistant", "content": "fixture answer"}, None),
                        ({}, "stop"),
                    ):
                        event = {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "fixture",
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": finish}
                            ],
                        }
                        self.wfile.write(
                            ("data: " + json.dumps(event) + "\n\n").encode()
                        )
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self.wfile.write(
                        json.dumps(
                            {
                                "id": "fixture",
                                "object": "chat.completion",
                                "created": 1,
                                "model": "fixture",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": "fixture answer",
                                        },
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                        ).encode()
                    )

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        self.addCleanup(provider.server_close)
        self.addCleanup(provider.shutdown)
        base = {
            "model": "fixture/fixture",
            "small_model": "fixture/fixture",
            "provider": {
                "fixture": {
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {
                        "baseURL": f"http://127.0.0.1:{provider.server_port}/v1",
                        "apiKey": "fixture",
                    },
                    "models": {
                        "fixture": {
                            "name": "fixture",
                            "limit": {"context": 200000, "output": 1024},
                        }
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            configure_groups(root, stack)
            stack.enter_context(patch.object(context, "RUNTIME", root / "runtime"))
            stack.enter_context(
                patch.object(context, "opencode_base", return_value=base)
            )
            stack.enter_context(
                patch.object(chat_history, "HISTORY_DIR", root / "history")
            )
            stack.enter_context(patch.object(acp, "HISTORY_DIR", str(root / "history")))
            stack.enter_context(patch.object(acp.ACPSession, "_index_rag_background"))
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {
                        "XDG_DATA_HOME": str(root / "data"),
                        "XDG_STATE_HOME": str(root / "state"),
                        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
                    },
                )
            )
            sessions = []

            def stop(session):
                process = session.proc
                session.unload()
                if process:
                    process.wait(timeout=10)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()

            for sid, name in [("aaaaaaaa", "alpha"), ("bbbbbbbb", "beta")]:
                session = acp.ACPSession(sid, backend="opencode")
                session.model = "fixture/fixture"
                stack.callback(stop, session)
                session.start()
                session.ready = True
                sessions.append(session)
            for session, name, excluded in [
                (sessions[0], "alpha", "beta"),
                (sessions[1], "beta", "alpha"),
            ]:
                status = session.opencode_request("GET", "/mcp")
                self.assertEqual(set(status), {name}, status)
                self.assertEqual(status[name]["status"], "connected", status)
                session.send_prompt("fixture-" + name)
                deadline = time.time() + 30
                while session._is_prompting and time.time() < deadline:
                    time.sleep(0.1)
                self.assertFalse(session._is_prompting)
                requests = [
                    r
                    for r in received
                    if "fixture-" + name in json.dumps(r["messages"])
                ]
                self.assertTrue(requests)
                payload = json.dumps(requests[-1])
                self.assertIn("STEERING_MARKER_" + name, payload)
                self.assertNotIn("STEERING_MARKER_" + excluded, payload)
                self.assertIn(name + "_probe", payload)
                self.assertNotIn(excluded + "_probe", payload)
            original_history = copy.deepcopy(sessions[0].history)
            native_id = sessions[0].acp_session_id
            native_messages = sessions[0].opencode_request(
                "GET", f"/session/{native_id}/message"
            )
            (root / "beta.md").write_text("UPDATED_STEERING_beta")
            manager = acp.ACPManager.__new__(acp.ACPManager)
            manager._lock = threading.Lock()
            manager.sessions = {sessions[0].id: sessions[0]}
            manager._save = Mock()
            manager._save_pid_map = Mock()
            manager._broadcast_sessions_list = Mock()
            self.assertTrue(manager.move_chat_to_group(sessions[0].id, "beta"))
            deadline = time.time() + 30
            while not sessions[0].ready and time.time() < deadline:
                time.sleep(0.1)
            self.assertTrue(
                sessions[0].ready, "Group move did not automatically wake the chat"
            )
            self.assertEqual(sessions[0].acp_session_id, native_id)
            # Startup metadata may append notifications; prior conversation stays intact.
            self.assertEqual(
                sessions[0].history[: len(original_history)], original_history
            )
            resumed_messages = sessions[0].opencode_request(
                "GET", f"/session/{native_id}/message"
            )
            self.assertEqual(
                [m["info"]["id"] for m in resumed_messages],
                [m["info"]["id"] for m in native_messages],
            )
            self.assertEqual(set(sessions[0].opencode_request("GET", "/mcp")), {"beta"})
            sessions[0].ready = True
            sessions[0].send_prompt("after-group-move")
            deadline = time.time() + 30
            while sessions[0]._is_prompting and time.time() < deadline:
                time.sleep(0.1)
            self.assertFalse(sessions[0]._is_prompting)
            resumed_requests = [
                r for r in received if "after-group-move" in json.dumps(r["messages"])
            ]
            self.assertTrue(resumed_requests)
            request = resumed_requests[-1]
            self.assertIn("fixture-alpha", json.dumps(request["messages"]))
            self.assertIn("fixture answer", json.dumps(request["messages"]))
            self.assertIn("UPDATED_STEERING_beta", json.dumps(request["messages"]))
            tools = json.dumps(request.get("tools"))
            self.assertIn("beta_probe", tools)
            self.assertNotIn("alpha_probe", tools)

    @unittest.skipUnless(
        os.environ.get("RUN_KIRO_HISTORY_INTEGRATION") == "1",
        "Explicit live Kiro history check (one short model turn)",
    )
    def test_kiro_profile_is_selected_and_shared_mcps_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            configure_groups(root, stack)
            stack.enter_context(patch.object(context, "RUNTIME", root / "runtime"))
            stack.enter_context(
                patch.object(chat_history, "HISTORY_DIR", root / "history")
            )
            stack.enter_context(patch.object(acp, "HISTORY_DIR", str(root / "history")))
            stack.enter_context(patch.object(acp.ACPSession, "_index_rag_background"))
            # Use a temporary native session store, preserving real authentication.
            stack.enter_context(
                patch.dict(
                    os.environ, {"KIRO_TEST_SESSIONS_DIR": str(root / "sessions")}
                )
            )
            session = acp.ACPSession("cccccccc", backend="kiro")

            def stop():
                process = session.proc
                session.unload()
                if process:
                    process.wait(timeout=10)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()

            stack.callback(stop)
            session.start()
            session.ready = True
            session.send_prompt("Remember user-history-marker for this test.")
            deadline = time.time() + 20
            while session._is_prompting and time.time() < deadline:
                time.sleep(0.1)
            self.assertFalse(session._is_prompting)
            self.assertTrue(
                any(
                    e.get("params", {}).get("update", {}).get("sessionUpdate")
                    == "agent_message_chunk"
                    for e in session.history
                )
            )
            original_history = copy.deepcopy(session.history)
            result = session.execute_command("mcp")
            self.assertTrue(result and result.get("success"), result)
            payload = json.dumps(result)
            self.assertIn("alpha", payload)
            self.assertNotIn("microsoft", payload)
            self.assertNotIn("chat_mcp", payload)
            resources = session.execute_command("context")
            self.assertTrue(resources.get("success"), resources)
            files = resources["data"]["breakdown"]["contextFiles"]["items"]
            self.assertEqual(
                [f["name"] for f in files], [str(root / "runtime/cccccccc/steering.md")]
            )
            native_id = session.acp_session_id
            stop()
            session.load(native_id)
            self.assertEqual(session.history[: len(original_history)], original_history)
            resumed = session.execute_command("mcp")
            self.assertTrue(resumed and resumed.get("success"), resumed)
            self.assertIn("alpha", json.dumps(resumed))
            self.assertNotIn("chat_mcp", json.dumps(resumed))
            # Moving groups must override the saved profile on ordinary wake.
            stop()
            groups.move_session_to_group("chat:cccccccc", "beta")
            (root / "beta.md").write_text("UPDATED_STEERING_beta")
            session.load(native_id)
            self.assertEqual(session.acp_session_id, native_id)
            self.assertIn(
                "UPDATED_STEERING_beta",
                (root / "runtime/cccccccc/steering.md").read_text(),
            )
            changed = session.execute_command("context")
            self.assertGreater(changed["data"]["breakdown"]["yourPrompts"]["tokens"], 0)
            self.assertGreater(
                changed["data"]["breakdown"]["kiroResponses"]["tokens"], 0
            )
            self.assertEqual(session.history[: len(original_history)], original_history)
            mcp_groups = [
                g["name"]
                for g in changed["data"]["breakdown"]["tools"]["groups"]
                if g["source"].startswith("mcp:")
            ]
            self.assertEqual(mcp_groups, ["beta"])
