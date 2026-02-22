#!/usr/bin/env python3
import asyncio
import json
import os
import random
import string
import subprocess
from datetime import datetime
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("fernando")

SUBAGENT_DIR = "/home/coder/fernando/subagents"

SUBAGENT_INSTRUCTIONS = """
You are a subagent working on a delegated task. Follow these rules STRICTLY:

=== MANDATORY VERIFICATION PROTOCOL ===
BEFORE marking any task as complete, you MUST:

1. VERIFY YOUR WORK: Re-check that you actually completed what was asked
   - For desktop tasks: Take a final screenshot and ANALYZE it yourself
   - For file tasks: Read back the files you created/modified
   - For search tasks: Confirm the search results are visible and relevant
   - For data tasks: Validate the output contains the expected information

2. EVIDENCE REQUIREMENT: Your final screenshot/output must PROVE task completion
   - Screenshot must show the END STATE, not an intermediate step
   - If asked to search for X, screenshot must show X search results
   - If asked to create Y, output must contain Y
   - If asked to navigate to Z, screenshot must show Z loaded

3. SELF-CRITIQUE: Before writing final.json, ask yourself:
   - "Does my final screenshot/output actually prove I did what was asked?"
   - "If someone only saw my final proof, would they believe the task is done?"
   - "Did I complete ALL parts of the task, or just some?"

4. IF VERIFICATION FAILS:
   - DO NOT mark as complete
   - Continue working until verification passes
   - Update status.json with "verification_failed" and retry

=== PROOF OF WORK ===
- Take screenshots after EACH action (especially desktop)
- Final screenshot MUST show completed task state
- Save to: {workspace}/proof/screenshots/
- Name format: 01_description.png, 02_description.png, etc.

=== OUTPUT FORMAT ===
- Write outputs as JSON to: {workspace}/proof/outputs/
- Never write to terminal
- Log all actions to: {workspace}/proof/logs/execution.log

=== STATUS UPDATES ===
Update {workspace}/status.json frequently:
{{
  "status": "in_progress|completed|failed",
  "progress": 0-100,
  "current_step": "description",
  "verification_passed": true|false,
  "start_time": "ISO timestamp",
  "end_time": "ISO timestamp or null",
  "last_update": "ISO timestamp"
}}

=== CONTEXT MANAGEMENT ===
{context_instructions}

=== FINAL RESULT ===
Write {workspace}/results/final.json ONLY after verification passes:
{{
  "task_id": "{task_id}",
  "task": "{task}",
  "status": "completed|failed",
  "result": {{
    "success": true|false,
    "verification_passed": true|false,
    "verification_evidence": "description of what proves completion",
    "actions_performed": ["action1", "action2"],
    "screenshots_captured": ["file1.png", "file2.png"],
    "final_screenshot_shows": "detailed description of what final screenshot proves"
  }},
  "completed_at": "ISO timestamp"
}}

=== YOUR TASK ===
Task ID: {task_id}
Task: {task}
Workspace: {workspace}

Begin work now. Update status.json with start_time immediately.
"""


def create_subagent(
    task_id, task, additional_context="", context_path=None, schedule=None
):
    random_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    task_id_with_random = f"{task_id}-{random_id}"
    workspace = f"{SUBAGENT_DIR}/{task_id_with_random}"
    os.makedirs(f"{workspace}/proof/screenshots", exist_ok=True)
    os.makedirs(f"{workspace}/proof/outputs", exist_ok=True)
    os.makedirs(f"{workspace}/proof/logs", exist_ok=True)
    os.makedirs(f"{workspace}/results", exist_ok=True)

    # Handle context file
    context_file = None
    if context_path:
        context_file = os.path.abspath(os.path.expanduser(context_path))
        if not os.path.exists(context_file):
            with open(context_file, "w") as f:
                f.write("")

    with open(f"{workspace}/task.json", "w") as f:
        json.dump(
            {
                "task_id": task_id_with_random,
                "task": task,
                "additional_context": additional_context,
                "context_path": context_file,
                "created_at": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )

    with open(f"{workspace}/status.json", "w") as f:
        json.dump(
            {
                "status": "scheduled",
                "progress": 0,
                "current_step": "waiting to start",
                "start_time": None,
                "end_time": None,
                "last_update": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )

    # Build context instructions
    if context_file:
        context_instructions = f"""A context file is available at: {context_file}
- READ this file at the start of your task to understand prior work and decisions
- APPEND updates to this file as you make progress (use fs_write append command)
- Include: key findings, decisions made, blockers encountered, next steps
- This allows context to persist across multiple subagent invocations"""
    else:
        context_instructions = "No persistent context file provided for this task."

    instructions = SUBAGENT_INSTRUCTIONS.format(
        workspace=workspace,
        task_id=task_id_with_random,
        task=task,
        context_instructions=context_instructions,
    )

    if additional_context:
        instructions += f"\n\nAdditional context:\n{additional_context}"

    # Write instructions to file
    instructions_file = f"{workspace}/instructions.txt"
    with open(instructions_file, "w") as f:
        f.write(instructions)

    session_name = f"subagent-{task_id_with_random}"

    # Create spawn script for scheduled execution
    script_path = f"{workspace}/spawn.sh"
    with open(script_path, "w") as f:
        f.write(f"""#!/bin/bash
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SESSION_NAME="{session_name}-$TIMESTAMP"
tmux new-session -d -s "$SESSION_NAME" kiro-cli chat --trust-all-tools "Read the instructions from {instructions_file} and execute the task described there."
""")
    os.chmod(script_path, 0o755)

    # Handle scheduling
    if schedule:
        if schedule.startswith("at "):
            # "at 14:30"
            time_spec = schedule[3:]
            subprocess.run(
                ["at", time_spec],
                input=f"{script_path}\n",
                capture_output=True,
                text=True,
            )
            return {
                "task_id": task_id_with_random,
                "session_name": session_name,
                "workspace": workspace,
                "scheduled_at": time_spec,
            }

        elif schedule.startswith("every "):
            # "every 5 minutes"
            interval = schedule[6:]
            cron_patterns = {
                "minute": "* * * * *",
                "5 minutes": "*/5 * * * *",
                "10 minutes": "*/10 * * * *",
                "15 minutes": "*/15 * * * *",
                "30 minutes": "*/30 * * * *",
                "hour": "0 * * * *",
                "day": "0 0 * * *",
                "week": "0 0 * * 0",
            }
            cron_time = cron_patterns.get(interval, "*/5 * * * *")
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            existing = result.stdout if result.returncode == 0 else ""
            subprocess.run(
                ["crontab", "-"],
                input=existing + f"{cron_time} {script_path}\n",
                text=True,
            )
            return {
                "task_id": task_id_with_random,
                "session_name": session_name,
                "workspace": workspace,
                "recurring": interval,
                "cron": cron_time,
            }

    # Immediate execution
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "kiro-cli",
            "chat",
            "--trust-all-tools",
            f"Read the instructions from {instructions_file} and execute the task described there.",
        ]
    )

    return {
        "task_id": task_id_with_random,
        "session_name": session_name,
        "workspace": workspace,
    }


def get_subagent_status(task_id):
    workspace = f"{SUBAGENT_DIR}/{task_id}"
    status_file = f"{workspace}/status.json"

    if not os.path.exists(status_file):
        return {"error": "Task not found"}

    with open(status_file) as f:
        status = json.load(f)

    result_file = f"{workspace}/results/final.json"
    if os.path.exists(result_file):
        with open(result_file) as f:
            status["final_result"] = json.load(f)

    return status


def list_subagents():
    if not os.path.exists(SUBAGENT_DIR):
        return []

    tasks = []
    for task_id in os.listdir(SUBAGENT_DIR):
        status = get_subagent_status(task_id)
        tasks.append({"task_id": task_id, "status": status})

    return tasks


def terminate_subagent(task_id):
    session_name = f"subagent-{task_id}"
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_name], capture_output=True, text=True
    )
    return {
        "task_id": task_id,
        "session_name": session_name,
        "terminated": result.returncode == 0,
    }


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="spawn_subagent",
            description="Spawn a subagent in a new tmux session to work on a delegated task. The subagent will save proof of work and communicate progress via JSON files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique identifier for this task (e.g., 'research-aws-pricing', 'debug-issue-123')",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description for the subagent",
                    },
                    "additional_context": {
                        "type": "string",
                        "description": "Optional additional context or instructions",
                    },
                    "context_path": {
                        "type": "string",
                        "description": "Optional path to a context file that will be read at task start and updated with progress. File will be created if it doesn't exist.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "Schedule string: 'at 14:30' for specific time, 'every 5 minutes/hour/day/week' for recurring. If not provided, starts immediately.",
                    },
                },
                "required": ["task_id", "task"],
            },
        ),
        Tool(
            name="get_subagent_status",
            description="Check the status and progress of a subagent task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to check"}
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="list_subagents",
            description="List all subagent tasks and their current status",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="terminate_subagent",
            description="Terminate a subagent tmux session",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to terminate",
                    }
                },
                "required": ["task_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "spawn_subagent":
        result = create_subagent(
            arguments["task_id"],
            arguments["task"],
            arguments.get("additional_context", ""),
            arguments.get("context_path"),
            arguments.get("schedule"),
        )
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_subagent_status":
        result = get_subagent_status(arguments["task_id"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "list_subagents":
        result = list_subagents()
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "terminate_subagent":
        result = terminate_subagent(arguments["task_id"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
