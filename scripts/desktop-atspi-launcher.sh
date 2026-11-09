#!/bin/bash
set -euo pipefail
umask 077
XDG_CACHE_HOME="$(mktemp -d "/dev/shm/fernando-atspi-$(id -u)-XXXXXXXX")"
export XDG_CACHE_HOME
exec /usr/libexec/at-spi-bus-launcher "$@"
