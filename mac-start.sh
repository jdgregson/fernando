#!/bin/bash

./scripts/stop.sh
FERNANDO_COMPOSE_FILE=docker-compose.mac.yml \
FERNANDO_NGINX_TEMPLATE=nginx.conf.mac.template \
./scripts/start.sh -f
