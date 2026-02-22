#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR""

echo "Stopping Flask..."
pkill -9 -f "run_fernando.py"

echo "Stopping nginx..."
pkill -9 nginx

echo "Stopping desktop container..."
docker-compose down

echo "All services stopped."
