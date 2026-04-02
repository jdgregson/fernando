# Fernando Architecture

## Overview

Fernando is an AI agent runtime — a persistent environment where Kiro CLI agents live, work, and interact with the outside world through a desktop, terminal sessions, and integrated services.

Under the hood, it's a Flask + SocketIO web app that manages tmux sessions, an integrated Kasm Linux desktop, and Microsoft 365 services. The entry point is `run_fernando.py` which creates the Flask app from `src/`.

## Request Flow

All state-changing actions go through WebSocket (authenticated via API key). Flask HTTP routes are read-only (serving HTML, proxying Kasm). nginx sits in front, proxying HTTP to Flask and WebSocket traffic to both Flask and the Kasm VNC server.

## Source Code (`src/`)

- `__init__.py` — Flask app factory, registers blueprints and SocketIO
- `config.py` — Environment-based config (host, port, debug, etc.)
- `routes/web.py` — HTTP routes: index page, Kasm desktop proxy, ACP chat interface
- `routes/websocket.py` — WebSocket handlers: terminal I/O, session CRUD, subagent management
- `services/tmux.py` — Tmux session lifecycle: create, attach, resize, cleanup
- `services/docker.py` — Kasm desktop container management
- `services/subagent.py` — Subagent session management (WebSocket layer)
- `services/subagent_core.py` — Subagent spawning, scheduling, cron/at integration
- `services/acp.py` — Agent Communication Protocol for chat-based sessions
- `templates/index.html` — Main terminal UI (xterm.js, session sidebar, mobile support)
- `templates/chat.html` — ACP chat interface
- `static/` — PWA manifest, service worker, icons, wallpaper

## MCP Servers (`mcp_servers/`)

These run as separate processes loaded by Kiro CLI at startup. Changing them requires restarting the Kiro CLI session (not just mutating Fernando).

- `fernando_mcp.py` — Subagent management, self-mutation (`mutate` tool), reboot
- `desktop_mcp.py` — Kasm desktop automation: screenshots, mouse/keyboard, browser DOM
- `microsoft_mcp.py` — Microsoft 365: mail, calendar, contacts, OneDrive, OneNote, To Do

## Scripts (`scripts/`)

Operational scripts for running Fernando:

- `start.sh` — Start all services (venv, nginx, docker, Flask). Uses `REPO_DIR` pointing to parent.
- `stop.sh` — Stop all services gracefully. Same `REPO_DIR` pattern.
- `restart.sh` — Restart via systemd (`sudo systemctl restart fernando`)
- `mutate.sh` — Self-mutation: runs stop/start in a detached process so tmux sessions survive. Called by `fernando_mcp.py`.
- `update-kiro.sh` — Update Kiro CLI installation
- `patch_cft_infobar.py` — Binary patch to remove Chrome for Testing infobar. Used during Docker image build only (`Dockerfile.desktop` COPYs it).

## Root-Level Files

- `setup.sh` — One-time server setup (installs deps, creates systemd service, builds Docker image)
- `run_fernando.py` — App entry point, signal handling, zombie reaping
- `docker-compose.yml` — Kasm desktop container definition (sysbox runtime)
- `Dockerfile.desktop` — Custom Kasm image with Chrome for Testing, CDP, wallpaper
- `nginx.conf.template` — nginx config template (start.sh generates `nginx.conf` from this)
- `config` / `config.example` — Runtime configuration (ports, origins)
- `requirements.txt` — Python dependencies

## Key Relationships

- `setup.sh` writes a systemd unit pointing to `scripts/start.sh` and `scripts/stop.sh`
- `scripts/start.sh` generates `nginx.conf` from `nginx.conf.template`, starts Docker, nginx, and Flask
- `scripts/mutate.sh` is invoked by `mcp_servers/fernando_mcp.py` via `os.path.join(_project_root, "scripts", "mutate.sh")`
- `Dockerfile.desktop` COPYs `scripts/patch_cft_infobar.py` for the Docker build
