#!/usr/bin/env python3
import asyncio
import json
import os
import subprocess
from datetime import datetime
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("fernando")

SUBAGENT_DIR = "/home/coder/fernando/subagents"

SUBAGENT_INSTRUCTIONS = """
You are a subagent working on a delegated task. Follow these rules:

1. PROOF OF WORK: After each significant action (especially desktop actions), take a screenshot and save it to your proof directory
2. OUTPUT FORMAT: Write all outputs as JSON files to your outputs directory, not to terminal
3. STATUS UPDATES: Update status.json frequently with your progress
4. FINAL RESULT: When complete, write final.json with your deliverable
5. TRACK TIME: Update start_time when you begin and end_time when you finish in status.json

Your workspace: {workspace}
Task ID: {task_id}
Task: {task}

Directory structure:
- {workspace}/proof/screenshots/ - Save screenshots here
- {workspace}/proof/outputs/ - Save JSON outputs here  
- {workspace}/proof/logs/ - Save execution logs here
- {workspace}/results/ - Save final.json here
- {workspace}/status.json - Update this with progress

Status JSON format:
{{
  "status": "in_progress|completed|failed",
  "progress": 0-100,
  "current_step": "description",
  "start_time": "ISO timestamp when you started work",
  "end_time": "ISO timestamp when you finished (null if in progress)",
  "last_update": "ISO timestamp"
}}

Begin work now and immediately update status.json with start_time.
"""

def create_subagent(task_id, task, additional_context="", delay_seconds=0, schedule=None):
    workspace = f"{SUBAGENT_DIR}/{task_id}"
    os.makedirs(f"{workspace}/proof/screenshots", exist_ok=True)
    os.makedirs(f"{workspace}/proof/outputs", exist_ok=True)
    os.makedirs(f"{workspace}/proof/logs", exist_ok=True)
    os.makedirs(f"{workspace}/results", exist_ok=True)
    
    with open(f"{workspace}/task.json", "w") as f:
        json.dump({
            "task_id": task_id,
            "task": task,
            "additional_context": additional_context,
            "created_at": datetime.now().isoformat()
        }, f, indent=2)
    
    with open(f"{workspace}/status.json", "w") as f:
        json.dump({
            "status": "scheduled",
            "progress": 0,
            "current_step": "waiting to start",
            "start_time": None,
            "end_time": None,
            "last_update": datetime.now().isoformat()
        }, f, indent=2)
    
    instructions = SUBAGENT_INSTRUCTIONS.format(
        workspace=workspace,
        task_id=task_id,
        task=task
    )
    
    if additional_context:
        instructions += f"\n\nAdditional context:\n{additional_context}"
    
    session_name = f"subagent-{task_id}"
    
    # Create spawn script for scheduled execution
    script_path = f"{workspace}/spawn.sh"
    with open(script_path, "w") as f:
        f.write(f"""#!/bin/bash
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SESSION_NAME="{session_name}-$TIMESTAMP"
tmux new-session -d -s "$SESSION_NAME" kiro-cli chat -a
sleep 1
tmux send-keys -t "$SESSION_NAME" -l {repr(instructions)}
sleep 1
tmux send-keys -t "$SESSION_NAME" Enter
""")
    os.chmod(script_path, 0o755)
    
    # Handle scheduling
    if schedule:
        if schedule.startswith("at "):
            # "at 14:30"
            time_spec = schedule[3:]
            subprocess.run(["at", time_spec], input=script_path, capture_output=True, text=True)
            return {"task_id": task_id, "session_name": session_name, "workspace": workspace, "scheduled_at": time_spec}
        
        elif schedule.startswith("every "):
            # "every 5 minutes"
            interval = schedule[6:]
            cron_patterns = {
                "minute": "* * * * *", "5 minutes": "*/5 * * * *", "10 minutes": "*/10 * * * *",
                "15 minutes": "*/15 * * * *", "30 minutes": "*/30 * * * *", "hour": "0 * * * *",
                "day": "0 0 * * *", "week": "0 0 * * 0"
            }
            cron_time = cron_patterns.get(interval, "*/5 * * * *")
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            existing = result.stdout if result.returncode == 0 else ""
            subprocess.run(["crontab", "-"], input=existing + f"{cron_time} {script_path}\n", text=True)
            return {"task_id": task_id, "session_name": session_name, "workspace": workspace, "recurring": interval, "cron": cron_time}
    
    elif delay_seconds > 0:
        subprocess.run(["at", f"now + {delay_seconds} seconds"], input=script_path, capture_output=True, text=True)
        return {"task_id": task_id, "session_name": session_name, "workspace": workspace, "scheduled_in_seconds": delay_seconds}
    
    # Immediate execution
    subprocess.run(["tmux", "new-session", "-d", "-s", session_name, "kiro-cli", "chat", "-a"])
    
    import time
    time.sleep(1)
    
    subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", instructions])
    time.sleep(1)
    subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"])
    
    return {"task_id": task_id, "session_name": session_name, "workspace": workspace}

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
        tasks.append({
            "task_id": task_id,
            "status": status
        })
    
    return tasks

def terminate_subagent(task_id):
    session_name = f"subagent-{task_id}"
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
        text=True
    )
    return {
        "task_id": task_id,
        "session_name": session_name,
        "terminated": result.returncode == 0
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
                        "description": "Unique identifier for this task (e.g., 'research-aws-pricing', 'debug-issue-123')"
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description for the subagent"
                    },
                    "additional_context": {
                        "type": "string",
                        "description": "Optional additional context or instructions"
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Delay in seconds before starting (0 = start immediately)",
                        "default": 0
                    },
                    "schedule": {
                        "type": "string",
                        "description": "Schedule string: 'at 14:30' for specific time, 'every 5 minutes/hour/day/week' for recurring. Overrides delay_seconds if provided."
                    }
                },
                "required": ["task_id", "task"]
            }
        ),
        Tool(
            name="get_subagent_status",
            description="Check the status and progress of a subagent task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to check"
                    }
                },
                "required": ["task_id"]
            }
        ),
        Tool(
            name="list_subagents",
            description="List all subagent tasks and their current status",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="terminate_subagent",
            description="Terminate a subagent tmux session",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to terminate"
                    }
                },
                "required": ["task_id"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "spawn_subagent":
        result = create_subagent(
            arguments["task_id"],
            arguments["task"],
            arguments.get("additional_context", ""),
            arguments.get("delay_seconds", 0),
            arguments.get("schedule")
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
