import json
import os
import random
import string
import subprocess
from datetime import datetime

TMUX_CMD = ["tmux", "new-session", "-d", "-s"]
KIRO_CMD = ["kiro-cli", "chat", "--trust-all-tools", "--model claude-opus-4.6"]

SUBAGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "subagents")

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
  "task": "(your task description)",
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


def _build_context_instructions(context_file):
    if context_file:
        return f"""A context file is available at: {context_file}
- READ this file at the start of your task to understand prior work and decisions
- APPEND updates to this file as you make progress (use fs_write append command)
- Include: key findings, decisions made, blockers encountered, next steps
- This allows context to persist across multiple subagent invocations"""
    return "No persistent context file provided for this task."


def create_workspace(task_id):
    random_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    task_id_with_random = f"{task_id}-{random_id}"
    workspace = f"{SUBAGENT_DIR}/{task_id_with_random}"
    for subdir in ["proof/screenshots", "proof/outputs", "proof/logs", "results"]:
        os.makedirs(f"{workspace}/{subdir}", exist_ok=True)
    return task_id_with_random, workspace


def resolve_context_path(context_path):
    if not context_path:
        return None
    context_file = os.path.abspath(os.path.expanduser(context_path))
    if not os.path.exists(context_file):
        with open(context_file, "w") as f:
            f.write("")
    return context_file


def write_task_json(workspace, task_id, task, context_file=None, additional_context="", schedule=None):
    with open(f"{workspace}/task.json", "w") as f:
        json.dump(
            {
                "task_id": task_id,
                "task": task,
                "additional_context": additional_context,
                "context_path": context_file,
                "schedule": schedule,
                "created_at": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )


def write_status_json(workspace, scheduled=False):
    with open(f"{workspace}/status.json", "w") as f:
        json.dump(
            {
                "status": "scheduled" if scheduled else "pending",
                "progress": 0,
                "current_step": "waiting to start",
                "start_time": None,
                "end_time": None,
                "last_update": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )


def write_instructions(workspace, task_id, task, context_file=None, additional_context=""):
    context_instructions = _build_context_instructions(context_file)
    instructions = SUBAGENT_INSTRUCTIONS.format(
        workspace=workspace,
        task_id=task_id,
        task=task,
        context_instructions=context_instructions,
    )
    if additional_context:
        instructions += f"\n\nAdditional context:\n{additional_context}"
    instructions_file = f"{workspace}/instructions.txt"
    with open(instructions_file, "w") as f:
        f.write(instructions)
    return instructions_file


def write_spawn_script(workspace, session_name, instructions_file):
    script_path = f"{workspace}/spawn.sh"
    log_file = f"{workspace}/proof/logs/chat.log"
    with open(script_path, "w") as f:
        f.write(f"""#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SESSION_NAME="{session_name}-$TIMESTAMP"
{' '.join(TMUX_CMD)} "$SESSION_NAME" {' '.join(KIRO_CMD)} "Read the instructions from {instructions_file} and execute the task described there."
tmux set-option -t "$SESSION_NAME" mouse on
tmux set-option -t "$SESSION_NAME" status-style "bg=blue,fg=white"
tmux resize-window -t "$SESSION_NAME" -x 220 -y 50
tmux pipe-pane -t "$SESSION_NAME" -o "cat >> {log_file}"
""")
    os.chmod(script_path, 0o500)
    return script_path


def schedule_at(script_path, time_spec):
    subprocess.run(
        ["at", time_spec],
        input=f"{script_path}\n",
        capture_output=True,
        text=True,
    )


def schedule_cron(script_path, cron_schedule):
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""
    subprocess.run(
        ["crontab", "-"],
        input=existing + f"{cron_schedule} {script_path}\n",
        text=True,
    )


def run_immediately(session_name, instructions_file):
    workspace = os.path.dirname(instructions_file)
    script_path = f"{workspace}/spawn.sh"
    subprocess.run(["bash", script_path])


def get_subagent_status(task_id):
    workspace = f"{SUBAGENT_DIR}/{task_id}"
    status_file = f"{workspace}/status.json"
    task_file = f"{workspace}/task.json"

    if not os.path.exists(status_file):
        return {"error": "Task not found"}

    with open(status_file) as f:
        status = json.load(f)

    if os.path.exists(task_file):
        with open(task_file) as f:
            task_data = json.load(f)
        status["task"] = task_data.get("task", "")
        status["task_id"] = task_id
        status["schedule"] = task_data.get("schedule")

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
        task_path = f"{SUBAGENT_DIR}/{task_id}"
        if not os.path.isdir(task_path) or not os.path.exists(f"{task_path}/task.json"):
            continue
        status = get_subagent_status(task_id)
        tasks.append({"task_id": task_id, "status": status})
    return tasks


def terminate_subagent(task_id):
    prefix = f"subagent-{task_id}"
    # Find actual session name (spawn.sh appends a timestamp)
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    killed = False
    for name in result.stdout.strip().split("\n"):
        if name.startswith(prefix):
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True, text=True)
            killed = True
    remove_cron_job(task_id)
    return {"task_id": task_id, "terminated": killed}


def delete_subagent(task_id):
    workspace = f"{SUBAGENT_DIR}/{task_id}"
    remove_cron_job(task_id)
    if os.path.exists(workspace):
        subprocess.run(["rm", "-rf", workspace])
    return {"task_id": task_id, "deleted": True}


def get_cron_jobs():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    jobs = []
    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            if line and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 6:
                    task_id = None
                    command = " ".join(parts[5:])
                    if "subagents/" in command and "/spawn.sh" in command:
                        task_id = command.split("subagents/")[1].split("/")[0]
                    if task_id:
                        jobs.append({"cron_time": " ".join(parts[:5]), "command": command, "task_id": task_id, "type": "cron"})
    return jobs


def get_at_jobs():
    result = subprocess.run(["atq"], capture_output=True, text=True)
    jobs = []
    for line in result.stdout.strip().split("\n"):
        if line:
            parts = line.split()
            if len(parts) >= 5:
                jobs.append({"job_id": parts[0], "scheduled_time": " ".join(parts[1:5]), "type": "at"})
    return jobs


def remove_cron_job(task_id):
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        new_lines = [l for l in lines if not (f"subagents/{task_id}/" in l and "/spawn.sh" in l)]
        subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n", text=True)
    return {"task_id": task_id, "removed": True}


def remove_at_job(job_id):
    subprocess.run(["atrm", job_id])
    return {"job_id": job_id, "removed": True}
