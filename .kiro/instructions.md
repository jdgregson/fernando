# Fernando Development Instructions

## Code Formatting

After making changes to Python files, always run:
```bash
cd ~/fernando && source venv/bin/activate && ruff format .
```

This ensures consistent formatting and removes trailing whitespace.

## API Architecture

**CRITICAL: All actions must go through WebSocket, not Flask routes.**

- **Flask routes (src/routes/web.py)**: ONLY for GET requests that serve HTML or static data
- **WebSocket handlers (src/routes/websocket.py)**: ALL actions (create, update, delete, etc.)

### Examples:
- ✅ Flask: Serve index.html, proxy Kasm desktop (read-only)
- ✅ WebSocket: Create sessions, manage subagents, terminate processes, modify state
- ❌ Flask: POST/PUT/DELETE endpoints, any state-changing operations

## Security — MANDATORY

**Read `.kiro/steering/security.md` for the full security model.** The short version:

- **Every Flask POST/PUT/DELETE route MUST call `_check_api_key()` and return 401 on failure.** No exceptions, even if the endpoint seems low-risk or internal-only.
- **Every frontend caller MUST send the API key** via `X-API-Key` header.
- **Every script or MCP server making internal HTTP POST calls MUST read `/tmp/fernando-api-key` and send it.**
- If a Flask POST route is genuinely needed (e.g., file uploads where WebSocket is impractical), it is allowed — but it MUST be authenticated with the API key. An unauthenticated POST endpoint is a CSRF vulnerability exploitable by any website the user visits.
- **Never assume Cloudflare Zero Trust or CORS protects POST endpoints.** They don't. See the security steering doc for why.

## Self-Mutation

After making code changes to Fernando, use the `mutate` MCP tool to restart Fernando and apply changes. This runs `scripts/stop.sh && scripts/start.sh` in a detached background process so your Kiro CLI session survives the restart. The tool blocks until Fernando is healthy or reports failure with logs — no manual health-check needed.

**Important**: If you changed MCP server code (`mcp_servers/`), the user must manually restart their Kiro CLI session since MCP servers are loaded at CLI startup.

## Microsoft 365 Login Flow

When the user asks to log in to Microsoft:

1. Call `microsoft_login` to get the sign-in URL
2. Present the URL to the user and ask: **"Want me to open this in the Kasm desktop browser, or would you prefer to sign in yourself?"**
   - If they want you to do it: open the URL in the Kasm desktop browser (the user must already be signed into their Microsoft account in that browser)
   - If they prefer to sign in themselves (OOB): just give them the URL
3. If the user signed in OOB and comes back with a callback URL (`localhost:8080/auth/callback?code=...`), use `curl` to hit that URL locally so the server can exchange the auth code for a token
4. Verify with `microsoft_status` that authentication succeeded

