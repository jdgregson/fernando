# Fernando Security Model

## Threat Model

Fernando is a single-user system. The primary threat is cross-origin attacks (CSRF): a malicious website convincing the user's browser to make authenticated requests to Fernando. Cloudflare Zero Trust authenticates the user but does NOT validate the request origin — any site the user visits can make requests that Cloudflare will forward with valid session cookies. The browser blocks cross-origin responses (CORS), but POST side effects execute server-side before the response is blocked.

## Authentication Mechanisms

### API Key (`/tmp/fernando-api-key`)

A static secret generated at startup. It is the sole authentication mechanism for all Fernando endpoints that perform actions or accept data.

- **WebSocket**: Required on connect via `api_key` query parameter. Validated in `src/routes/websocket.py`.
- **Flask POST routes**: Required via `X-API-Key` header or `api_key` form field. Validated by `_check_api_key()` in `src/routes/web.py`.
- **Frontend access**: Rendered into templates as `{{ api_key }}` — available to `index.html` and `chat.html`.
- **Scripts/MCP servers**: Read directly from `/tmp/fernando-api-key` when making internal HTTP calls.

This key is CSRF-safe because cross-origin JavaScript cannot read it (same-origin policy prevents loading Fernando pages), and browsers do not auto-attach custom headers cross-origin.

### WebSocket CSRF Tokens

Per-session CSRF tokens are issued after WebSocket connect and validated on state-changing WebSocket messages. Managed in `src/routes/websocket.py`.

### Cloudflare Zero Trust (External)

Sits in front of Fernando when exposed to the network. Authenticates users via browser cookies. Does NOT protect against CSRF — see threat model above.

## Mandatory Rules for All New Endpoints

1. **Every Flask POST/PUT/DELETE route MUST call `_check_api_key()` and return 401 on failure.** No exceptions. This applies even if the endpoint seems low-risk.
2. **Every frontend caller of a POST endpoint MUST include the API key** via `X-API-Key` header.
3. **Every script or MCP server making internal HTTP POST calls MUST read and send the API key.**
4. **GET routes that serve sensitive content** (file contents, configuration, secrets) should also require API key auth via `X-API-Key` header or query parameter.
5. **Prefer WebSocket for state-changing operations** when possible — it already has API key + CSRF token protection. Only use Flask POST routes when WebSocket is impractical (e.g., file uploads with multipart form data).

## What NOT to Rely On

- **CORS**: Blocks responses, not requests. A POST with side effects will execute.
- **Cloudflare Zero Trust**: Authenticates users, not origins. Doesn't prevent CSRF.
- **"It's internal only"**: Fernando may be exposed via tunnel/proxy. Always authenticate.
- **SameSite cookies**: Fernando doesn't use cookie-based auth, so this is irrelevant. The API key in a custom header is the equivalent protection.
