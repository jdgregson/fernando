import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path('scripts') / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shares = load('desktop_shares', 'desktop-shares.py')
access = load('desktop_access', 'desktop-shared-access.py')


class DesktopSharesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.repo = self.home / 'fernando'
        (self.repo / 'data' / 'desktop').mkdir(parents=True)

    def test_existing_native_data_and_ownership_are_untouched(self):
        paths = {name: self.home / name for name in shares.NAMES}
        for path in paths.values():
            path.mkdir()
            (path / 'personal.txt').write_text('existing user data')
        before = {name: (path.stat().st_ino, path.stat().st_uid, path.stat().st_mode) for name, path in paths.items()}
        config = shares.prepare(self.home, self.repo, paths, create=True)
        shares.migrate(self.home, self.repo, paths)
        self.assertEqual(before, {name: (path.stat().st_ino, path.stat().st_uid, path.stat().st_mode) for name, path in paths.items()})
        for path in paths.values():
            self.assertEqual((path / 'personal.txt').read_text(), 'existing user data')
        volumes = config['services']['fernando-desktop']['volumes']
        self.assertEqual({item['source'] for item in volumes}, {str(path) for path in paths.values()})
        self.assertTrue(all(item['bind']['create_host_path'] is False for item in volumes))

    def test_legacy_migration_moves_data_without_copying_or_deleting(self):
        paths = {name: self.home / name for name in shares.NAMES}
        inodes = {}
        for name, path in paths.items():
            old = self.repo / 'data' / 'desktop' / name
            old.mkdir()
            (old / 'keep.txt').write_text(name)
            inodes[name] = (old / 'keep.txt').stat().st_ino
            path.symlink_to(old)
        with self.assertRaises(RuntimeError):
            shares.prepare(self.home, self.repo, paths, create=True)
        shares.migrate(self.home, self.repo, paths)
        shares.migrate(self.home, self.repo, paths)
        for name, path in paths.items():
            self.assertFalse(path.is_symlink())
            self.assertEqual((path / 'keep.txt').stat().st_ino, inodes[name])
            self.assertEqual((path / 'keep.txt').read_text(), name)
            self.assertTrue((self.home / ('.' + name + '.fernando-legacy-link')).is_symlink())
        shares.prepare(self.home, self.repo, paths)

    def test_xdg_paths_and_user_owned_symlinks_are_respected(self):
        config = self.home / '.config'
        config.mkdir()
        (config / 'user-dirs.dirs').write_text('XDG_DOCUMENTS_DIR="$HOME/My Documents"\nXDG_DOWNLOAD_DIR="$HOME/Incoming"\n')
        paths = shares.configured_paths(self.home, self.repo, {})
        self.assertEqual(paths['Documents'], self.home / 'My Documents')
        incoming = self.root / 'external-incoming'
        incoming.mkdir()
        paths['Downloads'].symlink_to(incoming)
        paths['Documents'].mkdir()
        result = shares.prepare(self.home, self.repo, paths)
        self.assertTrue(paths['Downloads'].is_symlink())
        self.assertIn(str(incoming), [item['source'] for item in result['services']['fernando-desktop']['volumes']])
        with self.assertRaises(ValueError):
            shares.prepare(self.home, self.repo, {'Documents': self.home})

    def test_acl_grants_preserve_other_principals_effective_permissions(self):
        original = 'user::rwx\nuser:2000:rwx\ngroup::rwx\ngroup:3000:rwx\nmask::r--\nother::---\n'
        result = access.shared_acl(original, (1000, 1001), True, True)
        for line in ('user:2000:r--', 'group::r--', 'group:3000:r--', 'other::---', 'user:1000:rwx', 'user:1001:rwx', 'default:user:1000:rwx', 'default:user:1001:rwx', 'default:group::r--'):
            self.assertIn(line + '\n', result)
        self.assertEqual(result, access.shared_acl(result, (1000, 1001), True, True))

    def test_default_profile_never_overwrites_shared_or_existing_data(self):
        profile = self.root / 'profile'
        profile.mkdir()
        for name in ('Documents', 'Downloads', 'Desktop'):
            (profile / name).mkdir()
            (profile / name / 'default.txt').write_text('default')
            (self.home / name).mkdir()
            (self.home / name / 'keep.txt').write_text('personal')
        (profile / '.bashrc').write_text('default shell')
        subprocess.run([sys.executable, 'scripts/copy-desktop-profile.py', str(profile), str(self.home)], check=True)
        self.assertEqual((self.home / '.bashrc').read_text(), 'default shell')
        for name in ('Documents', 'Downloads', 'Desktop'):
            self.assertEqual((self.home / name / 'keep.txt').read_text(), 'personal')
            self.assertFalse((self.home / name / 'default.txt').exists())


if __name__ == '__main__':
    unittest.main()
