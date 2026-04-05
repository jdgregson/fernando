#!/bin/bash

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "Stopping Flask (graceful)..."
pkill -TERM -f "run_fernando.py"
# Give it a moment to clean up child processes
sleep 2
# Force kill only if still running
pkill -0 -f "run_fernando.py" 2>/dev/null && pkill -9 -f "run_fernando.py"

echo "Stopping nginx..."
nginx -c "$REPO_DIR/nginx.conf" -s quit 2>/dev/null || pkill nginx

echo "Stopping desktop container..."
docker compose down

echo "Stopping kiro-cli acp processes..."
pkill -TERM -f "kiro-cli-chat acp" 2>/dev/null
sleep 2
pkill -9 -f "kiro-cli-chat acp" 2>/dev/null
# Also kill their parent kiro-cli wrappers
pkill -TERM -f "kiro-cli acp" 2>/dev/null
sleep 1
pkill -9 -f "kiro-cli acp" 2>/dev/null

echo "Reaping any remaining zombies owned by us..."
# Kill any orphaned tmux attach-session processes we spawned
pkill -TERM -f "tmux attach-session" 2>/dev/null
sleep 1
pkill -9 -f "tmux attach-session" 2>/dev/null

echo "All services stopped."
