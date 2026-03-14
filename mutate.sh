#!/bin/bash
# Restart Fernando without killing the calling process.
# Runs stop/start in a detached process so tmux sessions (and Kiro agents) survive.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

nohup bash -c "
    cd '$SCRIPT_DIR'
    ./stop.sh
    ./start.sh
" > /tmp/fernando-mutate.log 2>&1 &

echo "Fernando restart initiated in background (PID: $!)"
echo "Log: /tmp/fernando-mutate.log"
