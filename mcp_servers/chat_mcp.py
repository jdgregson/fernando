#!/usr/bin/env python3
"""Fernando MCP server: chat/session utilities.

Tools: set_chat_name, save_memory, attach_file, live_canvas,
       search_conversations, get_conversation
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)
from _mcp_common import PROJECT_ROOT, read_api_key, find_my_session_id

import asyncio
import json
import os
import subprocess
import urllib.request

from src.services import rag
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("chat")


def _set_chat_name(name):
    """Rename the current chat session (ACP or tmux)."""
    # Try ACP session first
    session_id = find_my_session_id()
    if session_id:
        try:
            api_key = read_api_key()
            req = urllib.request.Request(
                f"http://localhost:5000/api/rename_chat",
                data=json.dumps({"session_id": session_id, "name": name}).encode(),
                headers={"Content-Type": "application/json", "X-API-Key": api_key},
            )
            urllib.request.urlopen(req, timeout=5)
            return {"status": "renamed", "type": "acp", "session_id": session_id, "name": name}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    # Fall back to tmux session rename
    try:
        current = subprocess.check_output(["tmux", "display-message", "-p", "#S"], text=True).strip()
        if current:
            subprocess.check_call(["tmux", "rename-session", "-t", current, name])
            return {"status": "renamed", "type": "tmux", "old_name": current, "name": name}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    return {"status": "error", "error": "Could not determine session"}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="set_chat_name",
            description="Set the display name of the current chat session in the sidebar. Use on your first turn to name the conversation based on what the user asked. Use lowercase words separated by dashes (e.g. 'debug-lambda-function').",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The new display name for this chat session"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="save_memory",
            description="Save a persistent memory/preference that will be automatically injected into your context on every future conversation. Use this when you learn something about the user's preferences, workflows, or conventions that should persist across sessions. Memories are stored in ~/.kiro/steering/memory.md.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory": {"type": "string", "description": "The memory to save (e.g. 'Jonathan prefers camelCase for JS variables', 'The production database is in us-west-2')"},
                },
                "required": ["memory"],
            },
        ),
        Tool(
            name="attach_file",
            description="Attach a file to the current ACP chat conversation. The file is copied to a persistent cache so it remains downloadable even if the original is deleted. Only files from allowed paths (~/Documents, ~/Downloads, ~/Desktop, ~/uploads, /tmp, and fernando data dirs) can be attached.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file to attach"},
                    "name": {"type": "string", "description": "Display name for the file (optional, defaults to filename)"},
                },
                "required": ["path"],
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
        Tool(
            name="live_canvas",
            description="Render HTML/CSS/JS in the chat as a live interactive canvas. Use for visualizations, formatted code display, diagrams, or anything that benefits from rendered HTML. The content is displayed inline in the chat via a sandboxed iframe.",
            inputSchema={
                "type": "object",
                "properties": {
                    "html": {"type": "string", "description": "Complete HTML document to render (include <!DOCTYPE html>, <style>, <script> as needed)"},
                },
                "required": ["html"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "set_chat_name":
        result = _set_chat_name(arguments["name"])
    elif name == "save_memory":
        memory_path = os.path.expanduser("~/.kiro/steering/memory.md")
        memory_text = arguments["memory"].strip()
        if not os.path.exists(memory_path):
            with open(memory_path, "w") as f:
                f.write("# Memories\n\nPersistent memories saved by Fernando across conversations.\n\n")
        with open(memory_path, "a") as f:
            f.write(f"- {memory_text}\n")
        result = {"status": "saved", "path": memory_path, "memory": memory_text}
    elif name == "attach_file":
        import hashlib, shutil
        file_path = os.path.realpath(arguments["path"])
        display_name = arguments.get("name") or os.path.basename(file_path)
        home = os.path.realpath(os.path.expanduser("~"))
        allowed = [os.path.join(home, d) for d in ("Documents", "Downloads", "Desktop", "uploads", "fernando/data/desktop", "fernando/data/image_cache", "fernando/data/file_cache")]
        allowed.append("/tmp")
        if not any(file_path.startswith(d + "/") or file_path == d for d in allowed):
            result = {"error": f"Path not allowed: {file_path}"}
        elif not os.path.isfile(file_path):
            result = {"error": f"File not found: {file_path}"}
        else:
            session_id = find_my_session_id() or "unknown"
            cache_dir = os.path.join(PROJECT_ROOT, "data", "file_cache", session_id)
            os.makedirs(cache_dir, exist_ok=True)
            ext = os.path.splitext(file_path)[1]
            file_hash = hashlib.sha256(file_path.encode()).hexdigest()[:16]
            cached_name = file_hash + ext
            cached_path = os.path.join(cache_dir, cached_name)
            shutil.copy2(file_path, cached_path)
            # Return path relative to home for the /api/files/ route
            rel_path = os.path.relpath(cached_path, home)
            result = {"status": "attached", "name": display_name, "path": cached_path, "url_path": rel_path}
    elif name == "search_conversations":
        result = rag.search(arguments["query"], limit=arguments.get("limit", 5))
    elif name == "get_conversation":
        conv = rag.get_conversation(arguments["session_id"], offset=arguments.get("offset", 0), limit=arguments.get("limit"))
        result = conv if conv is not None else {"error": "Session history not found"}
    elif name == "live_canvas":
        html = arguments["html"]
        return [TextContent(type="text", text=f"IMPORTANT: To render this canvas, you MUST include the following code block verbatim in your response message (not in a tool call). Copy it exactly:\n\n```html-canvas\n{html}\n```")]
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
