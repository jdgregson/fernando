import importlib.util
import tempfile
import unittest
from pathlib import Path


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure_desktop_accessibility', root / 'scripts/configure-desktop-accessibility.py')
configuration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configuration)


class AccessibilityConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = {
            'usr/share/dbus-1/services/org.a11y.Bus.service': 'Exec=/usr/libexec/at-spi-bus-launcher\n',
            'etc/xdg/autostart/at-spi-dbus-bus.desktop': 'Exec=/usr/libexec/at-spi-bus-launcher --launch-immediately\n',
            'usr/lib/systemd/user/at-spi-dbus-bus.service': 'ExecStart=/usr/libexec/at-spi-bus-launcher\n',
        }
        for relative, content in self.paths.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def test_all_native_activation_paths_use_wrapper_and_preserve_arguments(self):
        configuration.configure(self.root)
        for relative, content in self.paths.items():
            self.assertEqual((self.root / relative).read_text(), content.replace('/usr/libexec/at-spi-bus-launcher', '/dockerstartup/desktop-atspi-launcher.sh'))

    def test_upstream_configuration_drift_fails_before_any_patch(self):
        changed = self.root / 'usr/lib/systemd/user/at-spi-dbus-bus.service'
        changed.write_text('ExecStart=/different/launcher\n')
        with self.assertRaises(RuntimeError):
            configuration.configure(self.root)
        for relative, content in self.paths.items():
            path = self.root / relative
            if path != changed:
                self.assertEqual(path.read_text(), content)


if __name__ == '__main__':
    unittest.main()
