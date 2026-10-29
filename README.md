# Fernando

An AI agent runtime — a persistent environment where Kiro CLI agents live, work, and interact with the outside world through a desktop, terminal sessions, and integrated services.

## Features

- **ACP chat interface**: Full GUI chat with Kiro CLI agents from any browser or mobile device
- **Integrated desktop**: Kasm Linux desktop environment with browser, accessible via VNC proxy
- **Terminal sessions**: Tmux-based shell, Kiro CLI, and Kiro Unchained sessions
- **Subagent management**: Spawn, schedule, and manage autonomous Kiro CLI agents
- **Microsoft 365 integration**: Email, calendar, contacts, OneDrive, OneNote, and To Do
- **Integrated notes**: SilverBullet markdown notebook with live sync, graph view, and iOS PWA support
- **Inbound email automation**: Rule-based email monitoring that dispatches subagents on matching messages
- **Conversation memory**: Semantic search (RAG) across past chat sessions for persistent context
- **Self-mutation**: Agents can modify and restart Fernando to evolve their own runtime
- **Mobile-responsive PWA**: Touch-optimized, installable, works on phone and laptop

## Operating Model Warning

The current operating model of Fernando assumes that you are running it on localhost or on a host only accessible by trusted parties. Fernando does not currently support authentication or multi-user scenarios. It is critical that you do not expose Fernando to the internet. If your Fernando instance is accessible over the network or internet, anyone can connect to the websocket and assume complete control of Fernando.

If you host Fernando on a hostname other than localhost, configure the ALLOWED_ORIGINS setting to allow only the hostnames you use. If the `*` origin is used, any website opened in your browser can connect to the websocket and assume complete control of Fernando.

## Installation

Requires a fresh Ubuntu Server 24.04 system.

```bash
curl -fsSL https://raw.githubusercontent.com/jdgregson/fernando/refs/heads/master/setup.sh | sudo bash
sudo systemctl start fernando
```

Access at http://localhost:8080

## Configuration

Fernando can be configured using either a config file or environment variables. Environment variables take precedence over the config file.

**Config file (recommended):**
```bash
cp config.example config
# Edit config with your settings
```

**Environment variables:**
```bash
export ALLOWED_ORIGINS=https://fernando.yourdomain.com
export NGINX_PORT=8080
```

### Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_ORIGINS` | `http://localhost:8080` | Comma-separated list of allowed WebSocket origins |
| `NGINX_HOST` | `127.0.0.1` | nginx bind address |
| `NGINX_PORT` | `8080` | User-facing nginx port |
| `FLASK_HOST` | `127.0.0.1` | Flask backend bind address |
| `FLASK_PORT` | `5000` | Flask backend port (nginx proxies to this) |
| `DEBUG` | `true` | Enable debug mode |
| `TMUX_HISTORY_LINES` | `32768` | Number of lines to keep in tmux history |

## Usage

```bash
sudo systemctl start fernando    # Start
sudo systemctl stop fernando     # Stop
sudo systemctl restart fernando  # Restart
```

## Session Types

- **Shell**: Standard bash shell session
- **Kiro**: Kiro CLI in default mode
- **Kiro-Unchained**: Kiro CLI with all tools enabled (`-a` flag)
- **ACP Chat**: Graphical chat UI for conversational interaction

## Recurring subagents

Use `spawn_subagent` with `cron_schedule` and `schedule_mode: "resume"` for a monitor that retains its conversation:

```json
{
  "task_id": "monitor-build",
  "task": "Monitor the build until it completes, then report the result to me and cancel your schedule.",
  "cron_schedule": "*/5 * * * *",
  "schedule_mode": "resume",
  "schedule_timezone": "America/Los_Angeles"
}
```

Resume mode starts one child immediately and sends subsequent checks to that same session. It preserves the workspace, parent relationship, and native conversation. Busy ticks are skipped, idle children sleep between checks unless open in a pane, and schedules survive application restarts without replaying missed checks. Resume schedules are stored in `data/subagent_schedules.json`; they do not install user-crontab entries. Expressions use five cron fields and an IANA timezone. `wake_message` optionally supplies the recurring prompt.

The child can call `manage_subagent_schedule` with `action: "get"`, `"update"`, or `"cancel"`, omitting `session_id` to target itself. The parent supplies the child's session ID. Updates accept `cron_schedule`, `timezone`, and `wake_message`. Cancelling stops future checks but preserves the conversation; ending a turn alone does not stop monitoring. Terminating, archiving, or deleting the child cancels its schedule. Schedule status includes the next run, last result, and any wake-up error, and is also included in task status for resume-mode tasks.

The existing `schedule_mode: "spawn"` remains the default: `at_schedule` launches once later, and `cron_schedule` launches a fresh child on each tick. Resume mode requires an identified parent and `cron_schedule`; it cannot be combined with `at_schedule`.

## Architecture

Fernando is a Flask + Flask-SocketIO backend with an xterm.js frontend, behind an nginx reverse proxy. A Kasm Workspaces Docker container provides the integrated Linux desktop (VNC on port 6901). Three MCP servers (`mcp_servers/`) extend Kiro CLI with subagent management, desktop automation, and Microsoft 365 integration. A SilverBullet instance provides the integrated notes system, proxied through Flask with iOS PWA compatibility shims. All state-changing actions go through authenticated WebSocket connections; Flask HTTP POST routes require API key authentication.
