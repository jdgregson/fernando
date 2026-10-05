#!/bin/bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$REPO_DIR/scripts/desktop-shares.py" prepare
compose_file="${FERNANDO_COMPOSE_FILE:-docker-compose.yml}"
if [[ "$compose_file" != /* ]]; then
    compose_file="$REPO_DIR/$compose_file"
fi
compose=(docker compose --project-directory "$REPO_DIR" -f "$compose_file")
if [ -f "$REPO_DIR/docker-compose.override.yml" ]; then
    compose+=(-f "$REPO_DIR/docker-compose.override.yml")
fi
compose+=(-f "$REPO_DIR/data/shared-directories.compose.json")
"${compose[@]}" "$@"
