#!/bin/bash
# Restart the Fernando service.

exec bash "$(dirname "$0")/mutate.sh" "$@"
