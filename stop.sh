#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping Flask (graceful)..."
pkill -TERM -f "run_fernando.py"
# Give it a moment to clean up child processes
sleep 2
# Force kill only if still running
pkill -0 -f "run_fernando.py" 2>/dev/null && pkill -9 -f "run_fernando.py"

echo "Stopping nginx..."
nginx -c "$SCRIPT_DIR/nginx.conf" -s quit 2>/dev/null || pkill nginx

echo "Stopping desktop container..."
docker-compose down

echo "Reaping any remaining zombies owned by us..."
# Kill any orphaned tmux attach-session processes we spawned
pkill -TERM -f "tmux attach-session" 2>/dev/null
sleep 1
pkill -9 -f "tmux attach-session" 2>/dev/null

echo "All services stopped."
