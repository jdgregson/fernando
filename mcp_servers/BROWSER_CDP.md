# Browser DevTools access

The desktop MCP server provides a persistent browser-level CDP connection through
the existing desktop container. It does not expose a new network listener or change
Chrome's profile, authentication, or remote-debugging configuration.

## Workflow

1. Call `browser_capture` with `action: start` before navigating or triggering the
   action of interest. Its status includes stable `target_id` values and a cursor.
2. Use the desktop/browser tools normally. Capture continues between tool calls.
3. Call `browser_network` with a URL substring, optional method/status/target,
   and `after` set to the saved cursor. Follow pagination until `has_more` is false.
4. Pass a returned request `id` as `request_id` to `browser_request`. `details`
   returns protocol events, headers, initiator, timing, cookies, redirects and
   failures. `request_body` and `response_body` provide paginated body retrieval.
5. Call `browser_wait` for a JavaScript condition or a completed request matching
   a URL. Set `after` before the action to exclude old responses. A network failure
   is explicitly returned in the matching request, not represented as success.
6. Stop capture when finished to disconnect and clear retained event data.

## Full protocol access

`browser_cdp` accepts a protocol `method`, `params`, and optional `target_id`.
Without a target it addresses the browser session. This exposes DevTools domains
without needing a separate wrapper for each operation. Protocol failures remain
errors. Domain events are retrieved through `browser_events`, filtered by method
prefix, target and cursor. Enable additional domains before expecting their events.

Examples:

- `Runtime.enable`, then `browser_events` with `method: Runtime.consoleAPICalled`.
- `Page.captureScreenshot` for a target-specific screenshot.
- `DOM.getDocument` and `DOM.querySelector` for structured DOM inspection.
- `Network.setCacheDisabled` when explicitly investigating cache behavior.

The collector attaches to pages, out-of-process frames and workers, including new
related targets. New targets are briefly held at startup while network collection
is enabled, then immediately resumed. Existing targets are not reloaded. Capture
does not reconstruct traffic from before attachment, or inspect non-CDP browser
internals and unrelated applications. Attachment failures appear in capture status.

## Replay

`browser_replay` performs a request again; POSTs and other mutations retain their
side effects. It must be used within the authorized task.

- `mode: xhr` invokes Chrome's native `Network.replayXHR` for a captured XHR,
  without edits.
- `mode: fetch` executes an editable fetch in the original frame's isolated world
  or worker context, with current browser credentials and normal CORS enforcement.
  This is not byte-identical replay. Browser-managed headers are regenerated;
  header edits override case-insensitively. Redirects are manual and URL edits
  cannot change origins, preventing accidental credential forwarding. Uploaded
  file data is not reconstructed from incomplete CDP post-data records.

Use the returned `after_cursor` to inspect the replay's actual network traffic.
Cookies and authorization can have changed since the original request. A closed
target, expired native request ID, or destroyed frame produces an explicit error.

## Retention and lifecycle

Capture is local to the MCP server process, in memory. It is not a durable archive.
The event buffer retains up to 4000 events or 16 MiB; individual events over 1 MiB
are dropped. Evictions and oversized events increment `dropped_events`. Cursor
pagination reports the oldest retained event. Headers and payloads may contain
credentials or personal data; retrieve and disclose only what the task requires.

Chrome retains up to 32 MiB of network body data per target and 8 MiB per resource.
Bodies may be unavailable after eviction, navigation or target closure. Body
retrieval reports those errors rather than substituting empty content. Binary
bodies are base64 and returned with an explicit encoding flag; reassemble paged
base64 before decoding. Body offsets and limits count characters.

`browser_capture stop` clears event data and closes the transport. MCP shutdown
also closes it. Chrome remains running. A disconnected transport requires an
explicit stop/start; it does not silently reconnect and imply uninterrupted capture.

## Verification

Run focused regressions with:

```sh
python3 tests/test_browser_cdp.py -v
```

With the desktop container and Chrome running, include native browser checks:

```sh
FERNANDO_CDP_LIVE=1 python3 tests/test_browser_cdp.py -v
```

The live test creates an isolated loopback HTTP fixture and a temporary browser
tab, and removes both afterward. It exercises actual headers/cookies, bodies,
redirects, worker traffic, WebSocket frames, native/edited replay, waits and cleanup.
