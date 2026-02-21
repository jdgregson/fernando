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
pip install -r requirements.txt
```

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

### Manual Start
```bash
# Start Kasm desktop container
docker-compose up -d fernando-desktop

# Start nginx proxy
nginx -c /home/coder/fernando/nginx.conf

# Start Flask app
python run.py
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
