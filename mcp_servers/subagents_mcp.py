#!/usr/bin/env python3
"""Fernando MCP server: subagent orchestration.

Tools: spawn_subagent, get_subagent_status, list_subagents, terminate_subagent
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)
from _mcp_common import PROJECT_ROOT, read_api_key, find_my_session_id

import asyncio
import json
import urllib.request

from src.services.subagent_core import (
    create_workspace,
    resolve_context_path,
    write_task_json,
    write_status_json,
    write_instructions,
    write_spawn_script,
    schedule_at,
    schedule_cron,
    get_subagent_status,
    list_subagents,
    terminate_subagent,
)
from mcp.server import Server
from mcp.types import Tool, TextContent

import os

AVAILABLE_MODELS = []
DEFAULT_MODEL = "claude-opus-4.6"

def _load_available_models():
    """Read cached model list from disk (written by Flask at startup)."""
    global AVAILABLE_MODELS, DEFAULT_MODEL
    models_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "available_models.json")
    try:
        with open(models_file) as f:
            data = json.load(f)
        AVAILABLE_MODELS = [m["model_id"] for m in data.get("models", [])]
        DEFAULT_MODEL = data.get("default_model", DEFAULT_MODEL)
    except (OSError, json.JSONDecodeError, KeyError):
        pass

_load_available_models()

app = Server("subagents")


def create_subagent(
    task_id,
    task,
    additional_context="",
    context_path=None,
    at_schedule=None,
    cron_schedule=None,
    model=None,
    group_id=None,
    parent_session_id=None,
):
    """Spawn a subagent with full workspace/instructions, using ACP instead of tmux."""
    task_id, workspace = create_workspace(task_id)
    context_file = resolve_context_path(context_path)
    session_name = f"subagent-{task_id}"

    schedule = at_schedule or cron_schedule
    write_task_json(
        workspace, task_id, task, context_file, additional_context, schedule=schedule
    )
    write_status_json(workspace, scheduled=bool(schedule))
    instructions_file = write_instructions(
        workspace, task_id, task, context_file, additional_context
    )
    script_path = write_spawn_script(workspace, session_name, instructions_file)

    if at_schedule:
        # Rewrite spawn.sh to use ACP API instead of tmux
        _write_acp_spawn_script(script_path, instructions_file, session_name, model, group_id, parent_session_id)
        schedule_at(script_path, at_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "scheduled_at": at_schedule,
        }

    if cron_schedule:
        _write_acp_spawn_script(script_path, instructions_file, session_name, model, group_id, parent_session_id)
        schedule_cron(script_path, cron_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "cron": cron_schedule,
        }

    # Immediate: spawn via ACP API
    prompt = f"Read the instructions from {instructions_file} and execute the task described there."
    api_key = read_api_key()
    payload = {"task": prompt, "name": session_name}
    if model:
        payload["model"] = model
    if group_id:
        payload["group_id"] = group_id
    if parent_session_id:
        payload["parent_session_id"] = parent_session_id
    req = urllib.request.Request(
        "http://localhost:5000/api/spawn_subagent",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        result["task_id"] = task_id
        result["workspace"] = workspace
        return result
    except Exception as e:
        return {"error": str(e), "task_id": task_id, "workspace": workspace}


def _write_acp_spawn_script(script_path, instructions_file, session_name, model=None, group_id=None, parent_session_id=None):
    """Overwrite spawn.sh to use ACP API instead of tmux."""
    os.chmod(script_path, 0o700)
    payload_fields = f'\\"task\\": \\"$TASK\\", \\"name\\": \\"{session_name}\\"'
    if model:
        payload_fields += f', \\"model\\": \\"{model}\\"'
    if group_id:
        payload_fields += f', \\"group_id\\": \\"{group_id}\\"'
    if parent_session_id:
        payload_fields += f', \\"parent_session_id\\": \\"{parent_session_id}\\"'
    with open(script_path, "w") as f:
        f.write(f"""#!/bin/bash
API_KEY=$(cat /tmp/fernando-api-key 2>/dev/null)
TASK="Read the instructions from {instructions_file} and execute the task described there."
curl -s -X POST http://localhost:5000/api/spawn_subagent \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $API_KEY" \\
  --data-raw "{{{payload_fields}}}"
""")
    os.chmod(script_path, 0o500)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="spawn_subagent",
            description="Spawn a subagent in a new ACP chat session to work on a delegated task. The subagent will save proof of work and communicate progress via JSON files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique identifier for this task (e.g., 'research-aws-pricing', 'debug-issue-123')",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description for the subagent",
                    },
                    "additional_context": {
                        "type": "string",
                        "description": "Optional additional context or instructions",
                    },
                    "context_path": {
                        "type": "string",
                        "description": "Optional path to a context file that will be read at task start and updated with progress. File will be created if it doesn't exist.",
                    },
                    "at_schedule": {
                        "type": "string",
                        "description": "Run at specific time using 'at' command (e.g., '14:30', 'now + 1 hour'). Mutually exclusive with cron_schedule.",
                    },
                    "cron_schedule": {
                        "type": "string",
                        "description": "Run on cron schedule (e.g., '*/5 * * * *' for every 5 minutes, '0 * * * *' for hourly). Mutually exclusive with at_schedule.",
                    },
                    "model": {
                        "type": "string",
                        "description": f"Model ID for the subagent (default: {DEFAULT_MODEL})",
                        "enum": AVAILABLE_MODELS,
                    },
                    "group_id": {
                        "type": "string",
                        "description": "Optional group ID to place the subagent in. If not provided, subagent will be ungrouped.",
                    },
                },
                "required": ["task_id", "task"],
            },
        ),
        Tool(
            name="get_subagent_status",
            description="Check the status and progress of a subagent task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to check"}
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="list_subagents",
            description="List all subagent tasks and their current status",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="terminate_subagent",
            description="Terminate a subagent ACP chat session",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to terminate",
                    }
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="message_parent",
            description="Send a message to the agent that spawned you. The message is queued for the parent to read. Does NOT end your turn - you can continue working. Fails if you weren't spawned by another agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to send to your parent agent",
                    },
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="message_child",
            description="Send a message to one of your subagents. The message is delivered as a continuation, interrupting the child's current work. Fails if the target session is not your child.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID of the child to message (8-character hex ID)",
                    },
                    "message": {
                        "type": "string",
                        "description": "The message to send to the child agent",
                    },
                },
                "required": ["session_id", "message"],
            },
        ),
        Tool(
            name="read_child_messages",
            description="Read all unread messages from your subagents. Returns a list of messages with sender session IDs, timestamps, and content. Messages are marked as read after retrieval.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "spawn_subagent":
        # Check if caller is a subagent and if spawning is allowed
        my_session = find_my_session_id()
        if my_session:
            from src.services.acp import get_parent
            from src.services.settings import get as get_setting
            if get_parent(my_session) and not get_setting("subagents_can_spawn"):
                return [TextContent(type="text", text=json.dumps({
                    "error": "Subagents cannot spawn other subagents (subagents_can_spawn setting is disabled)"
                }))]
        model = arguments.get("model")
        if model and AVAILABLE_MODELS and model not in AVAILABLE_MODELS:
            return [TextContent(type="text", text=json.dumps({
                "error": f"Invalid model '{model}'. Available: {', '.join(AVAILABLE_MODELS)}"
            }))]
        result = create_subagent(
            arguments["task_id"],
            arguments["task"],
            arguments.get("additional_context", ""),
            arguments.get("context_path"),
            arguments.get("at_schedule"),
            arguments.get("cron_schedule"),
            arguments.get("model"),
            arguments.get("group_id"),
            parent_session_id=my_session,  # Record who spawned this agent
        )
    elif name == "get_subagent_status":
        result = get_subagent_status(arguments["task_id"])
    elif name == "list_subagents":
        result = list_subagents()
    elif name == "terminate_subagent":
        result = terminate_subagent(arguments["task_id"])
    elif name == "message_parent":
        my_session = find_my_session_id()
        if not my_session:
            result = {"error": "Could not determine your session ID"}
        else:
            from src.services.acp import get_parent, queue_child_message
            parent_id = get_parent(my_session)
            if not parent_id:
                result = {"error": "You were not spawned by another agent (no parent)"}
            else:
                msg_id = queue_child_message(parent_id, my_session, arguments["message"])
                # Wake parent via agent_message API
                api_key = read_api_key()
                wake_msg = f"[Subagent {my_session} sent you a message. Use read_child_messages() to read it.]"
                req = urllib.request.Request(
                    "http://localhost:5000/api/acp/agent_message",
                    data=json.dumps({"session_id": parent_id, "from_session": my_session, "message": wake_msg}).encode(),
                    headers={"Content-Type": "application/json", "X-API-Key": api_key},
                )
                try:
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass  # Parent may be busy, that's OK - message is queued
                result = {"ok": True, "message_id": msg_id, "parent_session": parent_id}
    elif name == "message_child":
        my_session = find_my_session_id()
        if not my_session:
            result = {"error": "Could not determine your session ID"}
        else:
            from src.services.acp import is_child_of
            child_id = arguments["session_id"]
            if not is_child_of(child_id, my_session):
                result = {"error": f"Session {child_id} is not your child"}
            else:
                api_key = read_api_key()
                req = urllib.request.Request(
                    "http://localhost:5000/api/acp/agent_message",
                    data=json.dumps({"session_id": child_id, "from_session": my_session, "message": arguments["message"]}).encode(),
                    headers={"Content-Type": "application/json", "X-API-Key": api_key},
                )
                try:
                    resp = urllib.request.urlopen(req, timeout=10)
                    result = json.loads(resp.read())
                    if result.get("ok"):
                        result["child_session"] = child_id
                except urllib.error.HTTPError as e:
                    body = e.read().decode() if e.fp else ""
                    try:
                        result = json.loads(body)
                    except (json.JSONDecodeError, ValueError):
                        result = {"error": f"HTTP {e.code}: {body}"}
                except Exception as e:
                    result = {"error": str(e)}
    elif name == "read_child_messages":
        my_session = find_my_session_id()
        if not my_session:
            result = {"error": "Could not determine your session ID"}
        else:
            from src.services.acp import get_unread_child_messages
            messages = get_unread_child_messages(my_session)
            result = {"messages": messages, "count": len(messages)}
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
