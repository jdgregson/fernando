"""MCP Client service — spawns MCP servers and calls tools on behalf of the user."""

import asyncio
import json
import logging
import os
import threading
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

MCP_CONFIG_PATH = os.path.expanduser("~/.kiro/settings/mcp.json")
_tools_cache = {}  # server_name -> tools_list (in-memory)
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mcp_tools_cache.json")


def _load_disk_cache():
    """Load cached tools from disk."""
    global _tools_cache
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE) as f:
                _tools_cache = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load MCP tools cache from disk: {e}")


def _save_disk_cache():
    """Persist tools cache to disk."""
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(_tools_cache, f)
    except Exception as e:
        logger.error(f"Failed to save MCP tools cache to disk: {e}")


# Load disk cache on import
_load_disk_cache()


def _load_server_configs():
    """Load MCP server configurations from mcp.json."""
    with open(MCP_CONFIG_PATH) as f:
        data = json.load(f)
    return data.get("mcpServers", {})


def list_servers():
    """Return list of configured MCP server names."""
    return list(_load_server_configs().keys())


async def _list_tools_from_server(name, config):
    """Connect to a single MCP server and list its tools."""
    params = StdioServerParameters(
        command=config["command"],
        args=config.get("args", []),
        env={**os.environ, **(config.get("env") or {})},
    )
    tools = []
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                for tool in result.tools:
                    tools.append({
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                        "server": name,
                    })
    except Exception as e:
        logger.error(f"Failed to list tools from MCP server '{name}': {e}")
    return tools


def list_tools(server=None, force_refresh=False):
    """Get tools for a specific server. Serves from disk cache instantly, refreshes in background."""
    configs = _load_server_configs()
    if server:
        if server not in configs:
            return []
        cached = _tools_cache.get(server)
        if cached and not force_refresh:
            # Serve cached, refresh in background
            threading.Thread(target=_refresh_server, args=(server, configs[server]), daemon=True).start()
            return cached
        # No cache — must fetch synchronously
        try:
            tools = asyncio.run(_list_tools_from_server(server, configs[server]))
            _tools_cache[server] = tools
            _save_disk_cache()
            return tools
        except Exception as e:
            logger.error(f"Failed to list tools for server '{server}': {e}")
            return cached or []
    else:
        all_tools = []
        for name in configs:
            cached = _tools_cache.get(name)
            if cached:
                all_tools.extend(cached)
            else:
                try:
                    tools = asyncio.run(_list_tools_from_server(name, configs[name]))
                    _tools_cache[name] = tools
                    all_tools.extend(tools)
                except Exception as e:
                    logger.error(f"Failed to list tools for server '{name}': {e}")
        _save_disk_cache()
        return all_tools


def _refresh_server(server_name, config):
    """Background refresh of a single server's tools."""
    try:
        tools = asyncio.run(_list_tools_from_server(server_name, config))
        if tools != _tools_cache.get(server_name):
            _tools_cache[server_name] = tools
            _save_disk_cache()
    except Exception as e:
        logger.error(f"Background refresh failed for '{server_name}': {e}")


async def _call_tool(server_name, tool_name, arguments):
    """Spawn an MCP server and call a specific tool."""
    configs = _load_server_configs()
    config = configs.get(server_name)
    if not config:
        return {"error": f"Unknown MCP server: {server_name}"}

    params = StdioServerParameters(
        command=config["command"],
        args=config.get("args", []),
        env={**os.environ, **(config.get("env") or {})},
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
