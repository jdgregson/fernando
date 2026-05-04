#!/bin/bash
# Mac setup for Fernando.
# Requires: Docker Desktop (with Enhanced Container Isolation enabled), Homebrew, Python 3.11+
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
command -v docker >/dev/null || { echo "Install Docker Desktop first"; exit 1; }

echo "Installing dependencies via Homebrew..."
brew install nginx tmux openssl

echo "Creating Python venv..."
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt

echo "Installing Kiro CLI..."
command -v kiro-cli >/dev/null || curl -fsSL https://cli.kiro.dev/install | bash

echo "Pulling SilverBullet image..."
docker pull zefhemel/silverbullet@sha256:6c36ff15f2230dbe3bca7e5d0c85a59c7dc831ce694517850ed5797775824d71

echo "Building Kasm desktop container (linux/amd64, uses Rosetta emulation)..."
docker compose -f docker-compose.mac.yml build

echo "Seeding config..."
[ -f config ] || cp config.example config

echo "Installing Kiro steering file..."
mkdir -p "$HOME/.kiro/steering"

echo "Installing Jupyter custom theme..."
jupyter_custom_dst="$HOME/.jupyter/custom"
if [ -L "$jupyter_custom_dst" ]; then
    echo "  Symlink already exists, skipping."
elif [ -d "$jupyter_custom_dst" ]; then
    echo "  WARNING: $jupyter_custom_dst is a regular directory. Remove it and re-run setup to use the repo copy."
else
    mkdir -p "$HOME/.jupyter"
    ln -s "$REPO_DIR/jupyter/custom" "$jupyter_custom_dst"
    echo "  Symlinked $jupyter_custom_dst -> $REPO_DIR/jupyter/custom"
fi
instructions_dst="$HOME/.kiro/steering/instructions.md"
if [ -L "$instructions_dst" ]; then
    echo "  Symlink already exists, skipping."
elif [ -f "$instructions_dst" ]; then
    echo "  WARNING: $instructions_dst is a regular file, not a symlink. Remove it and re-run setup to use the repo copy."
else
    ln -s "$REPO_DIR/instructions.md" "$instructions_dst"
    echo "  Symlinked $instructions_dst -> $REPO_DIR/instructions.md"
fi

echo "Registering MCP servers with Kiro CLI..."
MCP_CONFIG="$HOME/.kiro/settings/mcp.json"
mkdir -p "$(dirname "$MCP_CONFIG")"
VENV_PY="$REPO_DIR/venv/bin/python"
python3 - "$MCP_CONFIG" "$VENV_PY" "$REPO_DIR" <<'PY'
import json, os, sys
cfg_path, venv_py, repo_dir = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path) as f:
        cfg = json.load(f)
servers = cfg.setdefault("mcpServers", {})
for name in ("fernando_mcp", "desktop_mcp", "microsoft_mcp"):
    servers[name.replace("_mcp", "")] = {
        "command": venv_py,
        "args": [os.path.join(repo_dir, "mcp_servers", f"{name}.py")],
        "env": {},
        "disabled": False,
    }
with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"  Wrote {cfg_path}")
PY

echo ""
echo "Done. Next steps:"
echo "  1. Enable cron + at daemons (one-time, requires sudo):"
echo "     ./scripts/mac-enable-schedulers.sh"
echo "  2. Start Fernando:"
echo "     FERNANDO_COMPOSE_FILE=docker-compose.mac.yml \\"
echo "     FERNANDO_NGINX_TEMPLATE=nginx.conf.mac.template \\"
echo "     ./scripts/start.sh -f"
