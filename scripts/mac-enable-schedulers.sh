#!/bin/bash
# One-time macOS setup to make cron and at daemons functional.
# Requires sudo. Idempotent.
set -e

if [ "$(uname)" != "Darwin" ]; then
    echo "This script is only needed on macOS."
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-running with sudo..."
    exec sudo "$0" "$@"
fi

ATRUN_PLIST="/System/Library/LaunchDaemons/com.apple.atrun.plist"
if [ -f "$ATRUN_PLIST" ]; then
    echo "Enabling atrun daemon..."
    launchctl load -w "$ATRUN_PLIST" 2>/dev/null || echo "  (already loaded)"
else
    echo "WARNING: $ATRUN_PLIST not found. 'at' scheduling will not fire."
fi

echo ""
echo "cron is managed by launchd and starts automatically when a crontab exists."
echo ""
echo "IMPORTANT: macOS requires Full Disk Access for cron jobs to run."
echo "  1. Open System Settings -> Privacy & Security -> Full Disk Access"
echo "  2. Click '+' and add /usr/sbin/cron"
echo "     (Cmd+Shift+G in the file picker, then type /usr/sbin/cron)"
echo "  3. Ensure the toggle next to 'cron' is ON"
echo ""
echo "Without this, 'crontab' will accept jobs but they will never run."
