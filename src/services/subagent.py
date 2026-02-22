import json
import os
import random
import string
import subprocess
from datetime import datetime

SUBAGENT_DIR = "/home/coder/fernando/subagents"


class SubagentService:
    def list_subagents(self):
        if not os.path.exists(SUBAGENT_DIR):
            return []

        tasks = []
        for task_id in os.listdir(SUBAGENT_DIR):
            task_path = f"{SUBAGENT_DIR}/{task_id}"
            if not os.path.isdir(task_path):
                continue

            task_file = f"{task_path}/task.json"
            status_file = f"{task_path}/status.json"

            if not os.path.exists(task_file):
                continue

            with open(task_file) as f:
                task_data = json.load(f)

            status_data = {}
            if os.path.exists(status_file):
                with open(status_file) as f:
                    status_data = json.load(f)

            tasks.append(
                {
                    "task_id": task_id,
                    "task": task_data.get("task", ""),
                    "created_at": task_data.get("created_at", ""),
                    "schedule": task_data.get("schedule"),
                    "status": status_data.get("status", "unknown"),
                    "progress": status_data.get("progress", 0),
                    "current_step": status_data.get("current_step", ""),
                }
            )

        return sorted(tasks, key=lambda x: x.get("created_at", ""), reverse=True)

    def create_subagent(self, task_id, task, context_path=None, schedule=None):
        random_id = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=12)
        )
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
                    "context_path": context_file,
                    "schedule": schedule,
                    "created_at": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        with open(f"{workspace}/status.json", "w") as f:
            json.dump(
                {
                    "status": "scheduled" if schedule else "pending",
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

        instructions = f"""You are a subagent working on a delegated task.

Task ID: {task_id_with_random}
Task: {task}
Workspace: {workspace}

=== CONTEXT MANAGEMENT ===
{context_instructions}

Update {workspace}/status.json frequently with your progress.
Save proof of work to {workspace}/proof/
Write final results to {workspace}/results/final.json
"""

        instructions_file = f"{workspace}/instructions.txt"
        with open(instructions_file, "w") as f:
            f.write(instructions)

        session_name = f"subagent-{task_id_with_random}"

        script_path = f"{workspace}/spawn.sh"
        with open(script_path, "w") as f:
            f.write(
                f"""#!/bin/bash
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SESSION_NAME="{session_name}-$TIMESTAMP"
tmux new-session -d -s "$SESSION_NAME" kiro-cli chat --trust-all-tools "Read the instructions from {instructions_file} and execute the task described there."
"""
            )
        os.chmod(script_path, 0o755)

        if schedule:
            if schedule.startswith("at "):
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
                result = subprocess.run(
                    ["crontab", "-l"], capture_output=True, text=True
                )
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

    def get_subagent_status(self, task_id):
        workspace = f"{SUBAGENT_DIR}/{task_id}"
        status_file = f"{workspace}/status.json"
        task_file = f"{workspace}/task.json"

        if not os.path.exists(status_file):
            return {"error": "Task not found"}

        with open(task_file) as f:
            task_data = json.load(f)

        with open(status_file) as f:
            status = json.load(f)

        result_file = f"{workspace}/results/final.json"
        if os.path.exists(result_file):
            with open(result_file) as f:
                status["final_result"] = json.load(f)

        status["task"] = task_data.get("task", "")
        status["task_id"] = task_id
        status["schedule"] = task_data.get("schedule")

        return status

    def terminate_subagent(self, task_id):
        session_name = f"subagent-{task_id}"
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
        )
        return {"task_id": task_id, "terminated": result.returncode == 0}

    def delete_subagent(self, task_id):
        workspace = f"{SUBAGENT_DIR}/{task_id}"
        if os.path.exists(workspace):
            subprocess.run(["rm", "-rf", workspace])
        return {"task_id": task_id, "deleted": True}

    def get_at_jobs(self):
        """Get all scheduled 'at' jobs"""
        result = subprocess.run(["atq"], capture_output=True, text=True)
        jobs = []
        for line in result.stdout.strip().split("\n"):
            if line:
                parts = line.split()
                if len(parts) >= 5:
                    job_id = parts[0]
                    # Parse date/time from atq output
                    jobs.append(
                        {
                            "job_id": job_id,
                            "scheduled_time": " ".join(parts[1:5]),
                            "type": "at",
                        }
                    )
        return jobs

    def get_cron_jobs(self):
        """Get all cron jobs for current user"""
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        jobs = []
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) >= 6:
                        cron_time = " ".join(parts[:5])
                        command = " ".join(parts[5:])
                        # Extract task_id from spawn.sh path if present
                        task_id = None
                        if "subagents/" in command and "/spawn.sh" in command:
                            task_id = command.split("subagents/")[1].split("/")[0]
                        jobs.append(
                            {
                                "cron_time": cron_time,
                                "command": command,
                                "task_id": task_id,
                                "type": "cron",
                            }
                        )
        return jobs

    def remove_at_job(self, job_id):
        """Remove an 'at' job"""
        subprocess.run(["atrm", job_id])
        return {"job_id": job_id, "removed": True}

    def remove_cron_job(self, task_id):
        """Remove a cron job by task_id"""
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            new_lines = [
                line
                for line in lines
                if not (f"subagents/{task_id}/" in line and "/spawn.sh" in line)
            ]
            subprocess.run(["crontab", "-"], input="\n".join(new_lines), text=True)
        return {"task_id": task_id, "removed": True}


subagent_service = SubagentService()
