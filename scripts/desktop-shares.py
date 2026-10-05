import argparse
import json
import os
import platform
import shlex
import subprocess
from pathlib import Path


NAMES = {'Documents': 'XDG_DOCUMENTS_DIR', 'Downloads': 'XDG_DOWNLOAD_DIR'}


def configured_paths(home, repo, environ):
    paths = {name: home / name for name in NAMES}
    xdg = Path(environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'user-dirs.dirs'
    if xdg.exists():
        for line in xdg.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() in NAMES.values():
                tokens = shlex.split(value, comments=True)
                if len(tokens) != 1:
                    raise ValueError('Invalid XDG directory: ' + key)
                expanded = tokens[0].replace('${HOME}', str(home)).replace('$HOME', str(home))
                paths[next(name for name, variable in NAMES.items() if variable == key.strip())] = Path(expanded)
    config = repo / 'data' / 'shared-directories.json'
    if config.exists():
        values = json.loads(config.read_text())
        if set(values) - set(NAMES):
            raise ValueError('Shared directory configuration accepts Documents and Downloads only')
        paths.update({name: Path(value) for name, value in values.items()})
    for name in NAMES:
        variable = 'FERNANDO_' + name.upper() + '_DIR'
        if variable in environ:
            paths[name] = Path(environ[variable])
        if not paths[name].is_absolute():
            raise ValueError(name + ' must be an absolute host path')
    return paths


def prepare(home, repo, paths, create=False):
    volumes = []
    for name, path in paths.items():
        legacy = repo / 'data' / 'desktop' / name
        if path.is_symlink() and path.resolve() == legacy.resolve():
            raise RuntimeError(f'{path} uses the legacy desktop symlink. Stop the desktop and run desktop-shares.py migrate explicitly.')
        if create:
            path.mkdir(exist_ok=True)
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(str(path) + ' is not a directory')
        if resolved in (home, *home.parents):
            raise ValueError('Refusing to expose a whole home directory or its ancestors as ' + name)
        volumes.append({'type': 'bind', 'source': str(resolved).replace('$', '$$'), 'target': '/home/kasm-user/' + name, 'bind': {'create_host_path': False}})
    return {'services': {'fernando-desktop': {'environment': {'FERNANDO_HOST_UID': str(os.getuid()), 'FERNANDO_HOST_SYSTEM': platform.system()}, 'volumes': volumes}}}


def migrate(home, repo, paths):
    plans = []
    for name, path in paths.items():
        legacy = repo / 'data' / 'desktop' / name
        backup = path.with_name('.' + path.name + '.fernando-legacy-link')
        link = backup if not os.path.lexists(path) and backup.is_symlink() else path
        if not link.is_symlink() or link.resolve() != legacy.resolve():
            continue
        if not legacy.is_dir() or legacy.is_symlink():
            raise RuntimeError('Expected a real legacy directory: ' + str(legacy))
        if link == path and os.path.lexists(backup):
            raise FileExistsError('Migration backup already exists: ' + str(backup))
        if legacy.stat().st_dev != path.parent.stat().st_dev:
            raise RuntimeError('Migration crosses filesystems; an explicit copy-and-verify migration is required: ' + str(path))
        if not os.access(legacy, os.W_OK | os.X_OK) or not os.access(legacy.parent, os.W_OK | os.X_OK):
            raise PermissionError('Grant the host user access to the legacy directory before migration: ' + str(legacy))
        plans.append((path, legacy, backup, link))
    for path, legacy, backup, link in plans:
        if link == path:
            path.rename(backup)
        legacy.rename(path)
        print('Migrated to host directory:', path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'create', 'migrate'))
    args = parser.parse_args()
    home = Path.home().resolve()
    repo = Path(__file__).resolve().parent.parent
    paths = configured_paths(home, repo, os.environ)
    if args.action == 'migrate':
        result = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', 'fernando-desktop'], check=True, capture_output=True, text=True, timeout=15)
        if result.stdout.strip() != 'false':
            raise RuntimeError('Stop fernando-desktop before migrating its shared directories')
        migrate(home, repo, paths)
    configuration = prepare(home, repo, paths, create=args.action == 'create')
    output = repo / 'data' / 'shared-directories.compose.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(configuration, indent=2) + '\n')
    temporary.replace(output)


if __name__ == '__main__':
    main()
