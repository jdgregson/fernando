#!/bin/bash
# Restart Fernando without killing the calling process.
# Runs stop/start in a detached process so tmux sessions (and Kiro agents) survive.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

nohup bash -c "
    cd '$SCRIPT_DIR'
    # Notify frontend that a mutate is happening
    curl -s -X POST http://localhost:5000/api/mutating > /dev/null 2>&1
    sleep 0.5
    ./stop.sh
    ./start.sh
" > /tmp/fernando-mutate.log 2>&1 &

echo "Fernando restart initiated in background (PID: $!)"
echo "Log: /tmp/fernando-mutate.log"
