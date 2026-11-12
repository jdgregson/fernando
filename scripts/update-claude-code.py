import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


REPOSITORY = 'agentclientprotocol/claude-agent-acp'
INSTALL_ROOT = Path.home() / '.local/share/fernando/claude-code'


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Fernando-Claude-Code-Updater'})
    return urllib.request.urlopen(request, timeout=60)


def release_asset(release, machine):
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected a stable Claude ACP release')
    arch = {'x86_64': 'x64', 'aarch64': 'arm64'}[machine]
    name = f'claude-agent-acp-linux-{arch}.tar.gz'
    asset, = [item for item in release['assets'] if item['name'] == name]
    url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    if asset['browser_download_url'] != url or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest', '')):
        raise ValueError('Invalid Claude ACP release asset metadata')
    return tag[1:], asset


def extract_binaries(archive, destination):
    with tarfile.open(archive, 'r:gz') as package:
        for name in ('claude-agent-acp', 'claude'):
            member, = [entry for entry in package.getmembers() if entry.name in (name, './' + name)]
            if not member.isfile():
                raise ValueError(f'Claude ACP archive {name} is not a regular file')
            target = destination / name
            with package.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755)


def binary_version(path):
    return subprocess.check_output([str(path), '--version'], text=True, timeout=30).strip()


def update(settings_path, install_root=INSTALL_ROOT):
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    if settings.get('providers_claude_code') is not True:
        print('Skipping Claude Code update: backend is disabled', flush=True)
        return
    if platform.system() != 'Linux':
        raise RuntimeError('The Claude Code updater requires Linux')
    install_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (install_root / '.update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with fetch(f'https://api.github.com/repos/{REPOSITORY}/releases/latest') as response:
            latest, asset = release_asset(json.load(response), platform.machine())
        current = install_root / 'current'
        if current.exists():
            installed = binary_version(current / 'claude-agent-acp')
            if not re.fullmatch(r'\d+\.\d+\.\d+', installed):
                raise ValueError('Unexpected installed Claude ACP version')
            if tuple(map(int, latest.split('.'))) < tuple(map(int, installed.split('.'))):
                raise ValueError('Refusing Claude ACP downgrade')
            if installed == latest:
                print(f'Claude Code ACP {latest} is current', flush=True)
                return
        versions = install_root / 'versions'
        versions.mkdir(mode=0o700, exist_ok=True)
        destination = versions / latest
        if destination.exists():
            raise FileExistsError(f'Claude ACP version directory already exists: {destination}')
        with tempfile.TemporaryDirectory(prefix='.update-', dir=install_root) as directory:
            staging = Path(directory)
            archive = staging / asset['name']
            print(f'Downloading Claude Code ACP {latest} with bundled Claude CLI', flush=True)
            with fetch(asset['browser_download_url']) as response, archive.open('xb') as output:
                shutil.copyfileobj(response, output)
            with archive.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if archive.stat().st_size != asset['size'] or 'sha256:' + digest != asset['digest']:
                raise ValueError('Claude ACP archive checksum or size mismatch')
            candidate = staging / 'release'
            candidate.mkdir(mode=0o700)
            extract_binaries(archive, candidate)
            if binary_version(candidate / 'claude-agent-acp') != latest:
                raise ValueError('Claude ACP candidate version does not match release')
            cli_version = binary_version(candidate / 'claude')
            if not re.fullmatch(r'\d+\.\d+\.\d+ \(Claude Code\)', cli_version):
                raise ValueError('Unexpected bundled Claude CLI version')
            (candidate / 'manifest.json').write_text(json.dumps({
                'adapter_version': latest, 'cli_version': cli_version,
                'archive_sha256': digest, 'asset': asset['name'],
            }, indent=2))
            candidate.rename(destination)
            replacement = staging / 'current'
            replacement.symlink_to(Path('versions') / latest)
            os.replace(replacement, current)
        stored = sorted((path for path in versions.iterdir() if re.fullmatch(r'\d+\.\d+\.\d+', path.name)),
                        key=lambda path: tuple(map(int, path.name.split('.'))), reverse=True)
        for obsolete in stored[3:]:
            shutil.rmtree(obsolete)
        print(f'Installed Claude Code ACP {latest}, {cli_version}', flush=True)
        print(f'Sign in: {current / "claude"} auth login', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', type=Path, default=Path(__file__).resolve().parents[1] / 'data/settings.json')
    parser.add_argument('--install-root', type=Path, default=INSTALL_ROOT)
    args = parser.parse_args()
    update(args.settings, args.install_root)
