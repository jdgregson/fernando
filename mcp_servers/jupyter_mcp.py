#!/usr/bin/env python3
"""Fernando MCP server: Jupyter integration.

Tools: jupyter_list, jupyter_read, jupyter_execute, jupyter_insert_and_run,
       jupyter_run_cell, jupyter_run_all, jupyter_edit_cell
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)
from _mcp_common import read_api_key

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("jupyter")

_JUPYTER_PORT = 9999
_JUPYTER_API = f"http://127.0.0.1:{_JUPYTER_PORT}"


def _jupyter_ensure_running():
    """Start Jupyter if not running."""
    try:
        req = urllib.request.Request(f"{_JUPYTER_API}/api/kernels", method="GET")
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:
        pass
    # Trigger start via Flask proxy
    try:
        api_key = read_api_key()
        req = urllib.request.Request(f"http://localhost:5000/jupyter/?api_key={api_key}")
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception:
        return False


def _jupyter_api(path, method="GET", data=None):
    """Make a request to the Jupyter REST API."""
    _jupyter_ensure_running()
    url = f"{_JUPYTER_API}/{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def _jupyter_list_notebooks():
    """List all .ipynb files."""
    result = _jupyter_api("api/contents/")
    if "error" in result:
        return result
    items = result.get("content", [])
    notebooks = []
    def walk(items, prefix=""):
        for item in items:
            if item["type"] == "notebook":
                notebooks.append({"name": item["name"], "path": item["path"]})
            elif item["type"] == "directory":
                sub = _jupyter_api(f"api/contents/{urllib.parse.quote(item['path'], safe='/')}")
                if "content" in sub:
                    walk(sub["content"], item["path"] + "/")
    walk(items)
    return {"notebooks": notebooks}


def _jupyter_read_notebook(path):
    """Read a notebook's cells."""
    result = _jupyter_api(f"api/contents/{urllib.parse.quote(path, safe='/')}")
    if "error" in result:
        return result
    cells = []
    for i, c in enumerate(result.get("content", {}).get("cells", [])):
        cell = {"index": i, "cell_type": c["cell_type"], "source": c["source"]}
        if c.get("outputs"):
            outputs = []
            for o in c["outputs"]:
                if o.get("text"):
                    outputs.append({"type": "stream", "text": o["text"]})
                elif o.get("data"):
                    outputs.append({"type": o.get("output_type", "display"), "data": o["data"]})
                elif o.get("ename"):
                    outputs.append({"type": "error", "ename": o["ename"], "evalue": o["evalue"]})
            cell["outputs"] = outputs
        cells.append(cell)
    return {"path": path, "cells": cells}


_agent_km = None  # Dedicated agent kernel manager
_agent_kc = None  # Dedicated agent kernel client


def _get_agent_kernel():
    """Get or lazily start the dedicated agent kernel."""
    global _agent_km, _agent_kc
    # Check if existing kernel is still alive
    if _agent_km is not None and _agent_kc is not None:
        if _agent_km.is_alive():
            return _agent_kc
        # Kernel died, clean up
        try:
            _agent_kc.stop_channels()
        except Exception:
            pass
        _agent_km = None
        _agent_kc = None
    # Start a new dedicated kernel
    from jupyter_client import KernelManager
    _agent_km = KernelManager(kernel_name='python3')
    _agent_km.start_kernel()
    _agent_kc = _agent_km.blocking_client()
    _agent_kc.start_channels()
    _agent_kc.wait_for_ready(timeout=30)
    return _agent_kc


def _jupyter_execute(code):
    """Execute code on the agent's dedicated kernel. Does not touch notebook kernels."""
    try:
        kc = _get_agent_kernel()
        msg_id = kc.execute(code)
        outputs = []
        while True:
            try:
                msg = kc.get_iopub_msg(timeout=30)
                mt = msg["msg_type"]
                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                if mt == "stream":
                    outputs.append({"type": "stream", "name": msg["content"]["name"], "text": msg["content"]["text"]})
                elif mt in ("display_data", "execute_result"):
                    outputs.append({"type": mt, "data": msg["content"]["data"]})
                elif mt == "error":
                    outputs.append({"type": "error", "ename": msg["content"]["ename"], "evalue": msg["content"]["evalue"], "traceback": msg["content"]["traceback"]})
                elif mt == "status" and msg["content"]["execution_state"] == "idle":
                    break
            except Exception:
                break
        return {"status": "ok", "outputs": outputs}
    except Exception as e:
        return {"error": str(e)}


def _jupyter_insert_and_run(code, notebook, position="bottom"):
    """Insert a cell into the open notebook and execute it (live in UI)."""
    return _jupyter_cmd("insert_and_run", notebook=notebook, source=code, position=position)


def _jupyter_cmd(action, notebook=None, **kwargs):
    """Send a command to a specific Jupyter notebook open in the browser."""
    if not notebook:
        return {"error": "The 'notebook' parameter is required. Specify which notebook to target by name (e.g. 'Untitled4')."}
    try:
        api_key = read_api_key()
        import socketio as _sio
        import uuid as _uuid
        import threading as _threading
        sio = _sio.Client()
        csrf_event = _threading.Event()
        result_event = _threading.Event()
        csrf = [None]
        result = [None]
        cmd_id = str(_uuid.uuid4())

        @sio.on("connected")
        def on_conn(data):
            csrf[0] = data.get("csrf_token")
            csrf_event.set()

        @sio.on("jupyter_cmd_result")
        def on_result(data):
            if data.get("id") == cmd_id:
                result[0] = data
                result_event.set()

        sio.connect(f"http://localhost:5000?api_key={api_key}")
        if not csrf_event.wait(timeout=3):
            sio.disconnect()
            return {"error": "Could not get CSRF token"}
        sio.emit("jupyter_cmd", {
            "action": action,
            "id": cmd_id,
            "csrf_token": csrf[0],
            "notebook": notebook,
            **kwargs,
        })
        # Wait for result from Flask (relayed from browser ack)
        if result_event.wait(timeout=5):
            count = result[0].get("receivers", 0)
            sio.disconnect()
            if count == 0:
                return {"error": f"Notebook '{notebook}' is not open in the browser. Open it and retry.", "action": action}
            return {"status": "ok", "action": action, "notebook": notebook, "receivers": count}
        sio.disconnect()
        return {
            "error": f"No response from browser. Is notebook '{notebook}' open?",
            "action": action,
        }
    except Exception as e:
        return {"error": str(e)}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="jupyter_list",
            description="List all Jupyter notebooks (.ipynb files) in the Jupyter data directory.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="jupyter_read",
            description="Read a Jupyter notebook's cells (source code and outputs).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Notebook path (e.g. 'Untitled.ipynb')"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="jupyter_execute",
            description="Execute Python code on a dedicated agent kernel (separate from notebook kernels). Returns output. Does NOT show in the notebook UI — use jupyter_insert_and_run for that.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="jupyter_insert_and_run",
            description="Insert a new code cell into the open Jupyter notebook and execute it. The cell and its output appear live in the user's browser.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code for the new cell"},
                    "notebook": {"type": "string", "description": "Notebook name to target (e.g. 'Untitled4')"},
                    "position": {"description": "Where to insert the cell: 'top', 'bottom' (default), or a cell index to insert after"},
                },
                "required": ["code", "notebook"],
            },
        ),
        Tool(
            name="jupyter_run_cell",
            description="Run an existing cell in the open Jupyter notebook by index. The cell executes live in the user's browser.",
            inputSchema={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Cell index (0-based)"},
                    "notebook": {"type": "string", "description": "Notebook name to target (e.g. 'Untitled4')"},
                },
                "required": ["index", "notebook"],
            },
        ),
        Tool(
            name="jupyter_run_all",
            description="Run all cells in the open Jupyter notebook sequentially. Executes live in the user's browser.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {"type": "string", "description": "Notebook name to target (e.g. 'Untitled4')"},
                },
                "required": ["notebook"],
            },
        ),
        Tool(
            name="jupyter_edit_cell",
            description="Replace the source code of an existing cell in the open Jupyter notebook by index. The change appears live in the user's browser.",
            inputSchema={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Cell index (0-based)"},
                    "source": {"type": "string", "description": "New cell source code"},
                    "notebook": {"type": "string", "description": "Notebook name to target (e.g. 'Untitled4')"},
                },
                "required": ["index", "source", "notebook"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "jupyter_list":
        result = _jupyter_list_notebooks()
    elif name == "jupyter_read":
        result = _jupyter_read_notebook(arguments["path"])
    elif name == "jupyter_execute":
        result = _jupyter_execute(arguments["code"])
    elif name == "jupyter_insert_and_run":
        result = _jupyter_insert_and_run(arguments["code"], arguments["notebook"], arguments.get("position", "bottom"))
    elif name == "jupyter_run_cell":
        result = _jupyter_cmd("run_cell", notebook=arguments["notebook"], index=arguments["index"])
    elif name == "jupyter_run_all":
        result = _jupyter_cmd("run_all", notebook=arguments["notebook"])
    elif name == "jupyter_edit_cell":
        result = _jupyter_cmd("edit_cell", notebook=arguments["notebook"], index=arguments["index"], source=arguments["source"])
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
