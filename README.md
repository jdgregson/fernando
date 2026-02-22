# Fernando

Web-based terminal interface for managing tmux sessions with integrated Kasm desktop and Kiro CLI agent management.

## Features

- **Multi-session tmux management**: Create, attach, and manage multiple tmux sessions through a web interface
- **Integrated Kasm desktop**: Full Linux desktop environment accessible via browser with VNC proxy
- **Kiro CLI integration**: Quick-launch Kiro CLI sessions (standard and unchained mode)
- **Real-time terminal**: WebSocket-based terminal with xterm.js for responsive interaction
- **Mobile-responsive**: Touch-optimized UI with safe area support for mobile devices (designed for iPhone)
- **Session types**: Shell, Kiro CLI, and Kiro Unchained sessions with one-click creation
- **MCP servers**: Model Context Protocol servers for Fernando and desktop automation

## Installation

```bash
./start.sh
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
│   ├── web.py          # HTTP routes (index, Kasm proxy, history)
│   └── websocket.py    # WebSocket handlers (terminal I/O, sessions)
├── services/
│   ├── tmux.py         # Tmux session lifecycle management
│   └── docker.py       # Kasm desktop container management
├── templates/
│   ├── index.html      # Main terminal interface
│   └── history.html    # Session history viewer
└── static/
    └── sw.js           # Service worker for PWA

mcp_servers/
├── fernando_mcp.py     # MCP server for Fernando subagent management
└── desktop_mcp.py      # MCP server for desktop automation

subagents/              # Subagent workspace directory
```

## Technical Details

- **Backend**: Flask + Flask-SocketIO for real-time communication
- **Frontend**: xterm.js for terminal emulation with fit and web-links addons
- **Proxy**: nginx reverse proxy for Kasm desktop WebSocket/HTTP traffic
- **Desktop**: Kasm Workspaces container with VNC server on port 6901
- **Terminal**: PTY-based tmux attachment with proper resize and signal handling
