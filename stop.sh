#!/bin/bash

echo "Stopping Flask..."
pkill -9 -f "python run.py"

echo "Stopping nginx..."
pkill -9 nginx

echo "Stopping Kasm container..."
docker-compose down

echo "All services stopped."
