import json
import os
import random
import re
import string
import subprocess
import fcntl
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime

TMUX_CMD = ["tmux", "new-session", "-d", "-s"]


def _get_kiro_cmd():
    from src.services.settings import get as get_setting
    model = get_setting("default_model") or "claude-opus-4.6"
    return ["kiro-cli", "chat", "--legacy-ui", "--trust-all-tools", "--model", model]

SUBAGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "subagents")
_schedule_lock = threading.RLock()
_schedule_local = threading.local()


@contextmanager
def schedule_guard():
    with _schedule_lock:
        if getattr(_schedule_local, 'held', False):
            yield
            return
        os.makedirs(SUBAGENT_DIR, exist_ok=True)
        descriptor = os.open(os.path.join(SUBAGENT_DIR, '.schedule.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            _schedule_local.held = True
            try:
                yield
            finally:
                _schedule_local.held = False
                fcntl.flock(stream, fcntl.LOCK_UN)


def read_task(task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        return None
    path = os.path.join(SUBAGENT_DIR, task_id, 'task.json')
    if not os.path.isfile(path):
        return None
    with open(path) as stream:
        return json.load(stream)


def save_task(task_id, record):
    directory = os.path.join(SUBAGENT_DIR, task_id)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix='.task-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, os.path.join(directory, 'task.json'))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def task_for_session(manager, session_id):
    if os.path.isdir(SUBAGENT_DIR):
        for task_id in os.listdir(SUBAGENT_DIR):
            record = read_task(task_id)
            if record and (record.get('session_id') == session_id or session_id in record.get('session_ids', [])):
                return task_id, record
    session = manager.get_session(session_id)
    if session and session.display_name.startswith('subagent-'):
        task_id = session.display_name.removeprefix('subagent-')
        record = read_task(task_id)
        from src.services.acp import get_parent
        if record and (not record.get('parent_session_id') or record['parent_session_id'] == get_parent(session_id)):
            return task_id, record
    return None, None


def _read_crontab():
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and 'no crontab for' in result.stderr.lower():
        return ''
    raise RuntimeError('Could not read crontab: ' + result.stderr.strip())


def task_session_ids(manager, task_id, record):
    sessions = set(record.get('session_ids', []))
    if record.get('session_id'):
        sessions.add(record['session_id'])
    sessions.update(row['id'] for row in manager.list_sessions() if row['name'] == f'subagent-{task_id}')
    return sessions


def _write_crontab(contents):
    result = subprocess.run(['crontab', '-'], input=contents, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Could not update crontab: ' + result.stderr.strip())


def _task_cron_lines(contents, task_id):
    script = os.path.join(SUBAGENT_DIR, task_id, 'spawn.sh')
    return [line for line in contents.splitlines() if not line.lstrip().startswith('#') and script in line.split()]


def prepare_scheduled_spawn(name, parent_id=None):
    task_id = name.removeprefix('subagent-') if isinstance(name, str) and name.startswith('subagent-') else None
    record = read_task(task_id)
    if record and record.get('parent_session_id') and record['parent_session_id'] != parent_id:
        raise ValueError('Scheduled task parent does not match its owner')
    if record and record.get('schedule') and record.get('schedule_enabled') is False:
        raise ValueError('This task schedule has been canceled; no new agent was created')
    return task_id if record else None


def register_task_session(task_id, session_id, parent_id):
    if not task_id:
        return
    record = read_task(task_id)
    ids = record.setdefault('session_ids', [])
    if session_id not in ids:
        ids.append(session_id)
    record['session_id'] = session_id
    record['parent_session_id'] = parent_id
    save_task(task_id, record)


def manage_schedule(manager, actor, action, session_id=None, task_id=None, **changes):
    from src.services.acp import get_parent, is_child_of
    from src.services.subagent_schedules import resume_scheduler
    if action not in ('get', 'cancel', 'update'):
        raise ValueError('Unknown schedule action')
    if not isinstance(actor, str) or not actor:
        raise PermissionError('An identified caller is required')
    if session_id and task_id:
        raise ValueError('Specify session_id or task_id, not both')
    with schedule_guard():
        if task_id:
            record = read_task(task_id)
            if not record:
                raise ValueError('Task not found')
            owner = record.get('parent_session_id')
            own_task, _ = task_for_session(manager, actor)
            sessions = task_session_ids(manager, task_id, record)
            if not (actor == owner or own_task == task_id or any(is_child_of(sid, actor) for sid in sessions)):
                raise PermissionError('Only the task owner or its own worker can manage this schedule')
            session_id = record.get('session_id')
        else:
            session_id = session_id or actor
            if not isinstance(session_id, str):
                raise ValueError('Invalid session_id')
            if not ((actor == session_id and get_parent(actor)) or is_child_of(session_id, actor)):
                raise PermissionError('Only the child itself or its parent can manage this schedule')
            task_id, record = task_for_session(manager, session_id)
        row = resume_scheduler.get(session_id) if session_id else None
        if row:
            if action == 'cancel':
                row = resume_scheduler.cancel(session_id)
            elif action == 'update':
                row = resume_scheduler.update(session_id, **changes)
            return {**row, 'schedule_mode': 'resume', 'task_id': task_id}
        if not record or record.get('schedule_mode') == 'resume' or not record.get('schedule'):
            raise ValueError('No recurring schedule exists for this target; nothing was canceled')
        expression = record.get('cron_schedule') or record['schedule']
        if len(expression.split()) != 5:
            raise ValueError('This target is not a recurring schedule')
        lines = _task_cron_lines(_read_crontab(), task_id)
        if not lines and record.get('schedule_enabled') is not False:
            raise ValueError('No recurring schedule exists for this target; nothing was canceled')
        if action == 'cancel':
            record['schedule_enabled'] = False
            save_task(task_id, record)
            remove_cron_job(task_id)
            lines = []
        elif action == 'update':
            if set(changes) != {'cron_schedule'}:
                raise ValueError('Fresh-agent schedules support cron_schedule updates only; timezone and wake_message apply to resume schedules')
            if record.get('schedule_enabled') is False:
                raise ValueError('This schedule is canceled')
            schedule_cron(os.path.join(SUBAGENT_DIR, task_id, 'spawn.sh'), changes['cron_schedule'])
            expression = changes['cron_schedule']
            record.update(schedule=expression, cron_schedule=expression)
            save_task(task_id, record)
        return {'task_id': task_id, 'schedule_mode': 'spawn', 'cron_schedule': expression,
                'enabled': bool(lines), 'session_id': session_id}


def cancel_origin_schedule(manager, session_id):
    from src.services.subagent_schedules import resume_scheduler
    with schedule_guard():
        resume_scheduler.cancel(session_id, 'terminated')
        task_id, record = task_for_session(manager, session_id)
        if record and record.get('schedule') and record.get('schedule_mode', 'spawn') == 'spawn':
            record['schedule_enabled'] = False
            save_task(task_id, record)
            remove_cron_job(task_id)


def report_startup_failure(manager, session_id, parent_id, name, error):
    task_id = name.removeprefix('subagent-')
    if name.startswith('subagent-') and re.fullmatch(r'[A-Za-z0-9_-]{1,173}', task_id):
        workspace = os.path.join(SUBAGENT_DIR, task_id)
        status_path = os.path.join(workspace, 'status.json')
        if os.path.isfile(status_path):
            with open(status_path) as stream:
                status = json.load(stream)
            now = datetime.now().isoformat()
            status.update(status='failed', current_step='startup failed', error=error,
                          verification_passed=False, end_time=now, last_update=now)
            temporary = status_path + '.tmp'
            with open(temporary, 'w') as stream:
                json.dump(status, stream, indent=2)
            os.replace(temporary, status_path)
    if parent_id:
        message = f'Subagent {session_id} ({name}) failed to start: {error}. No task prompt was delivered.'
        parent = manager.get_session(parent_id)
        if parent:
            parent.receive_child_message(session_id, message)
        else:
            from src.services.acp import queue_child_message
            queue_child_message(parent_id, session_id, message)

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

=== AGENT MESSAGING ===
Use read_parent_messages while working and before reporting completion to consume all queued parent instructions. When a parent message arrives, call it before acting to pick up additional queued instructions.
You were spawned by a parent agent. If you receive an [AGENT MESSAGE from ...] prompt:
- This is a message from your parent agent
- You MUST reply using the message_parent tool
- Do NOT just respond in chat - use the tool so your parent receives your reply
- After replying, continue with your task unless instructed otherwise

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
{' '.join(TMUX_CMD)} "$SESSION_NAME" {' '.join(_get_kiro_cmd())} "Read the instructions from {instructions_file} and execute the task described there."
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
    from src.services.subagent_schedules import validate_schedule
    validate_schedule(cron_schedule, 'UTC', 'scheduled task')
    if '\n' in cron_schedule or '\r' in cron_schedule:
        raise ValueError('cron_schedule must be a single line')
    task_id = os.path.basename(os.path.dirname(script_path))
    if os.path.abspath(script_path) != os.path.join(SUBAGENT_DIR, task_id, 'spawn.sh'):
        raise ValueError('Invalid subagent schedule script')
    with schedule_guard():
        record = read_task(task_id)
        if record and record.get('schedule_enabled') is False:
            raise ValueError('This schedule has been canceled')
        existing = _read_crontab()
        old = _task_cron_lines(existing, task_id)
        lines = [line for line in existing.splitlines() if line not in old]
        line = f'{cron_schedule} {script_path}'
        lines.append(line)
        _write_crontab('\n'.join(lines) + '\n')
        if line not in _read_crontab().splitlines():
            raise RuntimeError('Schedule installation could not be verified')


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
        status['session_id'] = task_data.get('session_id')
        status['schedule_mode'] = task_data.get('schedule_mode', 'spawn')
        status['schedule_enabled'] = task_data.get('schedule_enabled')
        status['session_ids'] = task_data.get('session_ids', [])
        if status['schedule_mode'] == 'resume' and status['session_id']:
            from src.services.subagent_schedules import read_schedule_snapshot
            status['resume_schedule'] = read_schedule_snapshot(status['session_id'])
            status['schedule_enabled'] = bool(status['resume_schedule'] and status['resume_schedule']['enabled'])

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


def terminate_subagent(task_id, manager=None):
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        raise ValueError('Invalid task or session ID')
    if manager is None:
        from src.services.acp import acp_manager
        manager = acp_manager
    with schedule_guard():
        record = read_task(task_id)
        if manager.get_session(task_id):
            cancel_origin_schedule(manager, task_id)
            manager.destroy_session(task_id, delete_history=False)
            return {'session_id': task_id, 'terminated': True, 'schedule_canceled': True}
        if not record:
            raise ValueError('Task or session not found')
        record['schedule_enabled'] = False
        save_task(task_id, record)
        if record.get('schedule') and record.get('schedule_mode', 'spawn') == 'spawn':
            remove_cron_job(task_id)
        sessions = task_session_ids(manager, task_id, record)
        for session_id in sessions:
            cancel_origin_schedule(manager, session_id)
            if manager.get_session(session_id):
                manager.destroy_session(session_id, delete_history=False)
        if sessions:
            return {'task_id': task_id, 'session_ids': sorted(sessions), 'terminated': True, 'schedule_canceled': True}
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
    return {"task_id": task_id, "terminated": killed, 'schedule_canceled': True}


def delete_subagent(task_id):
    status = get_subagent_status(task_id)
    if status.get('session_id'):
        terminate_subagent(task_id)
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
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        raise ValueError('Invalid task ID')
    with schedule_guard():
        record = read_task(task_id)
        if record and record.get('schedule_enabled') is not False:
            record['schedule_enabled'] = False
            save_task(task_id, record)
        existing = _read_crontab()
        matches = _task_cron_lines(existing, task_id)
        if matches:
            _write_crontab('\n'.join(line for line in existing.splitlines() if line not in matches) + '\n')
        if _task_cron_lines(_read_crontab(), task_id):
            raise RuntimeError('Schedule cancellation could not be verified')
        return {'task_id': task_id, 'removed': bool(matches), 'schedule_canceled': True}


def remove_at_job(job_id):
    subprocess.run(["atrm", job_id])
    return {"job_id": job_id, "removed": True}
