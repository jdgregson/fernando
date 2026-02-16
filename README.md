# Tmux Web Interface

Web-based interface for managing and interacting with tmux sessions.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python run.py
```

Open http://localhost:5000

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
