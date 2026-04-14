#!/bin/bash
# Rebuild the Kasm desktop container to pull latest rolling updates

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Preserve VNC password across container recreation
if [ -f /tmp/fernando-vnc-password ]; then
    export VNC_PW="$(cat /tmp/fernando-vnc-password)"
else
    echo "Warning: /tmp/fernando-vnc-password not found, generating new password"
    VNC_PW="$(openssl rand -base64 16)"
    echo "$VNC_PW" > /tmp/fernando-vnc-password
    chmod 600 /tmp/fernando-vnc-password
    export VNC_PW
fi

echo "Pulling latest base image..."
docker compose pull

echo "Rebuilding desktop container..."
docker compose build --no-cache

echo "Restarting container..."
docker compose down
docker compose up -d

# Regenerate nginx VNC auth header to match
VNC_AUTH=$(echo -n "kasm_user:$VNC_PW" | base64)
sed -i "s|proxy_set_header Authorization \"Basic [^\"]*\"|proxy_set_header Authorization \"Basic $VNC_AUTH\"|" "$REPO_DIR/nginx.conf"
nginx -c "$REPO_DIR/nginx.conf" -s reload 2>/dev/null || true

echo "Desktop container updated."
