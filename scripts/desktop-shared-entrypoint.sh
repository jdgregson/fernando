#!/bin/bash
set -euo pipefail
python3 /dockerstartup/desktop-shared-access.py
exec "$@"
