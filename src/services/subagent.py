from src.services.subagent_core import (
    create_workspace, resolve_context_path, write_task_json, write_status_json,
    write_instructions, write_spawn_script, schedule_at, schedule_cron,
    run_immediately, get_subagent_status, list_subagents, terminate_subagent,
    delete_subagent, get_cron_jobs, get_at_jobs, remove_cron_job, remove_at_job,
)


CRON_PATTERNS = {
    "minute": "* * * * *",
    "5 minutes": "*/5 * * * *",
    "10 minutes": "*/10 * * * *",
    "15 minutes": "*/15 * * * *",
    "30 minutes": "*/30 * * * *",
    "hour": "0 * * * *",
    "day": "0 0 * * *",
    "week": "0 0 * * 0",
}


class SubagentService:
    def list_subagents(self):
        tasks = list_subagents()
        # Flatten for the web UI format
        result = []
        for t in tasks:
            status = t["status"]
            result.append({
                "task_id": t["task_id"],
                "task": status.get("task", ""),
                "created_at": status.get("created_at", ""),
                "schedule": status.get("schedule"),
                "status": status.get("status", "unknown"),
                "progress": status.get("progress", 0),
                "current_step": status.get("current_step", ""),
            })
        return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)

    def create_subagent(self, task_id, task, context_path=None, schedule=None):
        task_id, workspace = create_workspace(task_id)
        context_file = resolve_context_path(context_path)
        session_name = f"subagent-{task_id}"

        write_task_json(workspace, task_id, task, context_file, schedule=schedule)
        write_status_json(workspace, scheduled=bool(schedule))
        instructions_file = write_instructions(workspace, task_id, task, context_file)
        script_path = write_spawn_script(workspace, session_name, instructions_file)

        if schedule:
            if schedule.startswith("at "):
                schedule_at(script_path, schedule[3:])
                return {"task_id": task_id, "session_name": session_name, "workspace": workspace, "scheduled_at": schedule[3:]}
            elif schedule.startswith("every "):
                cron_time = CRON_PATTERNS.get(schedule[6:], "*/5 * * * *")
                schedule_cron(script_path, cron_time)
                return {"task_id": task_id, "session_name": session_name, "workspace": workspace, "recurring": schedule[6:], "cron": cron_time}

        run_immediately(session_name, instructions_file)
        return {"task_id": task_id, "session_name": session_name, "workspace": workspace}

    def get_subagent_status(self, task_id):
        return get_subagent_status(task_id)

    def terminate_subagent(self, task_id):
        return terminate_subagent(task_id)

    def delete_subagent(self, task_id):
        return delete_subagent(task_id)

    def get_at_jobs(self):
        return get_at_jobs()

    def get_cron_jobs(self):
        return get_cron_jobs()

    def remove_at_job(self, job_id):
        return remove_at_job(job_id)

    def remove_cron_job(self, task_id):
        return remove_cron_job(task_id)


subagent_service = SubagentService()
