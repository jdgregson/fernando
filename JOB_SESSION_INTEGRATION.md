# Job session integration audit

Status: **common non-chat session corrections deployed and verified**. Native phone touch remains unverified; the browser touch-emulation positive control failed independently of Job, as recorded below.

The baseline is a non-chat session, comparable to Jupyter, notebooks and Files. The earlier inventory incorrectly included ACP-only archival, chat sleep/wake, transcript search/export and forking. Those are not Job session requirements. Their staged implementations have been removed, not deferred for later implementation.

Job's requested workflow still uses independent worker/reviewer ACP conversations internally. That does not make the Job view an ACP chat or authorize changing the existing ACP lifecycle handlers.

## Shared session requirements

| Integration | Implementation | Verification status |
| --- | --- | --- |
| Registration and existing styles | Shared adapter, inventory and existing CSS/control classes | Actual embedded-browser checks and live inventory checks passed; no new CSS rules |
| Focus and bridge | Actual Job document loads `pane-bridge.js` with completed-click activation | Actual iframe field/button/body clicks and group-selection state pass; negative control fails with loader omitted |
| Open/close/reopen | Job picker uses the existing notebook-picker presentation. View registration is separate from retained Job data and execution | Actual browser and live API checks passed |
| Stable creation/restoration | Persisted draft has a real Job session ID, group and optimistic revision; form saves before replacement/close, with local pending-edit retention | Backend conflict checks, save-before-leave failure checks and actual two-pane browser reload passed |
| Selection/reuse and both panes | Re-select preserves the existing iframe; duplicate views retain distinct documents; shared split and active-pane behavior | Browser document-identity, two-pane input and saved-active-pane restoration checks passed |
| Sidebar names and context menu | Existing inline rename and Close controls; title/group filtering stays on the shared non-chat path | Browser Rename/Set page title/Close checks passed; no ACP actions present |
| Group moves and Ungrouped | Project membership and Job dispatch group are synchronized. Moving does not apply ACP restart/sleep behavior to Job | Browser drag/drop wiring and real WebSocket move checks passed |
| Group deletion | Job is retained and effective membership becomes Ungrouped | Live group deletion preserved draft data and cleared dispatch membership |
| Group visibility/restore | Existing group metadata behavior remains shared. Job dispatch waits while its required group context is archived, without converting the Job into an archived chat or reporting completion | Backend dispatch gating and live group archive/restore checks passed |
| Context and shared chrome | Persisted name/state, open Job group membership, pane descriptions, group colors and shared selection styles | Pane-context, custom title and both embedded group-color checks passed; backend context includes open Job metadata |
| Cross-view updates | Runtime broadcasts metadata revisions and view registration; parent passes updates through the checked shared bridge | Both browser views synchronized; separate live Socket.IO observer received authoritative updates |
| Native-input/mobile policy | Existing host focus policy and mobile controls, rather than Job-specific focus handlers | Shared native-focus regressions and mobile layout passed. Touch synthesis generated no click on either Job or an ordinary host-button positive control, so native touch is explicitly unverified |
| View cleanup and retained state | Switching disposes the document while retaining Job data. Closing pauses the Job and closes its owned agents without accepting, waiving or deleting obligations | Native-process regression verifies owned processes exit while an unrelated process remains running; explicit Resume restores the interrupted assignment |
| Security and runtime ownership | Existing API-key/CSRF/source checks; runtime-owned ledger and dispatcher | Changed API tests and live authenticated WebSocket/HTTP checks passed; served assets matched source |

## Explicit behavior boundaries

* Close pauses the Job and closes its associated agent sessions, retaining evidence and conversation histories. Reopening leaves it paused until Resume. This coupled behavior supersedes the original view-only Close policy at the user's request.
* Job's existing Pause/Resume, operator requests and requirement waivers are workflow operations, not ACP Sleep/Wake or chat archive operations.
* A group's normal archive/restore metadata behavior remains available. There is no individual ACP-style Job archive, chat fork, transcript export or conversation-content-search integration.
* Worker/reviewer conversations retain their existing ACP features through their own Chat sessions. Job provides links to them without becoming a Chat session itself.

Completion requires the shared integration rows to be verified against the real Job template, adapter, bridge and backend, rather than inferred from generic fixture adapters or workflow test totals.

## Evidence

* `tests/test_job_sessions.py`, `tests/test_job_api.py`, `tests/test_jobs.py`, `tests/test_group_archive.py`: 44 passing Python checks.
* Final focused Job and pane run: 14 passing checks, including rename preservation and saved active-pane restoration. Existing non-chat and native-input suites also passed during this work.
* `tests/check_jobs_ui.py --integration`: 11 passing actual embedded-browser checks. Report `/tmp/integration-results.json` records the separate unavailable touch control. Screenshots `/tmp/job-session-menu.png` and `/tmp/job-mobile-layout.png` were inspected.
* `tests/run_job_sessions_live.py`: eight live API/WebSocket checks passed after activation; exact fixture Jobs and groups removed. Report `/tmp/opencode/job-common-live-0cfd3675.json`.
