import json
import os
import pwd
import subprocess
from pathlib import Path


def shared_acl(text, uids, executable, directory):
    sections = {'access': {}, 'default': {}}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        parts = line.split('#', 1)[0].strip().split(':')
        scope = 'default' if parts[0] == 'default' else 'access'
        if scope == 'default':
            parts = parts[1:]
        kind, identity, permissions = parts
        sections[scope][(kind, identity)] = set(permissions) - {'-'}
    if directory and not sections['default']:
        sections['default'] = {key: value.copy() for key, value in sections['access'].items() if key in (('user', ''), ('group', ''), ('other', ''))}
        if ('mask', '') in sections['access']:
            sections['default'][('group', '')] &= sections['access'][('mask', '')]
    output = []
    for scope, entries in sections.items():
        if not entries:
            continue
        mask = entries.get(('mask', ''))
        if mask is not None:
            for (kind, identity), permissions in entries.items():
                if kind == 'group' or (kind == 'user' and identity):
                    permissions &= mask
        granted = set('rwx' if scope == 'default' or executable else 'rw')
        for uid in uids:
            entries[('user', str(uid))] = granted.copy()
        entries[('mask', '')] = set().union(*(permissions for (kind, identity), permissions in entries.items() if kind == 'group' or (kind == 'user' and identity)))
        for (kind, identity), permissions in entries.items():
            prefix = 'default:' if scope == 'default' else ''
            output.append(prefix + kind + ':' + identity + ':' + ''.join(character if character in permissions else '-' for character in 'rwx'))
    return '\n'.join(output) + '\n'


def grant(path, uids):
    info = path.stat()
    original = subprocess.run(['getfacl', '-cpnE', '--', str(path)], check=True, capture_output=True, text=True).stdout
    updated = shared_acl(original, uids, path.is_dir() or bool(info.st_mode & 0o111), path.is_dir())
    subprocess.run(['setfacl', '--set-file=-', '--', str(path)], input=updated, text=True, check=True)


def walk_error(error):
    raise error


def main():
    profile = Path('/home/kasm-user')
    user = pwd.getpwnam('kasm-user')
    host_uid = int(os.environ['FERNANDO_HOST_UID'])
    host_system = os.environ['FERNANDO_HOST_SYSTEM']
    if host_uid < 0 or host_system not in ('Linux', 'Darwin'):
        raise ValueError('Unsupported shared-directory identity or platform')
    mounts = {line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    folders = [profile / name for name in ('Documents', 'Downloads')]
    if any(str(path) not in mounts for path in folders):
        raise RuntimeError('Documents and Downloads must be explicit host bind mounts; use scripts/desktop-compose.sh')
    os.chown(profile, user.pw_uid, user.pw_gid)
    marker = profile / '.fernando-shared-access.json'
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    current = {}
    for folder in folders:
        info = folder.stat()
        identity = [host_uid, user.pw_uid, info.st_dev, info.st_ino]
        current[folder.name] = identity
        if host_system == 'Linux':
            if previous.get(folder.name) != identity:
                for directory, children, files in os.walk(folder, followlinks=False, onerror=walk_error):
                    children[:] = [name for name in children if not (Path(directory) / name).is_symlink()]
                    grant(Path(directory), (host_uid, user.pw_uid))
                    for name in files:
                        path = Path(directory) / name
                        if not path.is_symlink() and path.is_file():
                            grant(path, (host_uid, user.pw_uid))
            else:
                grant(folder, (host_uid, user.pw_uid))
        for permission in ('-r', '-w', '-x'):
            subprocess.run(['runuser', '-u', 'kasm-user', '--', 'test', permission, str(folder)], check=True)
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps(current) + '\n')
    temporary.replace(marker)


if __name__ == '__main__':
    main()
