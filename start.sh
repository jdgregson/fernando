#!/bin/bash

# Clean up stale Werkzeug environment variables
unset WERKZEUG_RUN_MAIN WERKZEUG_SERVER_FD

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install/update requirements
echo "Checking dependencies..."
pip install -q -r requirements.txt

# Load config
NGINX_HOST=127.0.0.1
NGINX_PORT=8080
FLASK_PORT=5000
ALLOWED_ORIGINS="http://localhost:8080"
if [ -f config ]; then
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        case "$key" in
            NGINX_HOST) NGINX_HOST="$value" ;;
            NGINX_PORT) NGINX_PORT="$value" ;;
            FLASK_PORT) FLASK_PORT="$value" ;;
            ALLOWED_ORIGINS) ALLOWED_ORIGINS="$value" ;;
        esac
    done < config
fi

# Override with environment variables if set
NGINX_HOST=${NGINX_HOST:-127.0.0.1}
NGINX_PORT=${NGINX_PORT:-8080}
FLASK_PORT=${FLASK_PORT:-5000}
ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-http://localhost:8080}

DETACHED=true
if [[ "$1" == "-f" || "$1" == "--foreground" ]]; then
    DETACHED=false
fi

# Generate API key
echo "Generating API key..."
API_KEY=$(openssl rand -hex 32)
echo "$API_KEY" > /tmp/fernando-api-key
chmod 600 /tmp/fernando-api-key

# Generate VNC password BEFORE starting container
echo "Generating VNC password..."
VNC_PASSWORD=$(openssl rand -base64 16)
echo "$VNC_PASSWORD" > /tmp/fernando-vnc-password
chmod 600 /tmp/fernando-vnc-password
export VNC_PW="$VNC_PASSWORD"

# Generate nginx.conf with VNC auth
echo "Generating nginx configuration..."
VNC_AUTH=$(echo -n "kasm_user:$VNC_PASSWORD" | base64)

sed -e "s|{{NGINX_HOST}}|$NGINX_HOST|g" \
    -e "s|{{NGINX_PORT}}|$NGINX_PORT|g" \
    -e "s|{{FLASK_PORT}}|$FLASK_PORT|g" \
    -e "s|{{ALLOWED_ORIGINS}}|$ALLOWED_ORIGINS|g" \
    -e "s|{{VNC_AUTH}}|$VNC_AUTH|g" \
    nginx.conf.template > nginx.conf

# Start Kasm desktop container with VNC_PW
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
pkill nginx 2>/dev/null
nginx -c "$SCRIPT_DIR/nginx.conf"

# Start Flask app
echo "Starting Flask application on port $FLASK_PORT..."
if [ "$DETACHED" = true ]; then
    python rrun_fernando.py > /tmp/fernando-flask.log 2>&1 &
    FLASK_PID=$!
    echo "Flask started in background (PID: $FLASK_PID)"
    echo "Access at http://localhost:$NGINX_PORT"
    echo "Logs: tail -f /tmp/fernando-flask.log"
else
    echo "Access at http://localhost:$NGINX_PORT"
    python run_fernando.py
fi
