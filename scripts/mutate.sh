#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$REPO_DIR/scripts/restart_guard.py" "$@"

nohup bash -c 'sleep 2; exec sudo -n /bin/systemctl restart fernando' </dev/null > /tmp/fernando-mutate.log 2>&1 &

echo "Fernando restart initiated in background (PID: $!)"
echo "Log: /tmp/fernando-mutate.log"
