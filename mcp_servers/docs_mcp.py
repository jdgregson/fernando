#!/usr/bin/env python3
"""Fernando MCP server: document generation.

Tools: create_pdf, create_docx
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)

import asyncio
import json

from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("docs")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
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
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "create_pdf":
        from docgen import create_pdf
        out = create_pdf(arguments["path"], arguments["content"], arguments.get("title"))
        result = {"status": "created", "path": out}
    elif name == "create_docx":
        from docgen import create_docx
        out = create_docx(arguments["path"], arguments["content"], arguments.get("title"))
        result = {"status": "created", "path": out}
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
