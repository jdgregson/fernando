# Independent web and runtime lifecycles

## Services

| Unit | Owns | Restart behavior |
| --- | --- | --- |
| `fernando.service` | nginx, desktop infrastructure, credentials, startup preparation | Full restart; propagates to both child services |
| `fernando-runtime.service` | ACP agents and their MCP processes, live Socket.IO connections, terminal processes, Jupyter, pending approvals, schedules, session-bound HTTP requests | Survives web restarts |
| `fernando-web.service` | Templates, static assets, stateless HTTP handlers | Independently restartable |

The runtime keeps the existing `FLASK_PORT` (default 5000). The web process uses `WEB_PORT` (default 5001), bound to loopback. nginx and MCP clients retain their existing addresses. The runtime forwards stateless HTTP requests to the web process, while Engine.IO/Socket.IO connections remain attached to the runtime. No in-memory ACP state is transferred or reconstructed during a web restart.

`src/services/service_boundary.py` declares the stateful endpoint ownership explicitly. The same authentication and CSRF handlers continue to protect runtime-owned operations. Forwarding preserves the caller's credentials, does not follow redirects, and does not retry requests. The web process does not create an ACP manager, register Socket.IO handlers, restore terminals, or start scheduling/background migration workers.

Both processes append to `/tmp/fernando-flask.log` with the existing log format. The health log view and error index therefore cover both processes.

## Mutate modes

* `mutate(mode="flask")`: default. Notifies connected UIs to show the mutation animation, restarts `fernando-web`, waits for its authenticated readiness endpoint, then automatically reloads connected UIs to apply frontend changes. Returns normally to the calling agent. Does not rotate credentials or restart the runtime, desktop, Jupyter, terminals, or agent MCP processes. No manual browser reload is required.
* `mutate(mode="all", force=true)`: explicitly disruptive full restart. Without `force=true`, this mode is denied even if every agent is idle. Supply a continuation for the calling agent, which will be terminated during the restart.

Before runtime shutdown, actively working sessions are checkpointed to a private atomic restart file. Once restored, each receives `Service restarted, continue what you were doing.` An explicit continuation replaces the generic one for its target session. Idle and sleeping sessions do not receive automatic work prompts. Pending recovery survives a session-load failure and is acknowledged after continuation submission. The runtime unit uses `KillMode=mixed` so its main process can checkpoint before systemd kills remaining child processes.

Flask-only mode refuses to run before the split services have been installed. It never substitutes a full restart.

Changes to `src/routes/websocket.py`, ACP internals, session-bound HTTP handlers, runtime startup, or MCP servers require the full mode. Stateless HTTP routes, templates, static assets, and web-only dependencies can use Flask-only mode. Shared modules used by the runtime require full mode to update the runtime's loaded copy.

During a web restart, existing chat/terminal streams and session-bound tool requests continue. New page/stateless HTTP requests may briefly receive 503; in-flight web-owned requests, including uploads, are not guaranteed to survive. This mode is not a rolling web deployment.

## Scheduled chat resumes

The Chat MCP server exposes `schedule_resume(delay_seconds, reason, continuation)` and `cancel_resume(schedule_id)` for the calling conversation. One pending resume is allowed per chat. A confirmed schedule is durable in `data/chat_resumes.json`; the agent can end its turn immediately. The runtime wakes the original conversation when due and waits for active turns and queued messages to finish before delivering the continuation. A user message does not cancel the schedule. Archiving or terminating the chat cancels it.

The conversation shows one collapsed system-style row per schedule. Clicking it opens the existing continuation detail modal with the reason, deadline, instructions, state, and Resume now / Cancel actions. Status events are append-only in history and update the same row during replay and live delivery. A sidebar clock identifies pending resumes, including sleeping chats.

Overdue resumes run after runtime recovery. Waking has a 120-second readiness deadline; busy chats have no such timeout. Delivery errors are recorded as failed, not silently retried. A persisted dispatch receipt prevents duplicate delivery; an interrupted delivery with no receipt is reported as uncertain and requires inspection before rescheduling. Resumed means the continuation was submitted to the agent transport, not that the follow-up work completed.

## Recurring subagent lifecycle

`spawn_subagent` defaults recurring `cron_schedule` tasks to `schedule_mode="resume"`, reusing one child conversation. Fresh workers on every tick require explicit `schedule_mode="spawn"`. Immediate and one-time invocations retain their existing behavior.

Both recurring modes use `manage_subagent_schedule(action="get"|"update"|"cancel")`. Omit IDs for the current worker's originating schedule, or specify a child `session_id` or owned `task_id`. Cancel stops future runs without terminating current workers and returns `enabled=false` only after cancellation is confirmed. Unknown schedules and failed cron edits return errors. Resume schedules support timezone and wake-message updates; fresh-worker schedules support cron-expression updates.

Task metadata records ownership and worker session IDs. Existing legacy workers are also resolved through their original task names. Terminating by task ID cancels future execution and terminates its workers; terminating a worker session cancels its originating schedule first. Archive/delete lifecycle operations use the same cancellation path. Cron edits and scheduled launches share a process/thread-safe lock. Cancellation persists a disabled task before removing and verifying the cron entry, so already-started launcher requests are rejected before creating a new worker.

## One-time activation

The migration requires root to replace the old systemd unit and install the narrowly scoped `sudo systemctl restart fernando-web` permission. Normal mutate permissions cannot install units.

Wait for active work to finish before migration. The activation explicitly stops the existing monolithic service and its agents. From a root shell:

```sh
python3 /home/fernando/fernando/scripts/install_service_split.py --output /tmp/fernando-service-units --activate --force
```

The installer validates staged unit definitions and sudoers before stopping anything, backs up existing unit files under `/var/backups/fernando-services/`, installs the three units, reloads systemd, and starts the new service group. It leaves desktop volumes and application data in place.

To render and validate units without modifying system services:

```sh
python3 scripts/install_service_split.py --output /tmp/fernando-service-units
```

Fresh setup uses `--install`, which refuses to replace a running service and does not start it. `systemctl start fernando` then starts the whole group.

## Verification

```sh
venv/bin/python -m unittest tests.test_service_boundary tests.test_acp_restart_lifecycle
```

The isolated boundary test runs the real web application on temporary ports alongside a fixture runtime. It replaces the web process while checking unchanged runtime/child PIDs, a continuous Socket.IO event sequence, and successful completion of an in-flight HTTP tool request. It does not create production agents or restart host services.

After activation, verify real agent/MCP PIDs, terminal sessions, Jupyter kernels, pending approvals and desktop connectivity across a Flask-only mutate. Verify full-mode denial without force. This production acceptance step must happen after the one-time migration, not against the old monolithic service.
