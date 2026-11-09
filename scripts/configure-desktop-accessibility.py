from pathlib import Path


def configure(root=Path('/')):
    launcher = '/usr/libexec/at-spi-bus-launcher'
    wrapper = '/dockerstartup/desktop-atspi-launcher.sh'
    paths = [
        'usr/share/dbus-1/services/org.a11y.Bus.service',
        'etc/xdg/autostart/at-spi-dbus-bus.desktop',
        'usr/lib/systemd/user/at-spi-dbus-bus.service',
    ]
    replacements = []
    for relative in paths:
        path = root / relative
        text = path.read_text()
        if text.count(launcher) != 1:
            raise RuntimeError(f'Unexpected AT-SPI activation configuration: {path}')
        replacements.append((path, text.replace(launcher, wrapper)))
    for path, text in replacements:
        path.write_text(text)


if __name__ == '__main__':
    configure()
