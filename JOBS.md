# Durable jobs

Choose **New Session > Job** to open a retained Job or create a new draft. Supply its name and agreed scope, then select worker and reviewer providers/models independently. Drafts are saved with revision checks before execution starts. TXT, Markdown and DOCX uploads support documents up to 4 MiB with extracted scope up to 200,000 characters. Review the extracted text before starting the job. The original document bytes, filename and SHA-256 remain available alongside the confirmed scope.

Job is a non-chat session. Its sidebar supports naming, grouping, pane focus and Close. Closing pauses the Job and closes all of its associated agent sessions while preserving the ledger, evidence and conversation histories. Reopen it through the Jobs picker, then use Resume to continue its interrupted assignment. Closing never accepts or waives unfinished work.

Associated agents appear directly beneath their Job in compact, single-line rows. The Job's chevron collapses or expands all of them. Archived subagents are hidden by default; use **Include subagents** to display them. Matching archived search results reveal hidden subagents without changing that checkbox, and clearing the search hides them again.

Creation settings include **Max workers**, defaulting to **5**, and **Reuse workers**, off by default. Max workers limits concurrent task pipelines, including review; dependency prerequisites must be accepted or waived before dependent tasks start. A blocked task releases its slot for independent work. When reuse is enabled, the same worker conversation can handle up to three assignment attempts for the same test. Reviewer sessions are always fresh and separate. The existing three-failure escalation remains in effect; further attempts after operator guidance use a new worker once the prior worker has reached its three-use limit.

The job pane uses the existing session, subagent card, form, detail and button styles. No job-specific CSS is required. It shows original scope, decomposition, requirements, attempts, recorded evidence, review decisions, agent sessions and operator requests. Pause dispatch allows current agents to finish their submissions but prevents further assignments. An operator answer enables another attempt. Waivers require an explicit reason and cannot be applied while the requirement has an active assignment.

## Workflow

1. A planner proposes requirements with stable local keys, exact source quotations, work, acceptance criteria, preconditions, access, evidence, controls and dependencies.
2. A different session reviews coverage against the original scope. Execution begins only after acceptance. Unknown dependencies and cycles are rejected deterministically.
3. The controller dispatches bounded independent requirements up to the configured limit. Each worker is fresh unless the same-test reuse option applies. A submission records conclusion, outcome, limitations, versions and tool event references.
4. A separate reviewer session retrieves original tool events directly. References are expanded to include every recorded input/output update belonging to the referenced invocation. The reviewer must retrieve all pages of every submitted event before submitting a decision.
5. Acceptance advances the ledger. Rejection records specific reasons and dispatches a fresh or eligible reused worker. Blocked work leaves independent requirements eligible.
6. Completion requires an accepted decomposition and every requirement accepted or explicitly waived by the operator. Outcome is separate from execution acceptance: a correctly performed test can be accepted while reporting a defect.

An agent's final message has no completion authority. The controller waits for a submitting agent's turn to end before dispatching the next assignment. An idle agent with an unfinished assignment receives at most two continuations before escalation. Three rejected execution attempts or three rejected decompositions since the last operator answer trigger escalation. Missing sessions, startup failures and controller failures become operator requests rather than successful completion.

## Runtime and evidence

`src/services/jobs.py` owns the SQLite ledger at `data/jobs.sqlite3`, the runtime controller and its private Unix socket at `data/jobs.sock`. SQLite transactions serialize transitions. A process lock protects socket ownership. The controller starts alongside the existing runtime schedulers, not in the web process. Web-only restarts preserve controller and agent processes; full runtime restarts reopen the ledger and use ACP's existing session recovery.

`src/routes/jobs.py` exposes authenticated job listing, creation, details, documents, evidence and operator actions. Mutations require the API key in a custom header. Runtime endpoint ownership is declared in `src/services/service_boundary.py`.

New recorded ACP events have stable `event_id` values. Selected tool events are copied into the ledger with their session identity and SHA-256; later conversation changes do not replace archived evidence. Agent retrieval is paginated at 16,000 characters. Evidence access is scoped to the assigned job.

The existing Chat MCP server exposes:

* `job_status`
* `job_plan`
* `job_evidence`
* `job_submit`
* `job_review`
* `job_operator`

The runtime derives the caller from kernel Unix-socket credentials and its ancestry to a live ACP process. Linux uses `SO_PEERCRED`; macOS uses libSystem `getpeereid` for the user/group and `SOL_LOCAL` / `LOCAL_PEERPID` for the process. Process ancestry uses psutil's native platform implementation, without requiring `/proc`. Credential or process-inspection failures deny access. Tool arguments cannot assert a session or reviewer role. Planner, worker, decomposition reviewer and execution reviewer permissions are checked against the current assignment. There is no agent completion or waiver tool.

This is workflow-level role separation inside Fernando's existing trusted operating-system account. It does not sandbox arbitrary shell access from that account or make the database inaccessible to an agent with unrestricted host-file access. Review enforces evidence retrieval and assignment authority, not perfect model judgment. Instructions treat original scope, candidate summaries and evidence as task data rather than authority to change review roles or workflow.

## Verification

```sh
venv/bin/python -m pytest tests/test_jobs.py tests/test_job_api.py -q
```

`tests/check_jobs_ui.py` renders the real template, JavaScript and existing styles into an isolated HTTP fixture. It checks desktop and phone layouts, creation, safe text rendering, evidence display, pause/resume, operator answers, draft preservation and explicit waivers.

`tests/run_jobs_live.py --output /tmp/opencode/job-live-verification` explicitly exercises the running runtime with Sonnet 4.6. It measures reviewer decisions against four misleading transcripts and one genuine success, then runs a complete planner/reviewer/worker/reviewer job against a private temporary text file. Fixture jobs are removed and their sessions archived after evidence is saved. This is a calibration exercise, not a guarantee about future reviewer decisions.

Changes to the controller, ACP event recording, runtime routes or Chat MCP tools require a full mutation. Template and static-only changes can use a web mutation.

Executed checks and calibration results are recorded separately in [JOB_VERIFICATION.md](JOB_VERIFICATION.md).
