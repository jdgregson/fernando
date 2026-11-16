# Job-management verification

**Completion correction:** The original workflow checks did not establish complete session integration. The subsequent non-chat session corrections are now deployed and their evidence is recorded in `JOB_SESSION_INTEGRATION.md`. That audit explicitly records the native-touch verification limitation rather than counting a failed input control as success.

## Automated checks

The focused job suite covers decomposition validation and independent review, role separation, immutable paginated evidence, full-read receipts, invocation-context expansion, cross-job access denial, acceptance versus outcome, rejection/rework, dependency scheduling, blocked independent work, operator escalation, explicit waivers, cancellation, restart recovery and turn-boundary handoff.

The API suite checks authenticated reads, header-authenticated mutations, exact original document retention, TXT/Markdown/DOCX ingestion, malformed input, style reuse, and actual Unix-socket peer identity rather than caller-supplied roles.

* 27 focused job/API tests passed, including the final turn-boundary regression.
* 13 existing service-boundary and ACP restart tests passed.
* 12 job adapter, pane-contract and new-session destination tests passed.
* 11 isolated Chrome checks passed using the real template, JavaScript and existing styles: 1440/390/320 layouts, creation, escaped scope and evidence text, pause/resume, operator draft preservation, answers and waivers. Screenshots were inspected. No new stylesheet or CSS rule was added.

The older `test_pane_compatibility.js` harness fails before exercising job code because it does not supply `bindBackdropDismissal`. It was not changed as part of this feature. The current pane-contract and destination suites pass.

## Live reviewer calibration

Reviewer: Kiro / Sonnet 4.6, with separate live ACP contexts and the production job MCP identity/evidence interfaces.

| Controlled transcript | Expected | Observed |
| --- | --- | --- |
| Cloudflare login redirect claimed as nginx upstream TLS rejection | Rework | Rework |
| Print-only isolation assertion without an access attempt | Rework | Rework |
| Command skipped by shell short-circuiting | Rework | Rework |
| Algorithm rejection claimed as issuer/audience validation | Rework | Rework |
| Executed arithmetic check with the specified positive/negative controls | Accept | Accept |

Observed false acceptance: **0/4**. Observed unjustified rejection: **0/1**. These are small, controlled calibration fixtures, not population error-rate estimates or guarantees about future decisions.

Full verdicts and reasons: `/tmp/opencode/job-live-0cfd3675/reviewer-calibration.json`.

## Live end-to-end execution

The production controller completed job `cf5feea191c343488eee65660c3c63f1` through independent planning, scope review, three bounded worker assignments and three result reviews. Every requirement was accepted; no waivers were used. Each assignment used a distinct ACP session.

The worker artifact's actual bytes were independently checked as `durable-jobs-ok` followed by one newline. SHA-256:

```text
c6848c29b2913cd34fa309e5f83c94cd34a6ef24abdb700bb937f7af828ac7b5
```

Authoritative log: `/tmp/opencode/job-live-0cfd3675.log`, ending in `ALL_LIVE_CHECKS_PASSED` and `EXIT=0`. Full ledger snapshots, archived raw evidence references and review decisions are retained in the same-named directory. The harness removed only its fixture jobs and archived their agent sessions.

## Final activation checks

### Pane integration correction

The initial Job page omitted the existing `pane-bridge.js` loader. Adapter registration alone did not activate the owning pane on document interaction or receive shared group-selection state. The original adapter tests simulated messages and the standalone layout fixture did not cover that missing application-side integration.

The Job template now loads the shared bridge with the existing document `click` activation policy. A regression loads the actual Job template and bridge inside the registered iframe, clicks its name input, scope textarea, document button and page body while the opposite pane is active, and verifies the owning pane becomes active. It also verifies shared group-selection styling and retains origin/source rejection checks. The focused job and pane-contract run passes all nine tests. No separate focus implementation or CSS was added.

The final controller and UI changes are active.

* An unfinished paused review fixture survived an actual runtime restart with its entire ledger unchanged and archived evidence hashes valid. Runtime PID changed from `2078603` to `2097016`. Proof: `restart-result.json` in the live-verification directory.
* A subsequent web-only restart preserved runtime PID `2097016` and controller socket inode `833766` while a live job continued. Proof: `web-restart-result.json`.
* A final compact read-only job, `c0f5ce06913148a99b7af4d99bfc4631`, completed all four roles. Each next assignment was created after the preceding session's actual recorded ACP turn-end event, including worker-to-reviewer handoff. Proof: `handoff-result.json`; `/tmp/opencode/job-handoff-0cfd3675.log` ends in `HANDOFF_TURN_BOUNDARIES_PASSED` and `EXIT=0`.
* Final served job JavaScript matched working-tree SHA-256 hashes. The full-width Job session card, authenticated job panel, and unauthorized API rejection were checked against the running application.
* Cleanup checked all 19 fixture session identities: no fixture jobs or processes remain. Archived session history and saved verification snapshots remain available. Proof: `cleanup-result.json`.

Current source hashes are recorded in `/tmp/opencode/job-live-0cfd3675/source-sha256.json`.

No Git commit or push was performed. Broad security scanning remains deferred until product approval.

## macOS peer-authentication correction

The original job socket handler assumed Linux `SO_PEERCRED` and `/proc` process metadata. This caused first-use failure on macOS. `unix_peer.py` now selects the platform's native socket credentials: Linux `SO_PEERCRED`, or macOS libSystem `getpeereid` plus `SOL_LOCAL` / `LOCAL_PEERPID`. Both use psutil for process ancestry. Credential and process-inspection failures deny the request inside the handler's error boundary.

The Darwin constants and libc signature were checked against Apple's XNU `bsd/sys/un.h` and Libc `gen/FreeBSD/getpeereid.c`. Forty-nine focused tests passed, including native Linux Unix-socket credentials and process ancestry, sibling/dead-session rejection, Darwin API-contract tests with `SO_PEERCRED` absent, credential failure, invalid peer PID, role enforcement and structured credential-error responses. Native macOS execution is not represented as tested.
