#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/restart_service.py" "$@"
