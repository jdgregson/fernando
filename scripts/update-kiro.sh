#!/bin/bash
# Update Kiro CLI to the latest version
# Removes existing binaries first to avoid interactive prompt

set -e

echo "Current version: $(kiro-cli --version 2>&1 || echo 'not installed')"
echo "Removing existing binaries..."
rm -f ~/.local/bin/kiro-cli ~/.local/bin/kiro-cli-chat ~/.local/bin/kiro-cli-term

echo "Installing latest version..."
curl -fsSL https://cli.kiro.dev/install | bash

echo "New version: $(kiro-cli --version 2>&1)"
