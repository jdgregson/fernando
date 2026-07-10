#!/usr/bin/env python3
"""Fernando MCP server: SilverBullet notebooks & notes.

Tools: notebooks_list, notebook_create, notebook_delete,
       notes_list, notes_read, notes_write, notes_search
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)
from _mcp_common import PROJECT_ROOT

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("notes")

_NOTEBOOKS_DIR = os.path.join(PROJECT_ROOT, "data", "notebooks")


def _nb_dir(notebook):
    """Get the data directory for a notebook, with path validation."""
    safe = os.path.basename(notebook)
    d = os.path.join(_NOTEBOOKS_DIR, safe)
    if not os.path.isdir(d):
        return None
    return d


def _nb_api(notebook):
    """Get the SilverBullet API URL for a running notebook."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
    from services.notebooks import get_notebook_port
    port = get_notebook_port(notebook)
    if not port:
        return None
    return f"http://localhost:{port}/.fs"


def _notes_list(notebook):
    d = _nb_dir(notebook)
    if not d:
        return {"error": f"Notebook '{notebook}' not found"}
    pages = []
    for root, dirs, files in os.walk(d):
        for f in sorted(files):
            if f.endswith(".md"):
                rel = os.path.relpath(os.path.join(root, f), d)
                pages.append(rel[:-3])
    return {"notebook": notebook, "pages": pages}


def _notes_read(notebook, page):
    api = _nb_api(notebook)
    if not api:
        # Fall back to reading from disk
        d = _nb_dir(notebook)
        if not d:
            return {"error": f"Notebook '{notebook}' not found"}
        fpath = os.path.join(d, page + ".md")
        fpath = os.path.realpath(fpath)
        if not fpath.startswith(os.path.realpath(d) + "/"):
            return {"error": "Invalid page path"}
        if not os.path.isfile(fpath):
            return {"error": f"Page not found: {page}"}
        with open(fpath) as f:
            return {"notebook": notebook, "page": page, "content": f.read()}
    try:
        encoded = urllib.parse.quote(page + ".md", safe="/")
        resp = urllib.request.urlopen(f"{api}/{encoded}", timeout=5)
        return {"notebook": notebook, "page": page, "content": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"Page not found: {page}"}
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _notes_write(notebook, page, content):
    api = _nb_api(notebook)
    if not api:
        # Fall back to writing to disk
        d = _nb_dir(notebook)
        if not d:
            return {"error": f"Notebook '{notebook}' not found"}
        fpath = os.path.join(d, page + ".md")
        fpath = os.path.realpath(fpath)
        if not fpath.startswith(os.path.realpath(d) + "/"):
            return {"error": "Invalid page path"}
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(content)
        return {"status": "written", "notebook": notebook, "page": page}
    try:
        encoded = urllib.parse.quote(page + ".md", safe="/")
        data = content.encode("utf-8")
        req = urllib.request.Request(
            f"{api}/{encoded}",
            data=data,
            method="PUT",
            headers={"Content-Type": "text/markdown"},
        )
        urllib.request.urlopen(req, timeout=5)
        return {"status": "written", "notebook": notebook, "page": page}
    except Exception as e:
        return {"error": str(e)}


def _notes_search(notebook, query):
    d = _nb_dir(notebook)
    if not d:
        return {"error": f"Notebook '{notebook}' not found"}
    results = []
    q_lower = query.lower()
    for root, dirs, files in os.walk(d):
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            fpath = os.path.join(root, f)
            page = os.path.relpath(fpath, d)[:-3]
            try:
                with open(fpath, "r") as fh:
                    for i, line in enumerate(fh, 1):
                        if q_lower in line.lower():
                            results.append({"page": page, "line": i, "text": line.rstrip()})
            except Exception:
                continue
    return {"notebook": notebook, "query": query, "results": results}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="notebooks_list",
            description="List all configured notebooks and their running status. Pass any value for key.",
            inputSchema={"type": "object", "properties": {"key": {"type": "string", "description": "Unused, pass any value"}}, "required": ["key"]},
        ),
        Tool(
            name="notebook_create",
            description="Create a new notebook. Name must be lowercase alphanumeric with hyphens/underscores.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Notebook name (e.g. 'recipes', 'work-notes')"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="notebook_delete",
            description="Delete a notebook from the config (data directory is preserved).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Notebook name to delete"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="notes_list",
            description="List all pages in a SilverBullet notebook. Returns page names (without .md extension).",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {"type": "string", "description": "Notebook name (e.g. 'default', 'recipes')"},
                },
                "required": ["notebook"],
            },
        ),
        Tool(
            name="notes_read",
            description="Read a note page by name. Returns the full markdown content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {"type": "string", "description": "Notebook name (e.g. 'default', 'recipes')"},
                    "page": {"type": "string", "description": "Page name (e.g. 'index', 'Fernando Theme')"},
                },
                "required": ["notebook", "page"],
            },
        ),
        Tool(
            name="notes_write",
            description="Create or overwrite a note page. Content is markdown.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {"type": "string", "description": "Notebook name (e.g. 'default', 'recipes')"},
                    "page": {"type": "string", "description": "Page name (e.g. 'Research/AWS Bedrock')"},
                    "content": {"type": "string", "description": "Markdown content"},
                },
                "required": ["notebook", "page", "content"],
            },
        ),
        Tool(
            name="notes_search",
            description="Search notes by keyword (grep). Returns matching lines with page names.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook": {"type": "string", "description": "Notebook name (e.g. 'default', 'recipes')"},
                    "query": {"type": "string", "description": "Search term"},
                },
                "required": ["notebook", "query"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "notebooks_list":
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
        from services.notebooks import list_notebooks
        result = {"notebooks": list_notebooks()}
    elif name == "notebook_create":
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
        from services.notebooks import create_notebook
        nb, err = create_notebook(arguments["name"])
        result = {"error": err} if err else nb
    elif name == "notebook_delete":
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
        from services.notebooks import delete_notebook
        err = delete_notebook(arguments["name"])
        result = {"error": err} if err else {"status": "deleted", "name": arguments["name"]}
    elif name == "notes_list":
        result = _notes_list(arguments["notebook"])
    elif name == "notes_read":
        result = _notes_read(arguments["notebook"], arguments["page"])
    elif name == "notes_write":
        result = _notes_write(arguments["notebook"], arguments["page"], arguments["content"])
    elif name == "notes_search":
        result = _notes_search(arguments["notebook"], arguments["query"])
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
