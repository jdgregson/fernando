import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]


def unit_files(root, username, home):
    root = str(root)
    if any(character in root + username + home for character in '\n\r%"\\'):
        raise ValueError('Unsupported service path or username')
    common = f'User={username}\nWorkingDirectory={root}\nEnvironment="HOME={home}"\nEnvironment="PATH={home}/.local/bin:{home}/.opencode/bin:/usr/local/bin:/usr/bin:/bin"\n'
    return {
        'fernando.service': f'''[Unit]
Description=Fernando infrastructure
After=network-online.target docker.service
Requires=docker.service
Wants=network-online.target fernando-runtime.service fernando-web.service

[Service]
Type=oneshot
RemainAfterExit=yes
{common}ExecStart=/bin/bash "{root}/scripts/start.sh" --infrastructure
ExecStop=/bin/bash "{root}/scripts/stop.sh"
TimeoutStartSec=300
TimeoutStopSec=60
KillMode=control-group

[Install]
WantedBy=multi-user.target
''',
        'fernando-runtime.service': f'''[Unit]
Description=Fernando persistent session runtime
Requires=fernando.service
After=fernando.service
PartOf=fernando.service

[Service]
Type=simple
{common}ExecStart="{root}/venv/bin/python" -u "{root}/run_runtime.py"
Environment=FERNANDO_LOG_FILE=/tmp/fernando-flask.log
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
KillMode=mixed
StandardOutput=journal
StandardError=journal
UMask=0077
''',
        'fernando-web.service': f'''[Unit]
Description=Fernando web interface and stateless HTTP backend
Requires=fernando-runtime.service
After=fernando-runtime.service
PartOf=fernando.service

[Service]
Type=simple
{common}ExecStart="{root}/venv/bin/python" -u "{root}/run_web.py"
Environment=FERNANDO_LOG_FILE=/tmp/fernando-flask.log
Restart=on-failure
RestartSec=2
TimeoutStopSec=15
KillMode=control-group
StandardOutput=journal
StandardError=journal
UMask=0077
''',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if args.activate and (not args.force or os.geteuid() != 0):
        raise SystemExit('Activation requires root and --force. It stops the existing service and its agents.')
    if args.install and os.geteuid() != 0:
        raise SystemExit('Installation requires root.')
    owner = pwd.getpwuid(ROOT.stat().st_uid)
    args.output.mkdir(parents=True, exist_ok=True)
    files = unit_files(ROOT, owner.pw_name, owner.pw_dir)
    for name, content in files.items():
        (args.output / name).write_text(content)
    sudoers = f'{owner.pw_name} ALL=(root) NOPASSWD: /bin/systemctl restart fernando-web\n'
    (args.output / 'fernando-web.sudoers').write_text(sudoers)
    subprocess.run(['systemd-analyze', 'verify', *[str(args.output / name) for name in files]], check=True, timeout=30)
    if not args.activate and not args.install:
        print(f'Validated staged units in {args.output}. Live services were not changed.')
        return
    subprocess.run(['visudo', '-cf', str(args.output / 'fernando-web.sudoers')], check=True, timeout=10)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'fernando.service'], timeout=10)
    if active.returncode == 0 and not args.activate:
        raise SystemExit('Fernando is running. Use --activate --force for the explicit full-restart migration.')
    backup = Path('/var/backups/fernando-services') / time.strftime('%Y%m%dT%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    for name in files:
        source = Path('/etc/systemd/system') / name
        if source.exists():
            shutil.copy2(source, backup / name)
    if args.activate:
        subprocess.run(['systemctl', 'stop', 'fernando'], check=True, timeout=90)
    for name in files:
        destination = Path('/etc/systemd/system') / name
        shutil.copyfile(args.output / name, destination)
        destination.chmod(0o644)
    destination = Path('/etc/sudoers.d/99-fernando-web-restart')
    shutil.copyfile(args.output / 'fernando-web.sudoers', destination)
    destination.chmod(0o440)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
    subprocess.run(['systemctl', 'enable', 'fernando.service'], check=True, timeout=30)
    if args.activate:
        subprocess.run(['systemctl', 'start', 'fernando.service', 'fernando-runtime.service', 'fernando-web.service'], check=True, timeout=360)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'fernando-runtime.service', 'fernando-web.service'], check=True, timeout=10)
        subprocess.run([str(ROOT / 'venv/bin/python'), str(ROOT / 'scripts/restart_service.py'), '--check-ready'], check=True, timeout=40)
    print(f'Installed split services. Previous units saved in {backup}.')


if __name__ == '__main__':
    main()
