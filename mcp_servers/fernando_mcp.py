#!/usr/bin/env python3
import os
import sys

# Activate the project venv so transitive deps (Flask etc.) are available
# even when launched by a bare system python (e.g. from MCP config)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_venv_site = os.path.join(_project_root, "venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
if os.path.isdir(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)

sys.path.insert(0, _project_root)

import asyncio
import json
import subprocess
import time
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
    run_immediately,
    get_subagent_status,
    list_subagents,
    terminate_subagent,
)
from src.services import rag
from mcp.server import Server
from mcp.types import Tool, TextContent

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _save_continuation(continuation):
    """Save a continuation message for the calling chat session."""
    if not continuation:
        return
    session_id = None
    try:
        with open(os.path.join(_project_root, "data", "acp_pid_map.json")) as f:
            pid_map = json.load(f)
        pid = os.getpid()
        for _ in range(5):
            pid = os.popen(f"ps -o ppid= -p {pid}").read().strip()
            if not pid:
                break
            session_id = pid_map.get(pid)
            if session_id:
                break
    except Exception:
        pass
    with open(os.path.join(_project_root, "data", "pending_continuation.json"), "w") as f:
        json.dump({"message": continuation, "session_id": session_id}, f)


app = Server("fernando")


def create_subagent(
    task_id,
    task,
    additional_context="",
    context_path=None,
    at_schedule=None,
    cron_schedule=None,
):
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
        schedule_at(script_path, at_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "scheduled_at": at_schedule,
        }

    if cron_schedule:
        schedule_cron(script_path, cron_schedule)
        return {
            "task_id": task_id,
            "session_name": session_name,
            "workspace": workspace,
            "cron": cron_schedule,
        }

    run_immediately(session_name, instructions_file)
    return {"task_id": task_id, "session_name": session_name, "workspace": workspace}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="spawn_subagent",
            description="Spawn a subagent in a new tmux session to work on a delegated task. The subagent will save proof of work and communicate progress via JSON files.",
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
            description="Terminate a subagent tmux session",
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
            name="mutate",
            description="Restart Fernando to apply code changes. Returns immediately, then the restart triggers in the background within seconds. MCP server changes require the user to manually restart the Kiro CLI session. IMPORTANT: This call will kill the active conversation within seconds. Do NOT write any response after calling this tool. Put ALL follow-up information in the continuation message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "continuation": {
                        "type": "string",
                        "description": "Optional message to auto-send to all active chat sessions after restart completes, so the conversation can continue autonomously.",
                    },
                },
            },
        ),
        Tool(
            name="reboot",
            description="Reboot the host machine using 'sudo reboot'. This will terminate all sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "continuation": {
                        "type": "string",
                        "description": "Optional message to auto-send to all active chat sessions after restart completes, so the conversation can continue autonomously.",
                    },
                },
            },
        ),
        Tool(
            name="create_pdf",
            description="Create a PDF document from markdown-like content. Supports headings (# ## ###), bold (**text**), italic (*text*), bullet lists (- item), numbered lists (1. item), code blocks (```), horizontal rules (---), tables (| col | col |), and images (![alt](/path/to/image.png)). Returns the file path. Use microsoft_mail_send with attachment_path to email it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output file path (e.g. '/tmp/report.pdf')"},
                    "content": {"type": "string", "description": "Document content in markdown-like format"},
                    "title": {"type": "string", "description": "Document title (optional, shown as header)"},
                },
                "required": ["path", "content"],
            },
        ),
        Tool(
            name="create_docx",
            description="Create a Word (.docx) document from markdown-like content. Supports headings (# ## ###), bold (**text**), italic (*text*), bullet lists (- item), numbered lists (1. item), code blocks (```), tables (| col | col |), and images (![alt](/path/to/image.png)). Returns the file path. Use microsoft_mail_send with attachment_path to email it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output file path (e.g. '/tmp/report.docx')"},
                    "content": {"type": "string", "description": "Document content in markdown-like format"},
                    "title": {"type": "string", "description": "Document title (optional, shown as header)"},
                },
                "required": ["path", "content"],
            },
        ),
        Tool(
            name="search_conversations",
            description="Search past chat conversations using semantic/vector search (RAG). Returns matching snippets with session IDs. Use get_conversation to fetch full context for a specific session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_conversation",
            description="Retrieve the full conversation history for a specific chat session, as readable user/assistant turns. Use after search_conversations to get full context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The session ID to retrieve"},
                    "offset": {"type": "integer", "description": "Starting turn index (0-based, default 0)"},
                    "limit": {"type": "integer", "description": "Max number of turns to return (default: all)"},
                },
                "required": ["session_id"],
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
    elif name == "mutate":
        _save_continuation(arguments.get("continuation"))
        subprocess.Popen(
            [os.path.join(_project_root, "scripts", "mutate.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result = {
            "status": "restart_initiated",
            "message": "Fernando restart initiated. This process will be terminated as part of the restart. The conversation will resume via the continuation message.",
        }
    elif name == "reboot":
        _save_continuation(arguments.get("continuation"))
        try:
            api_key = ""
            try:
                with open("/tmp/fernando-api-key") as f:
                    api_key = f.read().strip()
            except Exception:
                pass
            subprocess.run(["curl", "-s", "-X", "POST", "-H", f"X-API-Key: {api_key}", "http://localhost:5000/api/mutating"], timeout=2)
        except Exception:
            pass
        subprocess.Popen(
            ["nohup", "bash", "-c", "sleep 5 && sudo reboot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        result = {"status": "rebooting", "message": "Host will reboot in 5 seconds."}
    elif name == "create_pdf":
        from docgen import create_pdf
        out = create_pdf(arguments["path"], arguments["content"], arguments.get("title"))
        result = {"status": "created", "path": out}
    elif name == "create_docx":
        from docgen import create_docx
        out = create_docx(arguments["path"], arguments["content"], arguments.get("title"))
        result = {"status": "created", "path": out}
    elif name == "search_conversations":
        result = rag.search(arguments["query"], limit=arguments.get("limit", 5))
    elif name == "get_conversation":
        conv = rag.get_conversation(arguments["session_id"], offset=arguments.get("offset", 0), limit=arguments.get("limit"))
        result = conv if conv is not None else {"error": "Session history not found"}
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
