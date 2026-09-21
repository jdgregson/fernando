"""Small stdio server for native context-isolation tests (no external services)."""

import asyncio
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

name = sys.argv[1]
server = Server(name)


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name=name + "_probe",
            description="Context isolation fixture",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        )
    ]


@server.call_tool()
async def call_tool(tool, arguments):
    return [TextContent(type="text", text=name)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
