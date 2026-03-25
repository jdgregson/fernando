# Fernando

Web-based terminal interface for managing tmux sessions with integrated Kasm desktop, Kiro CLI agent management, and Microsoft 365 integration.

## Features

- **Multi-session tmux management**: Create, attach, and manage multiple tmux sessions through a web interface
- **Integrated Kasm desktop**: Full Linux desktop environment accessible via browser with VNC proxy
- **Kiro CLI integration**: Quick-launch Kiro CLI sessions (standard and unchained mode)
- **Real-time terminal**: WebSocket-based terminal with xterm.js for responsive interaction
- **Mobile-responsive**: Touch-optimized UI with safe area support for mobile devices (designed for iPhone), including iOS dictation input and prev/next terminal navigation
- **Session types**: Shell, Kiro CLI, and Kiro Unchained sessions with one-click creation
- **Inline session renaming**: Double-click or long-press to rename sessions
- **Subagent management**: Spawn, schedule, and manage Kiro CLI subagents with isolated workspaces
- **Self-mutation**: Live restart capability for applying code changes without losing sessions
- **Microsoft 365 integration**: Email, calendar, contacts, OneDrive, OneNote, and To Do via OAuth and Microsoft Graph API
- **MCP servers**: Model Context Protocol servers for subagent management, desktop automation, and Microsoft 365
- **CSRF protection**: WebSocket CSRF token validation
- **OSC 52 clipboard**: Terminal clipboard integration
- **PWA support**: Installable as a Progressive Web App

## Operating Model Warning

The current operating model of Fernando assumes that you are running it on localhost or on a host only accessible by trusted parties. Fernando does not currently support authentication or multi-user scenarios. It is critical that you do not expose Fernando to the internet. If your Fernando instance is accessible over the network or internet, anyone can connect to the websocket and assume complete control of Fernando.

If you host Fernando on a hostname other than localhost, configure the ALLOWED_ORIGINS setting to allow only the hostnames you use. If the `*` origin is used, any website opened in your browser can connect to the websocket and assume complete control of Fernando.

## Prerequisites

You will need the following installed and working for your user:
- docker
- docker-compose
- nginx
- python
- kiro-cli
- at

## Installation

```bash
./setup.sh   # First-time setup (builds desktop container, etc.)
./start.sh   # Start all services
```

The start script will automatically:
- Create a Python virtual environment if needed
- Install all required dependencies
- Configure and start all services

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
| `NGINX_PORT` | `8080` | User-facing nginx port |
| `FLASK_HOST` | `0.0.0.0` | Flask backend bind address |
| `FLASK_PORT` | `5000` | Flask backend port (nginx proxies to this) |
| `DEBUG` | `true` | Enable debug mode |
| `TMUX_HISTORY_LINES` | `32768` | Number of lines to keep in tmux history |

## Usage

### Quick Start
```bash
./start.sh
```
Access at http://localhost:8080

### Foreground Mode
```bash
./start.sh -f
```

### Stop
```bash
./stop.sh
```

### Restart (apply code changes)
```bash
./restart.sh
```

## Session Types

- **Shell**: Standard bash shell session
- **Kiro**: Kiro CLI in default mode
- **Kiro-Unchained**: Kiro CLI with all tools enabled (`-a` flag)

## Architecture

```
src/
├── __init__.py          # Flask app factory with SocketIO
├── config.py            # Environment-based configuration
├── routes/
│   ├── web.py           # HTTP routes (index, Kasm proxy)
│   └── websocket.py     # WebSocket handlers (terminal I/O, sessions)
├── services/
│   ├── tmux.py          # Tmux session lifecycle management
│   ├── docker.py        # Kasm desktop container management
│   ├── subagent.py      # Subagent session management
│   └── subagent_core.py # Subagent spawning, scheduling, and lifecycle
├── templates/
│   └── index.html       # Main terminal interface
└── static/
    ├── sw.js            # Service worker for PWA
    └── manifest.json    # PWA manifest

mcp_servers/
├── fernando_mcp.py      # MCP server for subagent management and self-mutation
├── desktop_mcp.py       # MCP server for Kasm desktop automation
└── microsoft_mcp.py     # MCP server for Microsoft 365 integration

subagents/               # Subagent workspace directory

setup.sh                 # First-time setup script
start.sh                 # Start all services
stop.sh                  # Stop all services
restart.sh               # Restart services (preserves sessions)
mutate.sh                # Self-mutation script (used by MCP)
docker-compose.yml       # Kasm desktop container definition
Dockerfile.desktop       # Custom Kasm desktop image
```

## Technical Details

- **Backend**: Flask + Flask-SocketIO for real-time communication
- **Frontend**: xterm.js for terminal emulation with fit and web-links addons
- **Proxy**: nginx reverse proxy for Kasm desktop WebSocket/HTTP traffic
- **Desktop**: Kasm Workspaces container with VNC server on port 6901
- **Terminal**: PTY-based tmux attachment with proper resize and signal handling
- **Microsoft 365**: OAuth2 authentication with Microsoft Graph API via MCP server
