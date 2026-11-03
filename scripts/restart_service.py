import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from src.config import get_config


def wait_web_ready():
    url = 'http://127.0.0.1:' + str(int(get_config('WEB_PORT', '5001'))) + '/api/web/ready'
    headers = {'X-API-Key': Path('/tmp/fernando-api-key').read_text().strip()}
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, headers=headers, timeout=2)
        except requests.ConnectionError:
            time.sleep(0.1)
            continue
        response.raise_for_status()
        if response.json().get('role') != 'web':
            raise RuntimeError('Unexpected web readiness response')
        return
    raise TimeoutError('Web process did not become ready within 30 seconds')


def notify_web_mutation(phase):
    url = 'http://127.0.0.1:' + str(int(get_config('FLASK_PORT', '5000'))) + '/api/mutating'
    headers = {'X-API-Key': Path('/tmp/fernando-api-key').read_text().strip()}
    response = requests.post(url, headers=headers, json={'mode': 'flask', 'phase': phase}, timeout=5)
    response.raise_for_status()
    if response.json().get('ok') is not True:
        raise RuntimeError('Runtime did not acknowledge web mutation')


def restart(mode, force):
    if mode == 'all':
        if not force:
            raise SystemExit('Full restart denied: mode=all requires force=true, even when agents are idle.')
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / 'scripts/restart_guard.py'), '--force'], check=True, capture_output=True, text=True, timeout=15)
        descriptor = os.open('/tmp/fernando-mutate.log', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as log:
            subprocess.Popen(
                ['bash', '-c', 'sleep 2; exec sudo -n /bin/systemctl restart fernando'],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        return {'status': 'restart_initiated', 'mode': mode}
    result = subprocess.run(
        ['systemctl', 'show', 'fernando-web.service', '--property=LoadState', '--value'],
        check=True, capture_output=True, text=True, timeout=5,
    )
    if result.stdout.strip() != 'loaded':
        raise SystemExit('Flask-only restart unavailable: the separate runtime services have not been installed. No services were restarted.')
    notify_web_mutation('starting')
    try:
        subprocess.run(['sudo', '-n', '/bin/systemctl', 'restart', 'fernando-web'], check=True, timeout=45)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'fernando-web.service'], check=True, timeout=5)
        wait_web_ready()
    except (subprocess.SubprocessError, requests.RequestException, TimeoutError, RuntimeError):
        notify_web_mutation('failed')
        raise
    notify_web_mutation('ready')
    return {'status': 'restarted', 'mode': mode, 'message': 'Web process restarted. Agent runtime, MCP servers, terminals, desktop, and Jupyter were not restarted.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['flask', 'all'], default='flask')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--check-ready', action='store_true')
    args = parser.parse_args()
    if args.check_ready:
        wait_web_ready()
        print(json.dumps({'status': 'ready'}))
        return
    print(json.dumps(restart(args.mode, args.force)))


if __name__ == '__main__':
    main()
