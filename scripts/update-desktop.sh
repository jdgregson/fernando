#!/bin/bash
# Rebuild the Kasm desktop container to pull latest rolling updates

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "Pulling latest base image..."
docker compose pull

echo "Rebuilding desktop container..."
docker compose build --no-cache

echo "Restarting container..."
docker compose down
docker compose up -d

echo "Desktop container updated."
