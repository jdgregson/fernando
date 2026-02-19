#!/bin/bash

# Activate venv if not already activated
if [ -z "$VIRTUAL_ENV" ]; then
    source venv/bin/activate
fi

DETACHED=true
if [[ "$1" == "-f" || "$1" == "--foreground" ]]; then
    DETACHED=false
fi

# Start Kasm desktop container
echo "Starting Kasm desktop container..."
docker-compose up -d fernando-desktop

# Wait for Kasm to be ready
echo "Waiting for Kasm to be ready..."
for i in {1..30}; do
    if curl -sk https://localhost:6901 > /dev/null 2>&1; then
        echo "Kasm is ready!"
        break
    fi
    sleep 1
done

# Start nginx
echo "Starting nginx..."
pkill nginx 2>/dev/null
nginx -c /home/coder/fernando/nginx.conf

# Start Flask app
echo "Starting Flask application..."
if [ "$DETACHED" = true ]; then
    nohup python run.py > /tmp/fernando-flask.log 2>&1 &
    echo "Flask started in background (PID: $!)"
    echo "Access at http://localhost:8080"
    echo "Logs: tail -f /tmp/fernando-flask.log"
else
    echo "Access at http://localhost:8080"
    python run.py
fi
