#!/bin/bash

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
export FERNANDO_COMPOSE_FILE=docker-compose.mac.yml
export FERNANDO_NGINX_TEMPLATE=nginx.conf.mac.template
bash "$REPO_DIR/scripts/stop.sh"
exec bash "$REPO_DIR/scripts/start.sh" -f
