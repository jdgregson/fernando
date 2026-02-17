# Tmux Web Interface

Web-based interface for managing and interacting with tmux sessions with integrated Kasm desktop browser.

## Features

- Create and manage tmux sessions
- Split view with multiple terminals or browser panes
- Fully responsive terminal and browser integration
- Switch between terminal and Kasm desktop browser in each pane

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Quick Start
```bash
./start.sh
```

### Manual Start
```bash
# Start Kasm desktop
docker-compose up -d

# Start Flask app
python run.py
```

Open http://localhost:5000

## Using Split View

1. Click "Split" to create a second pane
2. Click on each pane to select it
3. Use the Terminal/Browser buttons in each pane to switch between terminal and Kasm desktop
4. Attach different tmux sessions to each terminal pane

## Architecture

```
src/
├── __init__.py          # App factory
├── config.py            # Configuration management
├── routes/              # Request handlers
│   ├── web.py          # HTTP routes
│   └── websocket.py    # WebSocket handlers
├── services/            # Business logic
│   └── tmux.py         # Tmux session management
├── models/              # Data models (future expansion)
├── templates/           # HTML templates
└── static/              # Static assets
    ├── manifest.json   # PWA manifest
    ├── sw.js           # Service worker
    └── icons/          # PWA icons
```

## Future Expansion Points

- **Authentication**: Add user auth in `src/services/auth.py`
- **Persistence**: Add session storage in `src/models/session.py`
- **Collaboration**: Multi-user session sharing
- **Logging**: Audit trail in `src/services/logging.py`
- **API**: RESTful API in `src/routes/api.py`
