# Pane architecture

## First release: preserve the existing one/two-pane experience

`src/static/js/panes.js` owns the tab-local pane records, embedded-session registry, content transitions, activation, split transitions, and sidebar context-menu gesture handling. It is loaded before the terminal and session integrations.

The records replace the separate `paneTypes`, `currentSession1`, `currentSession2`, `paneNotebook`, and `_jupyterPaths` globals. `activeTerminal` and `isSplit` are the existing tab-local layout state, declared in this module. Nothing persists a shared layout on the server or in localStorage.

## Integration boundary

- `register(type, adapter)` adds embedded-session URL/key recognition, URL opening, mobile classification, and agent-context metadata.
- `showBrowser(pane, key)` handles the common browser-content transition and returns the existing browser container for integration-specific mounting.
- `showTerminal(pane, options)` handles browser-content dismissal. Its options explicitly preserve the existing close paths' different metadata, DOM disposal, and fit behavior.
- `finishOpen(type)` updates shared pane chrome and URL state. Chat retains its existing controls-before-URL ordering; other embedded views retain URL-before-controls ordering.
- `sessionKey(pane)` supplies sidebar highlighting, borders, and URL serialization.
- `context(pane)` supplies agent-visible pane metadata.
- `installFocusPolicy()` binds the shared iOS completed-click policy to the host document and accessible pane iframe documents. Existing frames are scanned; subsequent loads/navigation bind their current documents automatically.
- `bindContextMenu(element, callback)` implements the existing right-click, 500 ms long-press, touch movement, and cancellation behavior. The callback supplies session-specific actions.

Terminal attachment and output routing remain in the terminal/session integration. PTY viewer identity is distinct from displayed pane identity. Terminal attachment still moves the single rendered terminal and detaches its old viewer. Chats can still appear in both panes; re-opening the same chat in the same pane reuses its iframe. Notebook startup and Jupyter navigation retain their existing asynchronous lifecycle.

The public `setActiveTerminal` and `toggleSplit` functions delegate to the controller. Existing callers do not need to understand the content type. The former function wrapper has been replaced with an explicit delegate preserving its update order, including highlight/border updates after a rejected indirect activation.

## Compatibility boundaries

This release preserves the current DOM, styles, two-pane arrangement, URL format, restoration entry points, timing, and focus rules. The pane container markup and CSS are unchanged. Mobile viewport/keyboard positioning is unchanged. A subsequent user-reported terminal bug fix makes focus and scrolling resolve the terminal's current pane after a move, preserving the existing 300 ms delay and scroll offsets.

The existing URL decoder behavior, retained hidden iframe behavior, identifier compatibility rules, close-path metadata retention, and service-specific restart/error handling are intentionally preserved. They are not normalized into new behavior during this refactor. Changing those semantics requires a separately approved change.

The sidebar and socket-connected restoration paths still have their existing terminal availability rules. Both now use the same embedded-session registry instead of separate per-type dispatch chains.

## Verification

Run `node tests/test_pane_compatibility.js` from the repository root. The suite runs the pre-refactor JavaScript at revision `be229fa121501eb6f1c6fbb9a10b5972435be219` and the current code in separate Node VM environments with a deterministic DOM/event/timer harness. That revision must be available locally.

It compares pane state, DOM structure, sidebar/menu content, URL updates, storage, terminal viewer routing, emitted events, iframe messages, timer scheduling, and scrolling across content switches, both restoration entry points, focus guards, split transitions, notebook startup/errors, Jupyter navigation, and sidebar gestures. It also verifies independent tabs and a newly registered session type, and checks the unchanged layout CSS, container markup, keyboard-positioning code, and terminal focus-scroll code.

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
