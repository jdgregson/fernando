#!/bin/bash
# Update Kiro CLI to the latest version
# Maintains the last 3 versions for rollback (binaries + installer archives)

set -e

BACKUP_DIR="$HOME/.local/kiro-versions"
BIN_DIR="$HOME/.local/bin"
BINARIES="kiro-cli kiro-cli-chat kiro-cli-term"

BASE_URL="https://prod.download.cli.kiro.dev/stable/latest"
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64) ARCH="x86_64" ;;
    arm64|aarch64) ARCH="aarch64" ;;
esac
ZIP_FILENAME="kirocli-${ARCH}-linux.zip"
ZIP_URL="${BASE_URL}/${ZIP_FILENAME}"

current_version=$(kiro-cli --version 2>&1 || echo 'not installed')
echo "Current version: $current_version"

mkdir -p "$BACKUP_DIR"

if [[ "$current_version" != "not installed" && -f "$BIN_DIR/kiro-cli" ]]; then
    version_tag=$(echo "$current_version" | tr ' ' '-')
    timestamp=$(date +%Y%m%d-%H%M%S)
    backup_name="${version_tag}_${timestamp}"
    backup_path="$BACKUP_DIR/$backup_name"
    
    echo "Backing up current version to $backup_path..."
    mkdir -p "$backup_path"
    for bin in $BINARIES; do
        if [[ -f "$BIN_DIR/$bin" ]]; then
            cp "$BIN_DIR/$bin" "$backup_path/"
        fi
    done

    echo "Pruning old backups (keeping last 3)..."
    ls -1dt "$BACKUP_DIR"/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf
fi

echo "Removing existing binaries..."
for bin in $BINARIES; do
    rm -f "$BIN_DIR/$bin"
done

install_dir=$(mktemp -d)
cd "$install_dir"

echo "Downloading $ZIP_URL..."
curl -fsSL -o "$ZIP_FILENAME" "$ZIP_URL"

echo "Installing latest version..."
curl -fsSL https://cli.kiro.dev/install -o install.sh
chmod +x install.sh
./install.sh --force

new_version=$(kiro-cli --version 2>&1)
echo "New version: $new_version"

new_version_tag=$(echo "$new_version" | tr ' ' '-')
new_timestamp=$(date +%Y%m%d-%H%M%S)
new_backup_name="${new_version_tag}_${new_timestamp}"
new_backup_path="$BACKUP_DIR/$new_backup_name"

if [[ ! -d "$new_backup_path" ]]; then
    echo "Saving new version archive to $new_backup_path..."
    mkdir -p "$new_backup_path"
    cp "$ZIP_FILENAME" "$new_backup_path/"
    for bin in $BINARIES; do
        if [[ -f "$BIN_DIR/$bin" ]]; then
            cp "$BIN_DIR/$bin" "$new_backup_path/"
        fi
    done
fi

rm -rf "$install_dir"

echo "Pruning old backups (keeping last 3)..."
ls -1dt "$BACKUP_DIR"/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf

echo ""
echo "Stored versions:"
ls -1t "$BACKUP_DIR" 2>/dev/null || echo "  (none)"
