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

