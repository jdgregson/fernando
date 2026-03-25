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

The WebSocket connection requires API key authentication. Flask routes do not have authentication.

### Examples:
- ✅ Flask: Serve index.html, proxy Kasm desktop (read-only)
- ✅ WebSocket: Create sessions, manage subagents, terminate processes, modify state
- ❌ Flask: POST/PUT/DELETE endpoints, any state-changing operations

## Self-Mutation

After making code changes to Fernando, use the `mutate` MCP tool to restart Fernando and apply changes. This runs `stop.sh && start.sh` in a detached background process so your Kiro CLI session survives the restart. The tool blocks until Fernando is healthy or reports failure with logs — no manual health-check needed.

**Important**: If you changed MCP server code (`mcp_servers/`), the user must manually restart their Kiro CLI session since MCP servers are loaded at CLI startup.

## Microsoft 365 Login Flow

When the user asks to log in to Microsoft:

1. Call `microsoft_login` to get the sign-in URL
2. Present the URL to the user and ask: **"Want me to open this in the Kasm desktop browser, or would you prefer to sign in yourself?"**
   - If they want you to do it: open the URL in the Kasm desktop browser (the user must already be signed into their Microsoft account in that browser)
   - If they prefer to sign in themselves (OOB): just give them the URL
3. If the user signed in OOB and comes back with a callback URL (`localhost:8080/auth/callback?code=...`), use `curl` to hit that URL locally so the server can exchange the auth code for a token
4. Verify with `microsoft_status` that authentication succeeded

