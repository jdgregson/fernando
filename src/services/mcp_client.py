"""MCP Client service — spawns MCP servers and calls tools on behalf of the user."""

import asyncio
import json
import logging
import os
import sys
import threading
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

MCP_CONFIG_PATH = os.path.expanduser("~/.kiro/settings/mcp.json")
OPENCODE_CONFIG_PATH = os.path.expanduser("~/.config/opencode/opencode.jsonc")
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp_servers")

# Registry of Fernando-bundled MCP servers.
# "default_enabled" means setup will enable them on fresh install.
BUNDLED_SERVERS = {
    "desktop": {
        "description": "Kasm desktop automation (screenshots, mouse, keyboard, browser)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "desktop_mcp.py")],
        "default_enabled": True,
    },
    "subagents": {
        "description": "Subagent orchestration (spawn, status, list, terminate)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "subagents_mcp.py")],
        "default_enabled": True,
    },
    "system": {
        "description": "System & process control (run_command, run_steps, run_daemon, authorize, mutate, reboot)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "system_mcp.py")],
        "default_enabled": True,
    },
    "web": {
        "description": "Web search & fetch (fetch, brave/bing search, brave answers)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "web_mcp.py")],
        "default_enabled": True,
    },
    "notes": {
        "description": "SilverBullet notebooks & notes",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "notes_mcp.py")],
        "default_enabled": True,
    },
    "jupyter": {
        "description": "Jupyter integration (list, read, execute, live cell ops)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "jupyter_mcp.py")],
        "default_enabled": True,
    },
    "automation": {
        "description": "Inbound email automation rules",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "automation_mcp.py")],
        "default_enabled": True,
    },
    "docs": {
        "description": "Document generation (PDF, DOCX)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "docs_mcp.py")],
        "default_enabled": True,
    },
    "chat": {
        "description": "Chat/session utilities (scheduled resumes, set_chat_name, save_memory, attach_file, live_canvas, conversation search)",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "chat_mcp.py")],
        "default_enabled": True,
    },
    "microsoft": {
        "description": "Microsoft 365: mail, calendar, contacts, OneDrive, OneNote, To Do",
        "command": "python3",
        "args": [os.path.join(_MCP_DIR, "microsoft_mcp.py")],
        "default_enabled": True,
    },
    "govee": {
        "description": "Govee smart home device control (lights, sensors)",
        "command": "python3",
        "args": [os.path.expanduser("~/projects/govee-mcp/govee_mcp.py")],
        "default_enabled": False,
    },
}

_discovery_slots = threading.BoundedSemaphore(4)


def _load_server_configs():
    """Registered servers remain callable even when not globally injected."""
    from src.services.context_templates import get_config, server_config
    return {name: server_config(item, 'kiro') for name, item in get_config()['servers'].items()}


def list_servers():
    """Return list of configured MCP server names."""
    return list(_load_server_configs().keys())


def list_bundled_servers():
    """Return registered servers and their global injection state."""
    from src.services.context_templates import get_config
    return [{'name': name, 'description': item.get('description', name),
             'enabled': item.get('global', False)}
            for name, item in get_config()['servers'].items()]


def set_server_enabled(name, enabled):
    """Change Fernando defaults without editing either harness's shared config."""
    from src.services.context_templates import get_config, save_config
    config = get_config()
    if name not in config['servers']:
        return {'error': f'Unknown server: {name}'}
    config['servers'][name]['global'] = bool(enabled)
    save_config(config)
    return {"ok": True, "name": name, "enabled": enabled}


async def _list_tools_from_server(name, config):
    """Connect to a single MCP server and list its tools."""
    params = StdioServerParameters(
        command=config["command"],
        args=config.get("args", []),
        env={**os.environ, 'PATH': os.path.dirname(sys.executable) + os.pathsep + os.environ.get('PATH', ''), **(config.get("env") or {})},
    )
    tools = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cursor = None
            while True:
                result = await session.list_tools(cursor=cursor)
                for tool in result.tools:
                    tools.append({
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                        "server": name,
                    })
                cursor = result.nextCursor
                if not cursor:
                    break
    return tools


def list_tools(server=None):
    configs = _load_server_configs()
    if server is not None and server not in configs:
        raise ValueError('Unknown MCP server')
    tools = []
    for name in ([server] if server is not None else configs):
        with _discovery_slots:
            tools.extend(asyncio.run(asyncio.wait_for(_list_tools_from_server(name, configs[name]), timeout=30)))
    return tools


async def _call_tool(server_name, tool_name, arguments):
    """Spawn an MCP server and call a specific tool."""
    configs = _load_server_configs()
    config = configs.get(server_name)
    if not config:
        return {"error": f"Unknown MCP server: {server_name}"}

    params = StdioServerParameters(
        command=config["command"],
        args=config.get("args", []),
        env={**os.environ, 'PATH': os.path.dirname(sys.executable) + os.pathsep + os.environ.get('PATH', ''), **(config.get("env") or {})},
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                # Serialize the result
                content = []
                for item in result.content:
                    if hasattr(item, "text"):
                        content.append({"type": "text", "text": item.text})
                    elif hasattr(item, "data"):
                        content.append({"type": "image", "data": item.data, "mimeType": getattr(item, "mimeType", "image/png")})
                    else:
                        content.append({"type": "text", "text": str(item)})
                return {"content": content, "isError": result.isError if hasattr(result, "isError") else False}
    except Exception as e:
        logger.error(f"Failed to call tool '{tool_name}' on server '{server_name}': {e}")
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}


def call_tool(server_name, tool_name, arguments):
    """Synchronous wrapper to call a tool on an MCP server."""
    return asyncio.run(_call_tool(server_name, tool_name, arguments))
