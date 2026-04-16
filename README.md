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

## Architecture

Fernando is a Flask + Flask-SocketIO backend with an xterm.js frontend, behind an nginx reverse proxy. A Kasm Workspaces Docker container provides the integrated Linux desktop (VNC on port 6901). Three MCP servers (`mcp_servers/`) extend Kiro CLI with subagent management, desktop automation, and Microsoft 365 integration. A SilverBullet instance provides the integrated notes system, proxied through Flask with iOS PWA compatibility shims. All state-changing actions go through authenticated WebSocket connections; Flask HTTP POST routes require API key authentication.
