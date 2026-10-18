#!/bin/bash

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

cd "$REPO_DIR"
rm -rf "$install_dir"

echo "Pruning old backups (keeping last 3)..."
ls -1dt "$BACKUP_DIR"/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf

echo ""
echo "Stored versions:"
ls -1t "$BACKUP_DIR" 2>/dev/null || echo "  (none)"

python3 - "$REPO_DIR/data/settings.json" <<'PY'
import fcntl
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

settings_path = pathlib.Path(sys.argv[1])
settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
if settings.get("providers_opencode") is not True:
    print("Skipping OpenCode update: backend is disabled", flush=True)
    sys.exit(0)

home = pathlib.Path.home()
binary = home / ".opencode/bin/opencode"
backups = home / ".local/opencode-versions"
backups.mkdir(mode=0o700, parents=True, exist_ok=True)
lock = (backups / ".update.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

def version(path):
    value = subprocess.check_output([str(path), "--version"], text=True, timeout=30).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise RuntimeError(f"Unexpected OpenCode version: {value!r}")
    return value

def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Fernando-OpenCode-Updater"})
    return urllib.request.urlopen(request, timeout=60)

current = version(binary)
print(f"Current OpenCode version: {current}", flush=True)
with fetch("https://api.github.com/repos/anomalyco/opencode/releases/latest") as response:
    release = json.load(response)
tag = release["tag_name"]
if release["draft"] or release["prerelease"] or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
    raise RuntimeError("Expected a stable OpenCode release")
latest = tag[1:]
if tuple(map(int, latest.split("."))) < tuple(map(int, current.split("."))):
    raise RuntimeError(f"Refusing OpenCode downgrade from {current} to {latest}")

previous = backups / current
if not previous.exists():
    previous.mkdir(mode=0o700)
    shutil.copy2(binary, previous / "opencode")

if latest != current:
    if platform.system() != "Linux":
        raise RuntimeError("This updater requires Linux")
    arch = {"x86_64": "x64", "aarch64": "arm64"}[platform.machine()]
    baseline = "-baseline" if arch == "x64" and "avx2" not in pathlib.Path("/proc/cpuinfo").read_text().split() else ""
    asset_name = f"opencode-linux-{arch}{baseline}.tar.gz"
    asset, = [item for item in release["assets"] if item["name"] == asset_name]
    url = f"https://github.com/anomalyco/opencode/releases/download/{tag}/{asset_name}"
    if asset["browser_download_url"] != url or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"]):
        raise RuntimeError("Invalid OpenCode release asset metadata")
    with tempfile.TemporaryDirectory(prefix=".update-", dir=backups) as temporary:
        staging = pathlib.Path(temporary)
        archive = staging / asset_name
        with fetch(url) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if f"sha256:{digest}" != asset["digest"]:
            raise RuntimeError("OpenCode archive checksum mismatch")
        candidate = staging / "opencode"
        with tarfile.open(archive, "r:gz") as package:
            member, = [item for item in package.getmembers() if item.name in ("opencode", "./opencode")]
            if not member.isfile():
                raise RuntimeError("OpenCode archive binary is not a regular file")
            with package.extractfile(member) as source, candidate.open("wb") as output:
                shutil.copyfileobj(source, output)
        candidate.chmod(0o755)
        if version(candidate) != latest:
            raise RuntimeError("OpenCode candidate version does not match release")
        destination = backups / latest
        if destination.exists():
            raise RuntimeError(f"OpenCode backup already exists: {destination}")
        staging.rename(destination)
        with tempfile.TemporaryDirectory(prefix=".opencode-update-", dir=binary.parent) as install:
            replacement = pathlib.Path(install) / "opencode"
            shutil.copy2(destination / "opencode", replacement)
            os.replace(replacement, binary)
        if version(binary) != latest:
            raise RuntimeError("Installed OpenCode version does not match release")
        os.utime(destination, None)
    print(f"Installed OpenCode {latest}; new sessions will use this version", flush=True)

versions = sorted(
    (path for path in backups.iterdir() if path.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+", path.name)),
    key=lambda path: path.stat().st_mtime_ns,
    reverse=True,
)
for obsolete in versions[3:]:
    shutil.rmtree(obsolete)
print("Stored OpenCode versions:", ", ".join(path.name for path in versions[:3]), flush=True)
PY
