# Fernando session integration contract

## Scope and definition of complete

A new session type must be fully integrated with Fernando's **shared session platform**. Implementing its internal workflow or standalone page is not enough.

A session is not synonymous with an ACP chat. Compare non-chat types with Jupyter, notebooks and Files. Do not copy chat-only features into a non-chat type to fill every adapter field. In particular, ACP conversation archival, agent sleep/wake, transcript search/export, forking, message pins and model controls belong to chats unless the requested application itself requires a corresponding feature.

Optional adapter hooks are implementation extension points. They do not waive shared platform behavior, and they do not require inventing unrelated product features. The underlying application's lifecycle must remain distinct from opening or closing a pane.

## Mandatory shared integrations

| Area | Required behavior | Existing owners |
| --- | --- | --- |
| Creation and identity | New Session entry, stable session identity, selected destination group including explicit Ungrouped, errors without phantom sessions | `index.html`, `sessions.js`, application routes |
| Registration and inventory | Shared registry, sidebar ordering and selection, safe names, existing styles, accurate inventory | `panes.js`, `session-adapters.js`, `file-session.js` |
| Embedded bridge | Actual document loads `pane-bridge.js`; readiness, direct-interaction activation, context/state reception and source/origin validation | `pane-bridge.js`, application template |
| Focus/input | Mouse/touch activation from actual fields, controls and body; correct owning pane; shared native-input focus policy; stale frames excluded | Shared bridge and `installFocusPolicy` |
| Shared chrome | Active borders, highlights, titles, group colors and group-selection styling remain consistent | `panes.js`, `sessions.js`, `group-selection.css` |
| Layout | Both panes, split/unsplit, pane activation, duplicate/reused views according to comparable types, mobile sidebar and keyboard-control policy | `panes.js`, `mobile.js`, comparable adapters |
| View lifecycle | Open, reselect, replace, close and reopen without unintended application operations or loss of retained data; cleanup of view-owned resources | Shared pane lifecycle and application session registry |
| Restoration | URL/refresh/reconnect restoration, active pane and split state, relevant application location and unsaved editor state | `restore`, `locationKey`, `keyFromUrl`, `syncUrlParams`, application persistence |
| Sidebar interactions | Existing selection, right-click/long press, drag/drop, cancellation and custom page-title behavior; meaningful application-provided name/action handlers | `sidebarItem`, `bindContextMenu`, session context menu |
| Groups | Creation destination, moves, Ungrouped, membership persistence, group deletion, group-level visibility/restore and context consistency | `groups.py`, WebSocket group handlers, shared sidebar |
| Context | Accurate pane type/name/state, group membership and application context; consistent browser and backend descriptions | `fullContext`, adapter `context`/`describeSession`, backend context construction |
| Updates | Creation, names, membership and application state update connected views and shared chrome coherently | Session-list/status events and application subscriptions |
| Security | Existing authentication/CSRF, sensitive-read protection, message-source/origin validation and safe rendering | Security instructions, routes and shared bridge |
| Devices/accessibility | Existing keyboard activation, focus, labels, touch controls and responsive conventions | Existing controls and shared device tests |

Sidebar title/group filtering is shared. ACP conversation-content search is not a mandatory non-chat integration. Likewise, a group's existing metadata archive/restore behavior does not turn each member application into an ACP archival object.

## Verification

Record implementation paths, actual user actions, expected behavior, observations and evidence for the shared requirements. Inspect comparable session implementations and current tests rather than deriving requirements from every optional callback on the chat adapter.

Exercise the actual registered adapter, embedded template and bridge together. Test actual clicks inside the application while the other pane is active, both panes, restoration, groups, metadata updates and normal view lifecycle. A test that constructs an `activate` message does not prove the application emits one. A standalone screenshot does not prove pane integration. Workflow tests do not prove session integration.

Do not report completion while a required shared integration is missing or unverified. Keep any additional application-specific features tied to the user's request, not to a union of unrelated session types' capabilities.

## References

* `PANE_ARCHITECTURE.md`: shared host, adapter and bridge APIs.
* `SERVICE_LIFECYCLE.md`: runtime/web ownership and restart rules.
* `.kiro/steering/security.md`: authentication and CSRF.
* `tests/test_pane_contract.js`, `tests/test_pane_native_focus.js`, `tests/test_new_session_target.js`: existing shared behavior checks.
