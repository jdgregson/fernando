#!/bin/bash
# Restart Fernando without killing the calling process.
# Runs stop/start in a detached process so tmux sessions (and Kiro agents) survive.

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

nohup bash -c "
    cd '$REPO_DIR'
    echo '[mutate.sh] Starting mutate sequence'
    # Notify frontend that a mutate is happening and save loaded session state
    API_KEY=\$(cat /tmp/fernando-api-key 2>/dev/null)
    echo '[mutate.sh] API key loaded, calling /api/mutating...'
    RESP=\$(curl -s -X POST -H \"X-API-Key: \$API_KEY\" http://localhost:5000/api/mutating 2>&1)
    echo \"[mutate.sh] /api/mutating response: \$RESP\"
    sleep 2
    echo '[mutate.sh] Starting stop.sh'
    ./scripts/stop.sh
    echo '[mutate.sh] Starting start.sh'
    ./scripts/start.sh
" > /tmp/fernando-mutate.log 2>&1 &

echo "Fernando restart initiated in background (PID: $!)"
echo "Log: /tmp/fernando-mutate.log"
