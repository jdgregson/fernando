#!/usr/bin/env python3
"""Fernando MCP server: subagent orchestration.

Tools: spawn_subagent, get_subagent_status, list_subagents, terminate_subagent
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)
from _mcp_common import PROJECT_ROOT, read_api_key

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

app = Server("subagents")


def create_subagent(
    task_id,
    task,
    additional_context="",
    context_path=None,
    at_schedule=None,
    cron_schedule=None,
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
        _write_acp_spawn_script(script_path, instructions_file, session_name)
        schedule_at(script_path, at_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "scheduled_at": at_schedule,
        }

    if cron_schedule:
        _write_acp_spawn_script(script_path, instructions_file, session_name)
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
    payload = json.dumps({"task": prompt, "name": session_name}).encode()
    req = urllib.request.Request(
        "http://localhost:5000/api/spawn_subagent",
        data=payload,
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


def _write_acp_spawn_script(script_path, instructions_file, session_name):
    """Overwrite spawn.sh to use ACP API instead of tmux."""
    os.chmod(script_path, 0o700)
    with open(script_path, "w") as f:
        f.write(f"""#!/bin/bash
API_KEY=$(cat /tmp/fernando-api-key 2>/dev/null)
TASK="Read the instructions from {instructions_file} and execute the task described there."
curl -s -X POST http://localhost:5000/api/spawn_subagent \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $API_KEY" \\
  --data-raw "{{\\"task\\": \\"$TASK\\", \\"name\\": \\"{session_name}\\"}}"
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
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "spawn_subagent":
        result = create_subagent(
            arguments["task_id"],
            arguments["task"],
            arguments.get("additional_context", ""),
            arguments.get("context_path"),
            arguments.get("at_schedule"),
            arguments.get("cron_schedule"),
        )
    elif name == "get_subagent_status":
        result = get_subagent_status(arguments["task_id"])
    elif name == "list_subagents":
        result = list_subagents()
    elif name == "terminate_subagent":
        result = terminate_subagent(arguments["task_id"])
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
