#!/usr/bin/env python3
"""
MCP Server for Kasm Desktop Control
Allows AI to control the Kasm desktop via keyboard/mouse commands
"""

import asyncio
import json
import os
import subprocess
import base64
import struct
import uuid
from pathlib import Path
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent
from browser_cdp import browser


CDP_PORT = 9222


def cdp_send(ws_url, method, params=None):
    """Send a CDP command via python3 inside the container and return the result."""
    msg_json = json.dumps({"id": 1, "method": method, "params": params or {}})
    script = """
import websocket, json, sys
msg = sys.stdin.read()
ws = websocket.create_connection(sys.argv[1], timeout=10, suppress_origin=True)
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

OBSERVED_ACTIONS = {"type_text", "press_key", "click_mouse", "open_application", "scroll", "move_mouse", "drag_mouse", "double_click", "right_click", "focus_window", "set_screen_size"}

GAMEPLAY_ACTION_SCHEMA = {
    "type": "array", "maxItems": 200,
    "description": "Ordered actions. Keys/buttons remain held across waits and moves; all held inputs are released on completion or cancellation. Use separate key_down actions for chords. Move duration interpolates at up to 60Hz; relative moves support pointer-locked games.",
    "items": {"type": "object", "required": ["kind"], "properties": {
        "kind": {"type": "string", "enum": ["wait", "key_down", "key_up", "button_down", "button_up", "move"]},
        "key": {"type": "string", "description": "Single X keysym, e.g. w, space, Shift_L"},
        "button": {"type": "integer", "minimum": 1, "maximum": 9},
        "x": {"type": "number", "minimum": -32767, "maximum": 32767},
        "y": {"type": "number", "minimum": -32767, "maximum": 32767},
        "relative": {"type": "boolean", "default": False},
        "duration": {"type": "number", "minimum": 0, "maximum": 60, "description": "Seconds for wait or interpolated move. Total sequence at most 60 seconds."},
    }},
}


async def gameplay_operation(operation, **arguments):
    script = Path(__file__).with_name("desktop_gameplay.py").read_text()
    process = await asyncio.create_subprocess_exec(
        "docker", "exec", "-i", "--user", "1000:1000", "-e", "DISPLAY=:1",
        "fernando-desktop", "timeout", "--signal=TERM", "--kill-after=5", "80",
        "/usr/bin/python3", "-u", "-c", script,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        process.stdin.write((json.dumps({"operation": operation, **arguments}) + "\n").encode())
        await process.stdin.drain()
        output, errors, code = await asyncio.wait_for(asyncio.gather(
            process.stdout.read(), process.stderr.read(), process.wait()), timeout=90)
        if code:
            raise RuntimeError(f"Desktop gameplay operation failed: {errors.decode()}")
        return json.loads(output)
    finally:
        process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


async def gameplay_frames(recording_id, timestamps):
    result = await gameplay_operation("frames", recording_id=recording_id, timestamps=timestamps)
    content = []
    for frame in result["frames"]:
        path = Path(__file__).resolve().parent.parent / "data" / "desktop" / frame["file"]
        content.append(TextContent(type="text", text=f"Frame requested at {frame['requested_seconds']:.3f}s; saved to {path}"))
        content.append(ImageContent(type="image", mimeType="image/png", data=base64.b64encode(path.read_bytes()).decode("ascii")))
    return content


def desktop_observation(*args):
    script = Path(__file__).with_name("desktop_observe.py").read_text()
    result = subprocess.run(
        ["docker", "exec", "-i", "--user", "1000:1000", "-e", "DISPLAY=:1",
         "fernando-desktop", "/usr/bin/python3", "-", *args],
        input=script, capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        raise RuntimeError(f"Desktop observation failed: {result.stderr}")
    return json.loads(result.stdout)


def screenshot_content():
    filename = f"screenshot_{uuid.uuid4().hex[:12]}.png"
    metadata = desktop_observation("screenshot", f"/home/kasm-user/{filename}")
    path = Path(__file__).resolve().parent.parent / "data" / "desktop" / filename
    image = path.read_bytes()
    width, height = struct.unpack(">II", image[16:24])
    metadata.update({"path": str(path), "width": width, "height": height,
                     "coordinate_system": "Desktop pixels, origin at top left; pointer coordinates are the cursor hotspot."})
    return [TextContent(type="text", text=json.dumps(metadata, indent=2)),
            ImageContent(type="image", mimeType="image/png", data=base64.b64encode(image).decode("ascii"))]


def exec_in_kasm(cmd, env=None, shell=False, stdin_data=None):
    """Execute a command inside the Kasm container.
    cmd: shell string if shell=True, list of args if shell=False.
    env: dict of environment variables to set.
    stdin_data: optional string to pipe to stdin."""
    base = ["docker", "exec", "-i" if stdin_data else None, "--user", "1000:1000"]
    base = [x for x in base if x]
    if env:
        for k, v in env.items():
            base += ["-e", f"{k}={v}"]
    base += ["fernando-desktop"]
    if shell:
        base += ["bash", "-c", cmd]
    else:
        base += cmd
    result = subprocess.run(base, capture_output=True, text=True, input=stdin_data)
    return result.stdout + result.stderr


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="browser_capture",
            description="Start, inspect, or stop persistent browser-wide CDP network capture. Captures pages, out-of-process frames and workers, including future targets, while this MCP server runs. Start BEFORE navigation/actions; past requests cannot be recovered. In-memory event buffer: 4000 events/16 MiB; Chrome bodies: 32 MiB per target, 8 MiB per resource. Status lists stable target IDs and attachment failures. Stop clears captured events. Traffic may contain credentials; retrieve only what the task needs.",
            inputSchema={"type": "object", "properties": {
                "action": {"type": "string", "enum": ["start", "status", "stop"]},
            }, "required": ["action"]},
        ),
        Tool(
            name="browser_network",
            description="List captured browser requests with IDs, URLs, methods, status, completion/errors and target IDs. Filter by URL substring, HTTP method/status, target and event cursor. Follow cursor while has_more is true. Requires browser_capture start. Headers, cookies, timing, redirects and WebSocket frames are available through browser_request/browser_events.",
            inputSchema={"type": "object", "properties": {
                "url": {"type": "string"}, "method": {"type": "string"},
                "status": {"type": "integer"}, "target_id": {"type": "string"},
                "after": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            }},
        ),
        Tool(
            name="browser_request",
            description="Inspect a captured request: details includes request/response headers, initiator, timing, redirects, extra-info cookies and raw events. request_body/response_body retrieve Chrome's buffered body with explicit character pagination and base64 flag. Bodies can expire or be unavailable; errors are reported, never treated as empty. Binary base64 chunks must be reassembled before decoding. Uploaded files may be omitted by CDP request-body retrieval.",
            inputSchema={"type": "object", "properties": {
                "request_id": {"type": "string"},
                "part": {"type": "string", "enum": ["details", "request_body", "response_body"], "default": "details"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 16000},
            }, "required": ["request_id"]},
        ),
        Tool(
            name="browser_cdp",
            description="Send any Chrome DevTools Protocol command over the persistent connection. Provide target_id for a target session; omit for the browser session. Supports domains such as DOM, Runtime, Page, Network, Debugger and Storage. Enable domains explicitly to receive their events via browser_events. Commands can modify the browser, execute code or send requests: use only within the user's task. Requires browser_capture start.",
            inputSchema={"type": "object", "properties": {
                "method": {"type": "string"}, "params": {"type": "object"},
                "target_id": {"type": "string"},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 15},
            }, "required": ["method"]},
        ),
        Tool(
            name="browser_events",
            description="Read retained raw CDP events with cursor pagination. Filter by method prefix (e.g. Network.webSocket or Runtime.console) and target ID. Includes timestamps and reports dropped events. General browser information access; non-Network domains must first be enabled with browser_cdp.",
            inputSchema={"type": "object", "properties": {
                "after": {"type": "integer", "minimum": 0, "default": 0},
                "method": {"type": "string", "default": ""}, "target_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            }},
        ),
        Tool(
            name="browser_wait",
            description="Wait up to a bounded timeout for exactly one condition: a truthy JavaScript expression in target_id (e.g. document.querySelector('#results')?.innerText.includes('Ready')), OR a captured completed/failed request matching URL substring and optional status/target. Set after to the cursor before triggering the action to avoid old matches. Capture must already be started. Network failures are returned explicitly; timeouts raise an error.",
            inputSchema={"type": "object", "properties": {
                "target_id": {"type": "string"}, "expression": {"type": "string"},
                "url": {"type": "string"}, "status": {"type": "integer"},
                "after": {"type": "integer", "minimum": 0, "default": 0},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 15},
            }},
        ),
        Tool(
            name="browser_replay",
            description="Resend a captured request in its browser session. This performs the action again, including POST side effects; use only as authorized by the task. mode=xhr uses native Network.replayXHR without edits. mode=fetch allows URL/method/header/body edits, uses the original frame and current browser cookies, respects CORS, and stops at redirects. URL edits must retain the original origin to avoid forwarding credentials elsewhere. Browser-controlled headers are regenerated. Fetch replay is not byte-identical; binary/multipart bodies require an explicit replacement. Inspect new traffic using after_cursor.",
            inputSchema={"type": "object", "properties": {
                "request_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["fetch", "xhr"], "default": "fetch"},
                "url": {"type": "string"}, "method": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"type": "string"},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 15},
            }, "required": ["request_id"]},
        ),
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
            name="desktop_shell",
            description="Run a shell command in the Kasm desktop container",
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
            description="See a screenshot of the Kasm desktop including the cursor, with dimensions, pointer coordinates, active window and accessible focused controls. Returns the actual image, also displayed in chat, plus its saved path.",
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
        Tool(
            name="browser_exec_js",
            description="Execute JavaScript in a Chrome browser tab via CDP. Returns the result of the expression. Use for DOM manipulation, clicking elements, hiding panels, extracting data, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "JavaScript expression to evaluate (e.g., \"document.querySelector('.panel').style.display='none'\")",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Tab index from browser_tabs (default: 0)",
                        "default": 0,
                    },
                },
                "required": ["expression"],
            },
        ),
        Tool(
            name="browser_console",
            description="Get recent console output (logs, errors, warnings) from a Chrome browser tab via CDP. Enables Runtime and collects any buffered exceptions. Also installs a persistent collector for future calls.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "Tab index from browser_tabs (default: 0)",
                        "default": 0,
                    },
                },
            },
        ),
        Tool(
            name="desktop_copy_file",
            description="Copy a file between the host and the Kasm desktop container. Provide the source path and direction. The destination is chosen automatically and returned.",
            inputSchema={
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source file path (host path for to_desktop, container path for from_desktop)"},
                    "direction": {
                        "type": "string",
                        "enum": ["to_desktop", "from_desktop"],
                        "description": "to_desktop: host→container, from_desktop: container→host",
                    },
                },
                "required": ["src", "direction"],
            },
        ),
    ]
    for tool in tools:
        if tool.name in OBSERVED_ACTIONS:
            tool.inputSchema["properties"]["observe"] = {
                "type": "boolean", "default": False,
                "description": "Return a post-action screenshot visible in chat, with pointer and focus context. Shows observed state, not proof of intended application effect.",
            }
    tools.append(Tool(
        name="accessibility", description="Read native desktop AT-SPI control names, roles, states, screen-coordinate bounds and focused controls. Bounded snapshot reports truncation and application accessibility errors. Only exposes controls supported by the application.",
        inputSchema={"type": "object", "properties": {
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 300},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
        }},
    ))
    tools.extend([
        Tool(name="input_sequence", description="Perform bounded timed desktop keyboard/mouse actions, including simultaneous held inputs and smooth absolute or relative pointer movement. Releases inputs on completion/cancellation. Optional screenshot is visible to both agent and user. Input posting does not prove the intended game effect.",
             inputSchema={"type": "object", "required": ["actions"], "properties": {
                 "actions": GAMEPLAY_ACTION_SCHEMA, "observe": {"type": "boolean", "default": True}}}),
        Tool(name="record_gameplay", description="Record 1–60 seconds of desktop video with cursor (no audio), optionally running timed keyboard/mouse actions during capture. Returns a saved MP4 path, recording ID, input timing and four sampled images visible in chat. Agent inspects frames rather than consuming video directly. Capture overhead can affect game performance.",
             inputSchema={"type": "object", "required": ["duration"], "properties": {
                 "duration": {"type": "number", "minimum": 1, "maximum": 60},
                 "fps": {"type": "integer", "minimum": 5, "maximum": 60, "default": 30},
                 "actions": GAMEPLAY_ACTION_SCHEMA}}),
        Tool(name="recording_frames", description="Inspect 1–12 timestamped frames from a recording made by record_gameplay. Returns actual images displayed in chat; longest edge capped at 1280 pixels. Use closely spaced timestamps to inspect motion. Timestamps must precede recording duration.",
             inputSchema={"type": "object", "required": ["recording_id", "timestamps"], "properties": {
                 "recording_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                 "timestamps": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "number", "minimum": 0, "maximum": 60}}}}),
    ])
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    result = await dispatch_tool(name, arguments)
    if name in OBSERVED_ACTIONS and arguments.get("observe", False):
        result.extend(screenshot_content())
    return result


async def dispatch_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    _e = {"DISPLAY": ":1"}

    if name == "input_sequence":
        result = await gameplay_operation("sequence", actions=arguments["actions"])
        content = [TextContent(type="text", text=json.dumps(result, indent=2))]
        if arguments.get("observe", True):
            content.extend(screenshot_content())
        return content

    elif name == "record_gameplay":
        recording_id = uuid.uuid4().hex
        result = await gameplay_operation("record", recording_id=recording_id, **arguments)
        path = Path(__file__).resolve().parent.parent / "data" / "desktop" / f"gameplay_{recording_id}.mp4"
        result["path"] = str(path)
        content = [TextContent(type="text", text=json.dumps(result, indent=2))]
        content.extend(await gameplay_frames(recording_id, [result["duration"] * portion for portion in (0, 0.3, 0.6, 0.9)]))
        return content

    elif name == "recording_frames":
        return await gameplay_frames(arguments["recording_id"], arguments["timestamps"])

    elif name == "browser_capture":
        action = arguments["action"]
        if action == "start":
            result = await browser.start()
        elif action == "stop":
            result = await browser.stop()
        else:
            result = browser.status()
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "browser_network":
        return [TextContent(type="text", text=json.dumps(browser.list_requests(**arguments), indent=2))]

    elif name == "browser_request":
        part = arguments.get("part", "details")
        if part == "details":
            result = browser.request(arguments["request_id"])
        else:
            result = await browser.body(arguments["request_id"],
                                        "response" if part == "response_body" else "request",
                                        arguments.get("offset", 0), arguments.get("limit", 16000))
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "browser_cdp":
        target = arguments.get("target_id")
        result = await browser.command(arguments["method"], arguments.get("params"),
                                       browser.session(target) if target else None,
                                       arguments.get("timeout", 15))
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "browser_events":
        return [TextContent(type="text", text=json.dumps(browser.get_events(**arguments), indent=2))]

    elif name == "browser_wait":
        result = await browser.wait(**arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "browser_replay":
        result = await browser.replay(**arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "type_text":
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
        result = exec_in_kasm(["bash", "-c", 'nohup "$1" &>/dev/null & sleep 1', "_", app_name], env=_e)
        return [TextContent(type="text", text=f"Opened: {app_name}\n{result}")]

    elif name == "desktop_shell":
        cmd = arguments["command"]
        result = exec_in_kasm(f"DISPLAY=:1 {cmd}", shell=True)
        return [TextContent(type="text", text=f"Command output:\n{result}")]

    elif name == "screenshot":
        return screenshot_content()

    elif name == "accessibility":
        result = desktop_observation("accessibility", str(arguments.get("max_nodes", 300)), str(arguments.get("max_depth", 12)))
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

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
        result = exec_in_kasm(["xclip", "-selection", "clipboard"], env=_e, stdin_data=arguments["text"])
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

    elif name == "browser_exec_js":
        try:
            tabs = get_chrome_tabs()
            pages = [t for t in tabs if t.get("type") == "page"]
            idx = arguments.get("tab_index", 0)
            if not pages or idx >= len(pages):
                return [TextContent(type="text", text="Tab not found. Use browser_tabs to list available tabs.")]
            ws_url = pages[idx]["webSocketDebuggerUrl"]
            resp = cdp_send(ws_url, "Runtime.evaluate", {
                "expression": arguments["expression"],
                "returnByValue": True,
                "awaitPromise": True,
            })
            r = resp.get("result", {})
            if "exceptionDetails" in r:
                exc = r["exceptionDetails"]
                msg = exc.get("exception", {}).get("description", exc.get("text", str(exc)))
                return [TextContent(type="text", text=f"JS Error: {msg}")]
            val = r.get("result", {})
            if val.get("type") == "undefined":
                return [TextContent(type="text", text="undefined")]
            return [TextContent(type="text", text=json.dumps(val.get("value", val), indent=2, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error executing JS: {e}")]

    elif name == "browser_console":
        try:
            tabs = get_chrome_tabs()
            pages = [t for t in tabs if t.get("type") == "page"]
            idx = arguments.get("tab_index", 0)
            if not pages or idx >= len(pages):
                return [TextContent(type="text", text="Tab not found.")]
            ws_url = pages[idx]["webSocketDebuggerUrl"]
            script = """
import websocket, json, sys, time
ws = websocket.create_connection(sys.argv[1], timeout=10, suppress_origin=True)
msg_id = 1

def send(method, params=None):
    global msg_id
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    msg_id += 1

send("Runtime.enable")
send("Log.enable")

entries = []
deadline = time.time() + 1.5
while time.time() < deadline:
    ws.settimeout(0.5)
    try:
        raw = ws.recv()
        msg = json.loads(raw)
        if msg.get("method") == "Runtime.consoleAPICalled":
            p = msg["params"]
            level = p.get("type", "log")
            args = " ".join(a.get("description", a.get("value", str(a))) for a in p.get("args", []))
            entries.append(f"[{level}] {args}")
        elif msg.get("method") == "Runtime.exceptionThrown":
            exc = msg["params"].get("exceptionDetails", {})
            text = exc.get("exception", {}).get("description", exc.get("text", "unknown"))
            entries.append(f"[exception] {text}")
        elif msg.get("method") == "Log.entryAdded":
            e = msg["params"].get("entry", {})
            entries.append(f"[{e.get('level','log')}] {e.get('text','')}")
    except websocket.WebSocketTimeoutException:
        break
    except Exception:
        break

ws.close()
print(json.dumps(entries))
"""
            result = subprocess.run(
                ["docker", "exec", "-i", "fernando-desktop", "python3", "-c", script, ws_url],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return [TextContent(type="text", text=f"Error: {result.stderr}")]
            entries = json.loads(result.stdout)
            if entries:
                return [TextContent(type="text", text="\n".join(entries))]
            return [TextContent(type="text", text="(no console output)")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "desktop_copy_file":
        src = arguments["src"]
        direction = arguments["direction"]
        filename = os.path.basename(src)
        try:
            if direction == "to_desktop":
                dst = f"/home/kasm-user/{filename}"
                subprocess.run(["docker", "cp", src, f"fernando-desktop:{dst}"], check=True, capture_output=True, text=True)
                return [TextContent(type="text", text=f"Copied to container: {dst}")]
            else:
                dst = f"/tmp/{filename}"
                subprocess.run(["docker", "cp", f"fernando-desktop:{src}", dst], check=True, capture_output=True, text=True)
                return [TextContent(type="text", text=f"Copied to host: {dst}")]
        except subprocess.CalledProcessError as e:
            return [TextContent(type="text", text=f"Copy failed: {e.stderr}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    from mcp.server.stdio import stdio_server

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
