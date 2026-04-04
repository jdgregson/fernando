#!/usr/bin/env python3
"""
MCP Server for Kasm Desktop Control
Allows AI to control the Kasm desktop via keyboard/mouse commands
"""

import asyncio
import json
import os
import subprocess
from mcp.server import Server
from mcp.types import Tool, TextContent


CDP_PORT = 9222


def cdp_send(ws_url, method, params=None):
    """Send a CDP command via python3 inside the container and return the result."""
    msg_json = json.dumps({"id": 1, "method": method, "params": params or {}})
    script = """
import websocket, json, sys
msg = sys.stdin.read()
ws = websocket.create_connection(sys.argv[1], timeout=10)
ws.send(msg)
while True:
    resp = json.loads(ws.recv())
    if resp.get("id") == 1:
        print(json.dumps(resp))
        break
ws.close()
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "fernando-desktop", "python3", "-c", script, ws_url],
        input=msg_json, capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def get_chrome_tabs():
    """Get list of open Chrome tabs via CDP HTTP API."""
    result = subprocess.run(
        ["docker", "exec", "fernando-desktop", "curl", "-s", f"http://localhost:{CDP_PORT}/json"],
        capture_output=True, text=True, timeout=5,
    )
    return json.loads(result.stdout) if result.stdout.strip() else []

app = Server("kasm-desktop")


def exec_in_kasm(cmd, env=None, shell=False):
    """Execute a command inside the Kasm container.
    cmd: shell string if shell=True, list of args if shell=False.
    env: dict of environment variables to set."""
    base = ["docker", "exec", "--user", "1000:1000"]
    if env:
        for k, v in env.items():
            base += ["-e", f"{k}={v}"]
    base += ["fernando-desktop"]
    if shell:
        base += ["bash", "-c", cmd]
    else:
        base += cmd
    result = subprocess.run(base, capture_output=True, text=True)
    return result.stdout + result.stderr


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="type_text",
            description="Type text on the Kasm desktop",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"}
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="press_key",
            description="Press a keyboard key or key combination (e.g., 'Return', 'ctrl+c', 'alt+Tab')",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key or key combination to press",
                    }
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="click_mouse",
            description="Click mouse at coordinates or click a button",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (optional)"},
                    "y": {"type": "integer", "description": "Y coordinate (optional)"},
                    "button": {
                        "type": "integer",
                        "description": "Mouse button (1=left, 2=middle, 3=right)",
                        "default": 1,
                    },
                },
            },
        ),
        Tool(
            name="open_application",
            description="Open an application on the desktop",
            inputSchema={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name (e.g., 'firefox', 'terminal', 'gedit')",
                    }
                },
                "required": ["app"],
            },
        ),
        Tool(
            name="run_command",
            description="Run a shell command in the Kasm desktop",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    }
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="screenshot",
            description="Take a screenshot of the Kasm desktop",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="scroll",
            description="Scroll the mouse wheel up or down at specified coordinates. You must specify where to scroll.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {
                        "type": "integer",
                        "description": "X coordinate to scroll at",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate to scroll at",
                    },
                    "direction": {
                        "type": "string",
                        "description": "Direction to scroll: 'up' or 'down'",
                        "enum": ["up", "down"],
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Number of scroll clicks (default: 3)",
                        "default": 3,
                    },
                },
                "required": ["x", "y", "direction"],
            },
        ),
        Tool(
            name="move_mouse",
            description="Move mouse cursor to coordinates without clicking",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate"},
                    "y": {"type": "integer", "description": "Y coordinate"},
                },
                "required": ["x", "y"],
            },
        ),
        Tool(
            name="drag_mouse",
            description="Click and drag from one point to another",
            inputSchema={
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Starting X coordinate"},
                    "y1": {"type": "integer", "description": "Starting Y coordinate"},
                    "x2": {"type": "integer", "description": "Ending X coordinate"},
                    "y2": {"type": "integer", "description": "Ending Y coordinate"},
                    "button": {
                        "type": "integer",
                        "description": "Mouse button (1=left, 2=middle, 3=right)",
                        "default": 1,
                    },
                    "delay": {
                        "type": "number",
                        "description": "Delay in seconds after mousedown and before mouseup (default: 0.1)",
                        "default": 0.1,
                    },
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        ),
        Tool(
            name="get_mouse_position",
            description="Get current mouse cursor position",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="double_click",
            description="Double-click at coordinates or current position",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (optional)"},
                    "y": {"type": "integer", "description": "Y coordinate (optional)"},
                },
            },
        ),
        Tool(
            name="right_click",
            description="Right-click at coordinates or current position",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (optional)"},
                    "y": {"type": "integer", "description": "Y coordinate (optional)"},
                },
            },
        ),
        Tool(
            name="get_window_info",
            description="List open windows with their IDs, names, and positions",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="focus_window",
            description="Bring a window to front by name or ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "Window name or ID"}
                },
                "required": ["window"],
            },
        ),
        Tool(
            name="get_screen_size",
            description="Get desktop screen resolution",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="set_screen_size",
            description="Set desktop screen resolution",
            inputSchema={
                "type": "object",
                "properties": {
                    "width": {
                        "type": "integer",
                        "description": "Screen width in pixels",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Screen height in pixels",
                    },
                },
                "required": ["width", "height"],
            },
        ),
        Tool(
            name="get_clipboard",
            description="Get text content from clipboard",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="set_clipboard",
            description="Set text content to clipboard",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to copy to clipboard",
                    }
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="desktop_exec",
            description="Execute a bash command in the Kasm desktop container",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to execute",
                    }
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="browser_tabs",
            description="List open Chrome browser tabs (title, URL, tab ID)",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="browser_get_dom",
            description="Get the DOM content (outer HTML or extracted text) from a Chrome browser tab. Requires Chrome running with --remote-debugging-port.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "Tab index from browser_tabs (default: 0, the first tab)",
                        "default": 0,
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to extract (default: 'body'). Use 'document' for full page HTML.",
                        "default": "body",
                    },
                    "text_only": {
                        "type": "boolean",
                        "description": "Return innerText instead of HTML (default: true)",
                        "default": True,
                    },
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _e = {"DISPLAY": ":1"}

    if name == "type_text":
        result = exec_in_kasm(["xdotool", "type", "--", arguments["text"]], env=_e)
        return [TextContent(type="text", text=f"Typed: {arguments['text']}\n{result}")]

    elif name == "press_key":
        result = exec_in_kasm(["xdotool", "key", "--", arguments["key"]], env=_e)
        return [TextContent(type="text", text=f"Pressed: {arguments['key']}\n{result}")]

    elif name == "click_mouse":
        x = arguments.get("x")
        y = arguments.get("y")
        button = str(arguments.get("button", 1))
        argv = ["xdotool"]
        if x is not None and y is not None:
            argv += ["mousemove", str(x), str(y)]
        argv += ["click", button]
        result = exec_in_kasm(argv, env=_e)
        return [TextContent(type="text", text=f"Clicked at ({x}, {y}) button {button}\n{result}")]

    elif name == "open_application":
        app_name = arguments["app"]
        if app_name in ("chrome", "google-chrome", "chromium"):
            app_name = "google-chrome"
        result = exec_in_kasm(f"DISPLAY=:1 nohup {app_name} &>/dev/null & sleep 1", shell=True)
        return [TextContent(type="text", text=f"Opened: {app_name}\n{result}")]

    elif name == "run_command":
        cmd = arguments["command"]
        result = exec_in_kasm(f"DISPLAY=:1 {cmd}", shell=True)
        return [TextContent(type="text", text=f"Command output:\n{result}")]

    elif name == "screenshot":
        filename = "screenshot.png"
        host_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "desktop", filename)
        exec_in_kasm(["scrot", "--overwrite", f"/home/kasm-user/{filename}"], env=_e)
        return [TextContent(type="text", text=f"Screenshot saved to: {host_path}")]

    elif name == "scroll":
        x, y = str(arguments["x"]), str(arguments["y"])
        amount = str(arguments.get("amount", 3))
        button = "4" if arguments["direction"] == "up" else "5"
        result = exec_in_kasm(["xdotool", "mousemove", x, y, "click", "--repeat", amount, button], env=_e)
        return [TextContent(type="text", text=f"Scrolled {arguments['direction']} {amount} times at ({x}, {y})\n{result}")]

    elif name == "move_mouse":
        result = exec_in_kasm(["xdotool", "mousemove", str(arguments["x"]), str(arguments["y"])], env=_e)
        return [TextContent(type="text", text=f"Moved mouse to ({arguments['x']}, {arguments['y']})\n{result}")]

    elif name == "drag_mouse":
        x1, y1 = str(arguments["x1"]), str(arguments["y1"])
        x2, y2 = str(arguments["x2"]), str(arguments["y2"])
        button = str(arguments.get("button", 1))
        delay = str(arguments.get("delay", 0.1))
        result = exec_in_kasm(["xdotool", "mousemove", x1, y1, "mousedown", button, "sleep", delay, "mousemove", x2, y2, "sleep", delay, "mouseup", button], env=_e)
        return [TextContent(type="text", text=f"Dragged from ({x1}, {y1}) to ({x2}, {y2})\n{result}")]

    elif name == "get_mouse_position":
        result = exec_in_kasm(["xdotool", "getmouselocation", "--shell"], env=_e)
        return [TextContent(type="text", text=f"Mouse position:\n{result}")]

    elif name == "double_click":
        x, y = arguments.get("x"), arguments.get("y")
        argv = ["xdotool"]
        if x is not None and y is not None:
            argv += ["mousemove", str(x), str(y)]
        argv += ["click", "--repeat", "2", "1"]
        result = exec_in_kasm(argv, env=_e)
        return [TextContent(type="text", text=f"Double-clicked at ({x}, {y})\n{result}")]

    elif name == "right_click":
        x, y = arguments.get("x"), arguments.get("y")
        argv = ["xdotool"]
        if x is not None and y is not None:
            argv += ["mousemove", str(x), str(y)]
        argv += ["click", "3"]
        result = exec_in_kasm(argv, env=_e)
        return [TextContent(type="text", text=f"Right-clicked at ({x}, {y})\n{result}")]

    elif name == "get_window_info":
        result = exec_in_kasm(["wmctrl", "-lG"], env=_e)
        return [TextContent(type="text", text=f"Open windows:\n{result}")]

    elif name == "focus_window":
        window = arguments["window"]
        flag = "-ia" if window.startswith("0x") else "-a"
        result = exec_in_kasm(["wmctrl", flag, window], env=_e)
        return [TextContent(type="text", text=f"Focused window: {window}\n{result}")]

    elif name == "get_screen_size":
        result = exec_in_kasm("DISPLAY=:1 xdpyinfo | grep dimensions", shell=True)
        return [TextContent(type="text", text=f"Screen size:\n{result}")]

    elif name == "set_screen_size":
        w, h = str(arguments["width"]), str(arguments["height"])
        result = exec_in_kasm(["xrandr", "--output", "VNC-0", "--fb", f"{w}x{h}"], env=_e)
        return [TextContent(type="text", text=f"Set screen size to {w}x{h}\n{result}")]

    elif name == "get_clipboard":
        result = exec_in_kasm(["xclip", "-selection", "clipboard", "-o"], env=_e)
        return [TextContent(type="text", text=f"Clipboard content:\n{result}")]

    elif name == "set_clipboard":
        result = exec_in_kasm(f"echo '{arguments['text'].replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}' | DISPLAY=:1 xclip -selection clipboard", shell=True)
        return [TextContent(type="text", text=f"Set clipboard to: {arguments['text']}\n{result}")]

    elif name == "desktop_exec":
        result = exec_in_kasm(arguments["command"], shell=True)
        return [TextContent(type="text", text=f"Command output:\n{result}")]

    elif name == "browser_tabs":
        try:
            tabs = get_chrome_tabs()
            pages = [t for t in tabs if t.get("type") == "page"]
            if not pages:
                return [TextContent(type="text", text="No browser tabs open (is Chrome running with --remote-debugging-port?)")]
            lines = [f"[{i}] {t['title']}\n    {t['url']}" for i, t in enumerate(pages)]
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error listing tabs: {e}")]

    elif name == "browser_get_dom":
        try:
            tabs = get_chrome_tabs()
            pages = [t for t in tabs if t.get("type") == "page"]
            idx = arguments.get("tab_index", 0)
            if not pages or idx >= len(pages):
                return [TextContent(type="text", text="Tab not found. Use browser_tabs to list available tabs.")]
            ws_url = pages[idx]["webSocketDebuggerUrl"]
            selector = arguments.get("selector", "body")
            text_only = arguments.get("text_only", True)
            prop = "innerText" if text_only else "outerHTML"
            if selector == "document":
                js = "document.documentElement.outerHTML"
            else:
                sel_escaped = json.dumps(selector)
                js = f"document.querySelector({sel_escaped})?.{prop} || 'Element not found: ' + {sel_escaped}"
            resp = cdp_send(ws_url, "Runtime.evaluate", {"expression": js, "returnByValue": True})
            result = resp.get("result", {}).get("result", {})
            if result.get("type") == "string":
                return [TextContent(type="text", text=result["value"])]
            return [TextContent(type="text", text=f"Unexpected result: {json.dumps(result)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting DOM: {e}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
