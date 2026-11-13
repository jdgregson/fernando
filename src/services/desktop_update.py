from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import psutil
import requests


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'data/desktop-update'
CALLBACK_FILE = Path('/tmp/fernando-desktop-callback.conf')
PASSWORD_FILE = Path('/tmp/fernando-vnc-password')
ACTIVE = frozenset(('queued', 'stopping', 'building', 'starting', 'checking'))


def write_private(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lock(name):
    DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    DIRECTORY.chmod(0o700)
    with (DIRECTORY / name).open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def read_state():
    path = DIRECTORY / 'state.json'
    if not path.exists():
        return {'phase': 'stopped', 'message': 'Desktop has not started.'}
    return json.loads(path.read_text())


def worker_process(state):
    pid = state.get('pid')
    if not pid or not psutil.pid_exists(pid):
        return None
    try:
        process = psutil.Process(pid)
        if process.create_time() == state.get('process_started') and process.status() != psutil.STATUS_ZOMBIE:
            return process
    except psutil.NoSuchProcess:
        return None
    return None


def status(include_log=True):
    state = read_state()
    if state['phase'] in ACTIVE and worker_process(state) is None:
        state = {**state, 'phase': 'failed', 'message': 'The desktop update worker stopped before completing. Retry the update.'}
    result = {key: state[key] for key in ('id', 'phase', 'message', 'started', 'updated') if key in state}
    result['ready'] = state['phase'] == 'ready'
    result['log'] = ''
    path = DIRECTORY / 'build.log'
    if include_log and path.exists():
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 32768))
            result['log'] = stream.read().decode('utf-8', errors='replace')
    return result


def require_ready():
    current = status(include_log=False)
    if not current['ready']:
        raise RuntimeError(f"Desktop unavailable ({current['phase']}): {current['message']} Open Desktop for progress and retry controls.")


def change(generation, phase, message):
    with lock('state.lock'):
        state = read_state()
        if state.get('id') != generation:
            raise InterruptedError('Desktop update was superseded')
        state.update(phase=phase, message=message, updated=time.time())
        write_private(DIRECTORY / 'state.json', json.dumps(state))


def cancel_locked():
    state = read_state()
    process = worker_process(state)
    if process is not None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=15)
        except psutil.TimeoutExpired:
            if worker_process(state) is not None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    if state.get('phase') in ACTIVE:
        change(state['id'], 'stopped', 'Desktop update stopped.')


def cancel():
    with lock('manager.lock'):
        cancel_locked()


def stop():
    with lock('manager.lock'):
        cancel_locked()
        with lock('state.lock'):
            state = read_state()
            state.update(phase='stopped', message='Desktop stopped.', updated=time.time())
            write_private(DIRECTORY / 'state.json', json.dumps(state))


def prepare(nginx_port, flask_port):
    stop()
    write_private(CALLBACK_FILE, '')
    template = ROOT / 'nginx.desktop-callback.conf.template'
    content = '' if sys.platform == 'darwin' else template.read_text().replace('{{NGINX_PORT}}', str(nginx_port)).replace('{{FLASK_PORT}}', str(flask_port))
    write_private(DIRECTORY / 'callback-ready.conf', content)


def launch(action='update', replace=False, no_cache=False):
    with lock('manager.lock'):
        previous = read_state()
        if worker_process(previous) is not None and previous.get('phase') in ACTIVE:
            if not replace:
                raise ValueError('A desktop update is already running.')
            cancel_locked()
        if action == 'restart' and previous.get('phase') != 'ready':
            action = 'update'
        generation = uuid.uuid4().hex
        compose = os.environ.get('FERNANDO_COMPOSE_FILE') or previous.get('compose_file') or 'docker-compose.yml'
        nginx = os.environ.get('FERNANDO_NGINX_BIN') or previous.get('nginx_bin') or shutil.which('nginx', path=os.environ.get('PATH', '') + ':/usr/sbin')
        if not nginx:
            raise ValueError('nginx executable was not found')
        now = time.time()
        with lock('state.lock'):
            write_private(DIRECTORY / 'build.log', '')
            with (DIRECTORY / 'worker.log').open('w') as output:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / 'scripts/desktop-update.py'), 'run', generation],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            state = {
                'id': generation, 'phase': 'queued', 'message': 'Desktop update queued.',
                'started': now, 'updated': now, 'pid': process.pid,
                'process_started': psutil.Process(process.pid).create_time(),
                'compose_file': compose, 'nginx_bin': nginx, 'action': action, 'no_cache': no_cache,
            }
            write_private(DIRECTORY / 'state.json', json.dumps(state))
        threading.Thread(target=process.wait, daemon=True).start()
        return status(include_log=False)


def append_log(content):
    path = DIRECTORY / 'build.log'
    if path.stat().st_size > 2 * 1024 * 1024:
        with path.open('rb') as stream:
            stream.seek(-1024 * 1024, os.SEEK_END)
            retained = stream.read().decode('utf-8', errors='replace')
        write_private(path, retained)
    with path.open('ab') as stream:
        stream.write(content)


def execute(command, env, timeout, check=True):
    import selectors
    append_log(('\n$ ' + ' '.join(str(part) for part in command) + '\n').encode())
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Desktop command exceeded its deadline')
                if selector.select(min(0.5, max(0, deadline - time.monotonic()))):
                    content = os.read(process.stdout.fileno(), 65536)
                    if not content:
                        break
                    append_log(content)
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code and check:
                raise RuntimeError(f'Desktop command exited with status {code}. See the build log.')
            return code
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()


def wait_ready(env, timeout=180):
    deadline = time.monotonic() + timeout
    with requests.Session() as client:
        client.trust_env = False
        while time.monotonic() < deadline:
            try:
                response = client.get('https://127.0.0.1:6901/', auth=('kasm_user', env['VNC_PW']), verify=False, timeout=3, allow_redirects=False, stream=True)
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(1)
                continue
            code = response.status_code
            response.close()
            if code == 200:
                code = execute(['docker', 'exec', '--user', '1000:1000', '-e', 'DISPLAY=:1', 'fernando-desktop', 'xdpyinfo'], env, 15, check=False)
                if code == 0:
                    return
            time.sleep(1)
    raise TimeoutError('The desktop did not pass its authenticated readiness check within 180 seconds.')


def run(generation):
    def interrupted(signum, frame):
        raise InterruptedError('Desktop update stopped.')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with lock('worker.lock'):
        with lock('state.lock'):
            state = read_state()
            if state.get('id') != generation:
                return
        compose = ['bash', str(ROOT / 'scripts/desktop-compose.sh')]
        try:
            env = {**os.environ, 'FERNANDO_COMPOSE_FILE': state['compose_file'], 'VNC_PW': PASSWORD_FILE.read_text().strip()}
            if state['action'] == 'update':
                change(generation, 'stopping', 'Stopping the desktop before updating.')
                execute(compose + ['stop', '--timeout', '20', 'fernando-desktop'], env, 60)
                change(generation, 'building', 'Updating the desktop image. Chats and other services remain available.')
                build = ['--progress', 'plain', 'build', '--pull']
                if state.get('no_cache'):
                    build.append('--no-cache')
                execute(compose + build + ['fernando-desktop'], env, 3600)
                change(generation, 'starting', 'Starting the updated desktop.')
                execute(compose + ['up', '-d', '--no-build', '--force-recreate', '--no-deps', 'fernando-desktop'], env, 180)
            else:
                change(generation, 'starting', 'Restarting the desktop.')
                execute(['docker', 'restart', '--time', '20', 'fernando-desktop'], env, 60)
            change(generation, 'checking', 'Waiting for the desktop to become ready.')
            wait_ready(env)
            callback = DIRECTORY / 'callback-ready.conf'
            if callback.exists() and callback.read_text():
                write_private(CALLBACK_FILE, callback.read_text())
                execute([state['nginx_bin'], '-t', '-c', str(ROOT / 'nginx.conf')], env, 15)
                execute([state['nginx_bin'], '-c', str(ROOT / 'nginx.conf'), '-s', 'reload'], env, 15)
            change(generation, 'ready', 'Desktop ready.')
        except InterruptedError as error:
            change(generation, 'stopped', str(error))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            append_log(('\nERROR: ' + str(error) + '\n').encode())
            change(generation, 'failed', str(error))
            raise
