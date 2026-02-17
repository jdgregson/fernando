#!/usr/bin/env python3
"""
MCP Server for Kasm Desktop Control
Allows AI to control the Kasm desktop via keyboard/mouse commands
"""
import asyncio
import subprocess
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("kasm-desktop")

def exec_in_kasm(cmd):
    """Execute command inside Kasm container"""
    result = subprocess.run(
        ['docker', 'exec', 'fernando-kasm-noble-1', 'bash', '-c', cmd],
        capture_output=True,
        text=True
    )
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
                "required": ["text"]
            }
        ),
        Tool(
            name="press_key",
            description="Press a keyboard key or key combination (e.g., 'Return', 'ctrl+c', 'alt+Tab')",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key or key combination to press"}
                },
                "required": ["key"]
            }
        ),
        Tool(
            name="click_mouse",
            description="Click mouse at coordinates or click a button",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (optional)"},
                    "y": {"type": "integer", "description": "Y coordinate (optional)"},
                    "button": {"type": "integer", "description": "Mouse button (1=left, 2=middle, 3=right)", "default": 1}
                }
            }
        ),
        Tool(
            name="open_application",
            description="Open an application on the desktop",
            inputSchema={
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "Application name (e.g., 'firefox', 'terminal', 'gedit')"}
                },
                "required": ["app"]
            }
        ),
        Tool(
            name="run_command",
            description="Run a shell command in the Kasm desktop",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"}
                },
                "required": ["command"]
            }
        ),
        Tool(
            name="screenshot",
            description="Take a screenshot of the Kasm desktop",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="scroll",
            description="Scroll the mouse wheel up or down at current position or specified coordinates",
            inputSchema={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "Direction to scroll: 'up' or 'down'", "enum": ["up", "down"]},
                    "amount": {"type": "integer", "description": "Number of scroll clicks (default: 3)", "default": 3},
                    "x": {"type": "integer", "description": "X coordinate to scroll at (optional, moves mouse first)"},
                    "y": {"type": "integer", "description": "Y coordinate to scroll at (optional, moves mouse first)"}
                },
                "required": ["direction"]
            }
        ),
        Tool(
            name="move_mouse",
            description="Move mouse cursor to coordinates without clicking",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate"},
                    "y": {"type": "integer", "description": "Y coordinate"}
                },
                "required": ["x", "y"]
            }
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
                    "button": {"type": "integer", "description": "Mouse button (1=left, 2=middle, 3=right)", "default": 1},
                    "delay": {"type": "number", "description": "Delay in seconds after mousedown and before mouseup (default: 0.1)", "default": 0.1}
                },
                "required": ["x1", "y1", "x2", "y2"]
            }
        ),
        Tool(
            name="get_mouse_position",
            description="Get current mouse cursor position",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="double_click",
            description="Double-click at coordinates or current position",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (optional)"},
                    "y": {"type": "integer", "description": "Y coordinate (optional)"}
                }
            }
        ),
        Tool(
            name="right_click",
            description="Right-click at coordinates or current position",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (optional)"},
                    "y": {"type": "integer", "description": "Y coordinate (optional)"}
                }
            }
        ),
        Tool(
            name="get_window_info",
            description="List open windows with their IDs, names, and positions",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="focus_window",
            description="Bring a window to front by name or ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "Window name or ID"}
                },
                "required": ["window"]
            }
        ),
        Tool(
            name="get_screen_size",
            description="Get desktop screen resolution",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="get_clipboard",
            description="Get text content from clipboard",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="set_clipboard",
            description="Set text content to clipboard",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy to clipboard"}
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="desktop_exec",
            description="Execute a bash command in the Kasm desktop container",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command to execute"}
                },
                "required": ["command"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "type_text":
        text = arguments["text"].replace("'", "'\\''")
        result = exec_in_kasm(f"DISPLAY=:1 xdotool type '{text}'")
        return [TextContent(type="text", text=f"Typed: {arguments['text']}\n{result}")]
    
    elif name == "press_key":
        key = arguments["key"]
        result = exec_in_kasm(f"DISPLAY=:1 xdotool key {key}")
        return [TextContent(type="text", text=f"Pressed: {key}\n{result}")]
    
    elif name == "click_mouse":
        x = arguments.get("x")
        y = arguments.get("y")
        button = arguments.get("button", 1)
        
        cmd = f"DISPLAY=:1 xdotool "
        if x is not None and y is not None:
            cmd += f"mousemove {x} {y} "
        cmd += f"click {button}"
        
        result = exec_in_kasm(cmd)
        return [TextContent(type="text", text=f"Clicked at ({x}, {y}) button {button}\n{result}")]
    
    elif name == "open_application":
        app = arguments["app"]
        result = exec_in_kasm(f"DISPLAY=:1 {app} &")
        return [TextContent(type="text", text=f"Opened: {app}\n{result}")]
    
    elif name == "run_command":
        cmd = arguments["command"]
        result = exec_in_kasm(f"DISPLAY=:1 {cmd}")
        return [TextContent(type="text", text=f"Command output:\n{result}")]
    
    elif name == "screenshot":
        filename = "screenshot.png"
        host_path = f"/home/coder/fernando/data/desktop/{filename}"
        exec_in_kasm(f"DISPLAY=:1 scrot --overwrite /home/kasm-user/{filename}")
        return [TextContent(type="text", text=f"Screenshot saved to: {host_path}")]
    
    elif name == "scroll":
        direction = arguments["direction"]
        amount = arguments.get("amount", 3)
        button = "4" if direction == "up" else "5"
        x = arguments.get("x")
        y = arguments.get("y")
        
        cmd = "DISPLAY=:1 xdotool "
        if x is not None and y is not None:
            cmd += f"mousemove {x} {y} "
        cmd += f"click --repeat {amount} {button}"
        
        result = exec_in_kasm(cmd)
        pos = f" at ({x}, {y})" if x is not None and y is not None else ""
        return [TextContent(type="text", text=f"Scrolled {direction} {amount} times{pos}\n{result}")]
    
    elif name == "move_mouse":
        x = arguments["x"]
        y = arguments["y"]
        result = exec_in_kasm(f"DISPLAY=:1 xdotool mousemove {x} {y}")
        return [TextContent(type="text", text=f"Moved mouse to ({x}, {y})\n{result}")]
    
    elif name == "drag_mouse":
        x1 = arguments["x1"]
        y1 = arguments["y1"]
        x2 = arguments["x2"]
        y2 = arguments["y2"]
        button = arguments.get("button", 1)
        delay = arguments.get("delay", 0.1)
        
        result = exec_in_kasm(f"DISPLAY=:1 xdotool mousemove {x1} {y1} mousedown {button} sleep {delay} mousemove {x2} {y2} sleep {delay} mouseup {button}")
        return [TextContent(type="text", text=f"Dragged from ({x1}, {y1}) to ({x2}, {y2}) with {delay}s delay\n{result}")]
    
    elif name == "get_mouse_position":
        result = exec_in_kasm("DISPLAY=:1 xdotool getmouselocation --shell")
        return [TextContent(type="text", text=f"Mouse position:\n{result}")]
    
    elif name == "double_click":
        x = arguments.get("x")
        y = arguments.get("y")
        
        cmd = "DISPLAY=:1 xdotool "
        if x is not None and y is not None:
            cmd += f"mousemove {x} {y} "
        cmd += "click --repeat 2 1"
        
        result = exec_in_kasm(cmd)
        return [TextContent(type="text", text=f"Double-clicked at ({x}, {y})\n{result}")]
    
    elif name == "right_click":
        x = arguments.get("x")
        y = arguments.get("y")
        
        cmd = "DISPLAY=:1 xdotool "
        if x is not None and y is not None:
            cmd += f"mousemove {x} {y} "
        cmd += "click 3"
        
        result = exec_in_kasm(cmd)
        return [TextContent(type="text", text=f"Right-clicked at ({x}, {y})\n{result}")]
    
    elif name == "get_window_info":
        result = exec_in_kasm("DISPLAY=:1 wmctrl -lG")
        return [TextContent(type="text", text=f"Open windows:\n{result}")]
    
    elif name == "focus_window":
        window = arguments["window"]
        # Try by name first, then by ID if it looks like a hex ID
        if window.startswith("0x"):
            result = exec_in_kasm(f"DISPLAY=:1 wmctrl -ia {window}")
        else:
            result = exec_in_kasm(f"DISPLAY=:1 wmctrl -a '{window}'")
        return [TextContent(type="text", text=f"Focused window: {window}\n{result}")]
    
    elif name == "get_screen_size":
        result = exec_in_kasm("DISPLAY=:1 xdpyinfo | grep dimensions")
        return [TextContent(type="text", text=f"Screen size:\n{result}")]
    
    elif name == "get_clipboard":
        result = exec_in_kasm("DISPLAY=:1 xclip -selection clipboard -o")
        return [TextContent(type="text", text=f"Clipboard content:\n{result}")]
    
    elif name == "set_clipboard":
        text = arguments["text"].replace("'", "'\\''")
        result = exec_in_kasm(f"echo '{text}' | DISPLAY=:1 xclip -selection clipboard")
        return [TextContent(type="text", text=f"Set clipboard to: {arguments['text']}\n{result}")]
    
    elif name == "desktop_exec":
        cmd = arguments["command"]
        result = exec_in_kasm(cmd)
        return [TextContent(type="text", text=f"Command output:\n{result}")]
    
    return [TextContent(type="text", text=f"Unknown tool: {name}")]

async def main():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
