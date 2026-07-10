#!/usr/bin/env python3
"""Fernando MCP server: inbound email automation rules.

Tools: create_automation_rule, list_automation_rules, delete_automation_rule
"""
import _mcp_common  # noqa: F401  (activates venv + sys.path)

import asyncio
import json

from src.services.automation import create_rule, list_rules, delete_rule, load_meta_policy
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("automation")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_automation_rule",
            description="Create an inbound automation rule. Rules are matched against incoming emails by the poller (runs every 60s). When matched, the action is taken (dispatch=spawn subagent, summary=spawn with body stripped, drop=ignore). Agent-created rules are constrained by meta-policy: only 'dispatch' and 'summary' actions allowed, must be fire_once, max 72h TTL, max 10 active agent rules. Use this to set up notifications for specific senders/subjects.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable rule name (e.g. 'github-notifications')"},
                    "purpose": {"type": "string", "description": "Why this rule exists — what the subagent should do with matched messages (e.g. 'Summarize GitHub PR notifications', 'Execute instructions from Jonathan'). This is shown to the subagent to scope its actions."},
                    "action": {"type": "string", "enum": ["dispatch", "summary"], "description": "What to do when matched. dispatch=spawn subagent with full message, summary=spawn with body stripped"},
                    "from_filter": {"type": "string", "description": "Email address or domain to match (e.g. 'notifications@github.com' or 'github.com')"},
                    "subject_contains": {"type": "string", "description": "Optional substring to match in subject"},
                    "body_contains": {"type": "string", "description": "Optional substring to match in email body"},
                    "fire_once": {"type": "boolean", "description": "If true, rule is deleted after first match (required for agent-created rules by default policy)"},
                    "ttl_hours": {"type": "number", "description": "Hours until rule expires (max 72 for agent-created rules)"},
                },
                "required": ["name", "from_filter", "purpose"],
            },
        ),
        Tool(
            name="list_automation_rules",
            description="List all active automation rules and the current meta-policy.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="delete_automation_rule",
            description="Delete an automation rule by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string", "description": "The rule ID to delete"},
                },
                "required": ["rule_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "create_automation_rule":
        rule = {
            "name": arguments["name"],
            "purpose": arguments["purpose"],
            "action": arguments.get("action", "dispatch"),
            "fire_once": arguments.get("fire_once", True),
            "created_by": "agent",
            "trigger": {
                "type": "inbound",
                "channel": "email",
                "from": arguments["from_filter"],
            },
        }
        if arguments.get("subject_contains"):
            rule["trigger"]["subject_contains"] = arguments["subject_contains"]
        if arguments.get("body_contains"):
            rule["trigger"]["body_contains"] = arguments["body_contains"]
        if arguments.get("ttl_hours"):
            rule["ttl_hours"] = arguments["ttl_hours"]
        created, err = create_rule(rule)
        result = created if created else {"error": err}
    elif name == "list_automation_rules":
        result = {"rules": list_rules(), "meta_policy": load_meta_policy()}
    elif name == "delete_automation_rule":
        delete_rule(arguments["rule_id"])
        result = {"status": "deleted", "rule_id": arguments["rule_id"]}
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
