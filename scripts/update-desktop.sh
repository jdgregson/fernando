#!/bin/bash
set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/desktop-update.py" start --no-cache --wait
