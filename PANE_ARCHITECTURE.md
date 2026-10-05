# Pane architecture

## Shared host and session adapters

`src/static/js/panes.js` owns tab-local pane records, the session registry, opening/dismissal, restoration, sidebar construction and gestures, activation, split transitions, and the validated parent bridge. `src/static/js/session-adapters.js` registers desktop, Jupyter, SilverBullet, terminal, and chat integrations. Both load before the application-specific integrations.

The records replace the separate `paneTypes`, `currentSession1`, `currentSession2`, `paneNotebook`, and `_jupyterPaths` globals. `activeTerminal` and `isSplit` are the existing tab-local layout state, declared in this module. Nothing persists a shared layout on the server or in localStorage.

## Integration boundary

- `register(type, adapter)` validates and registers an integration. Required functions are `matches`, `mount`, `context`, `sessions`, and `sidebar`.
- `open(key)` resolves the adapter, prepares service-specific parameters, transitions the pane, mounts content, and updates shared chrome and URL state. Applications do not call `showBrowser` or `finishOpen` themselves.
- `close(key)` invokes the service-specific close operation and applies the declared dismissal policy. View disposal is distinct from terminating a PTY, archiving a chat, or stopping a container.
- `ready(key)` completes asynchronous mounting and refreshes shared state. `unmount(context)` is an optional view-resource cleanup hook when content is replaced or disposed.
- `inventory(data)` obtains sessions from every adapter. `sidebarItem(session)` supplies click/touch handling, inline rename, buttons, context menus, long press, drag-to-group, and mobile sidebar dismissal. Adapters supply presentation and operations rather than wiring those handlers.
- `restore(availableTerminalSessions)` handles both restoration entry points, retaining their distinct availability rules. `prepare` decodes a serialized key before mounting.
- `sessionKey(pane)` supplies sidebar highlighting and borders; `locationKey(pane)` optionally uses the adapter's `serialize(key, state)` for URL persistence.
- `context(pane)` supplies agent-visible pane metadata.
- `installFocusPolicy()` binds the shared iOS completed-click policy to the host document and accessible pane iframe documents. Existing frames are scanned; subsequent loads/navigation bind their current documents automatically.
- `bindContextMenu(element, callback)` implements the existing right-click, 500 ms long-press, touch movement, and cancellation behavior. The callback supplies session-specific actions.

PTY attachment and output routing remain terminal-specific mounting operations. PTY viewer identity is distinct from displayed pane identity. Terminal attachment still moves the single rendered terminal and detaches its old viewer. Chats can still appear in both panes; re-opening the same chat in the same pane reuses its iframe. Notebook startup and Jupyter navigation retain their asynchronous lifecycle. Existing public opening functions are thin delegates to `open`.

## Adding an application

Register an adapter in `session-adapters.js` (or load an application-owned registration script before restoration). Its `sessions(data)` returns records with stable `key` and `name` values from the application's backend data. `mount({key, pane, browser, state, options})` mounts application content into the supplied container. Shared opening, sidebar refresh, URL persistence, group placement, focus policy, and mobile controls are automatic. Application backend operations and the source of its session data remain application-specific.

```js
paneController.register('example', {
    matches: key => key.startsWith('example:'),
    sessions: data => (data.example_sessions || []).map(name => ({key: 'example:' + name, name})),
    sidebar: () => ({}),
    context: key => ({type: 'example', description: 'Example ' + key.slice(8)}),
    mount({key, browser}) {
        const frame = document.createElement('iframe');
        frame.src = '/example/' + encodeURIComponent(key.slice(8));
        browser.replaceChildren(frame);
    },
    close: key => emitWithCsrf('example_close', {name: key.slice(8)}),
});
```

Authentication of the example application route is its application's responsibility; existing integrations continue to provide the API key required by their routes.

Optional sidebar descriptor fields: `icon` (trusted application SVG), `label` (returns `{element, title}`), `className`, `labelClass`, `dataset`, `rename(name)`, `optimisticRename`, `closeLabel`, `sleep`, `clone`, `collapse`, and `button: {icon, run}`. Session records can be `pinned` or `hidden`. Default close dismissal removes matching browser views and synchronizes the URL. Existing adapters explicitly override this where legacy behavior differs.

Optional adapter hooks/policies include `prepare`, `beforeMount`, `afterOpen`, `ready`, `firstReadyOnly`, `unmount`, `serialize`, `navigate`, `selectInactiveOnly`, `activateExisting`, `selectionKey`, `toggle`, and `dismiss`. `mobileClass` declares existing control styling; new embedded applications default to hiding terminal controls. `priority` resolves overlapping matchers; the legacy unprefixed terminal matcher has the lowest priority. `description` in context supplies agent-readable text for a new type without a chat-template branch.

## Application-to-host bridge contract

Load `/static/js/pane-bridge.js` from the Fernando host. The script URL fixes the parent origin; the destination is always `window.parent`. Both ends validate the sender window and exact origin. The host resolves pane identity from the live registered iframe, never an application-supplied pane number or session ID. Removed frames and unrelated windows cannot activate a pane or request its context.

For ordinary document interaction, the entire application-side wiring is:

```html
<script src="/static/js/pane-bridge.js" data-activate-on="click"></script>
```

Use the integration's event timing: chat uses `mousedown`; notebooks and Jupyter use `click`. Desktop uses `pointerdown` with `data-activate-capture="true"`: Kasm's canvas cancels touch events and does not deliver ordinary document clicks. Its old `enable_audio` message also triggered activation before the bridge migration. Capture-phase pointer activation replaces that dependency. New document-based applications should use completed `click`; applications with a custom interaction surface can omit the attribute and call `FernandoPane.activate()` at the equivalent user-interaction point. Passive/programmatic focus must not be reported as a user activation.

On iOS, adapters can supply `focusOnTap(pane)` to focus their native input synchronously on a completed stationary touch. The terminal uses this because waiting for a synthesized click can require a second tap when transferring focus from an iframe. The host excludes movement, cancellation, multiple touches, long presses, active text selection, and replaced views. This does not replace the completed-click `preventScroll` policy or change its timers/offsets. Native keyboard behavior requires physical phone verification.

- `FernandoPane.activate()` reports direct user interaction. The host owns active-pane state, highlights, URL changes, and focus policy.
- `FernandoPane.navigate({name, path})` reports application navigation to the registered adapter's `navigate(pane, location)` hook. Service-specific navigation interpretation belongs there.
- `FernandoPane.requestContext()` requests a fresh pane/group context.
- `FernandoPane.on('context', callback)` and `.on('state', callback)` subscribe to host updates; the returned function unsubscribes. State contains `active`, `split`, and `group`.
- The bridge automatically emits `ready` on load. The host responds with context and state. Listener lifetime is the application document's lifetime; callers unsubscribe temporary listeners.

The wire envelope is `{type: 'fernando-pane', version: 1, event, payload}`. Application code uses the SDK methods, not handwritten `postMessage` calls. Native iOS completed-click handling remains host-owned and independent of activation-event timing. Jupyter's existing service-specific command protocol remains application-owned; pane activation, navigation, context, and state use the shared bridge.

The public `setActiveTerminal` and `toggleSplit` functions delegate to the controller. Existing callers do not need to understand the content type. The former function wrapper has been replaced with an explicit delegate preserving its update order, including highlight/border updates after a rejected indirect activation.

## Compatibility boundaries

This release preserves the current DOM, styles, two-pane arrangement, URL format, restoration entry points, timing, and focus rules. The pane container markup and CSS are unchanged. Mobile viewport/keyboard positioning is unchanged. A subsequent user-reported terminal bug fix makes focus and scrolling resolve the terminal's current pane after a move, preserving the existing 300 ms delay and scroll offsets.

The existing URL decoder behavior, retained hidden iframe behavior, identifier compatibility rules, close-path metadata retention, and service-specific restart/error handling are intentionally preserved. They are not normalized into new behavior during this refactor. Changing those semantics requires a separately approved change.

The sidebar and socket-connected restoration paths retain their terminal availability rules within the shared restoration method. Existing service-specific restart/error scopes and legacy notebook/chat close metadata are preserved explicitly rather than silently corrected.

## Verification

Run `node --test tests/test_pane_contract.js` from the repository root. It registers an otherwise unknown type and verifies automatic sidebar construction, opening/closing, cleanup, URL restoration, context, mobile controls, and authenticated bridge activation. It also covers current adapters' chat reuse, duplicate views, terminal movement, asynchronous notebook readiness, archive actions, and SDK origin/source validation.

The earlier `test_pane_compatibility.js` is a first-refactor characterization harness against `be229fa121501eb6f1c6fbb9a10b5972435be219`. Its exact old wire-message, adapter-API, and handcrafted DOM expectations predate this contract and are not the current contract acceptance check. The existing native-focus, terminal-focus, composer, chat-context/error, and group-archive regression checks remain applicable.

This is a behavior characterization suite, not a physical iPhone rendering test. Hardware-specific rendering remains subject to Jonathan's validation.

Desktop four-pane layouts, resizing, and drag-to-split are a later release. They are not part of this refactor.

## Native focus-scroll follow-up

The initial mousedown-based opt-out failed on Jonathan's iPhone. Captured traces show the correct upper input focused with preventScroll, followed by approximately 155 px of native keyboard-centering movement before Fernando's existing correction. The input's document center was y=353 and the keyboard-reduced viewport was 397 px high, consistent with centering by 154.5 px. One trace showed native movement 95 ms after focus, before any JavaScript scroll call.

WebKit source shows the default mouse-focus path can refocus the same element with default options, replacing the pending focus options. Requesting native `focus({preventScroll: true})` on the completed click, after native focus and caret placement, fixed the chat issue on Jonathan's iPhone. Three subsequent traces confirmed zero native scroll when the keyboard opened, followed only by Fernando's intended 31 px positioning. Full evidence and WebKit source references are in the Fernando notebook's Pane Architecture page.

The policy now belongs entirely to the pane host, with no chat-specific hookup. It binds local and accessible nested iframe documents, recognizes text-entry inputs and contenteditable surfaces, and resolves the live owning pane/surface at click time. This covers terminal-style hidden inputs newly focused by a gesture as well as direct editor clicks. The scope remains the upper pane in an iOS split layout. It neither cancels taps nor changes caret placement, CSS, or scrolling. Detached documents, hidden views, unrelated clicks, and noneditable controls are excluded. Cross-origin/opaque documents are not accessible; the desktop integration concerns browser-side viewer inputs. Duplicate binding is prevented with a WeakSet, without retaining replaced documents. Temporary tracing and its backend handler have been removed.

Run `npm ci --prefix tests --ignore-scripts` once, then `npm --prefix tests run test:pane-focus`. The 12 jsdom tests cover input kinds, nested/loaded/navigated frames, moved inputs, stale/hidden documents, open shadow roots, source boundaries, native selection, and gesture scope. Shared DOM lifecycle behavior is tested automatically; chat is the physically confirmed iPhone case so far.

## Input follow-up

Jonathan reported that iOS classified this conversation's composer as a contact autofill field, and that a terminal moved to the top pane could still focus/scroll the bottom pane. The latter was traced to a captured creation-time pane number; the listener now reads the terminal entry's current pane. The composer now has its own labeled form, an explicit chat-message field identity and associated label, and explicit autocomplete/correction attributes. Safari's native classification still requires iPhone validation; the precise triggering signal in this conversation has not been established.

`node tests/test_terminal_pane_focus.js` exercises movement in both directions before/after terminal initialization, delayed scrolling after a move, and matching input/resize routing. `node tests/test_chat_composer.js` checks the form/label boundary, prevents native form navigation, and exercises the existing iOS/desktop Enter and per-session draft behavior.
