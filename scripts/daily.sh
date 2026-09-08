#!/bin/sh
# Nightly job: refresh the log from Garmin, then reconcile Apple Reminders with
# the plan. Order matters — the sync is what marks a planned slot done, and the
# reminders pass is what ticks the matching reminder off on the phone.
#
# Install as a launchd agent (see com.easecrafted.sport-tracker.plist):
#     cp scripts/com.easecrafted.sport-tracker.plist ~/Library/LaunchAgents/
#     launchctl load ~/Library/LaunchAgents/com.easecrafted.sport-tracker.plist
set -u
cd "$(dirname "$0")/.." || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
# A failed sync must not stop the reminders pass: yesterday's plan still needs
# pushing even when Garmin is unreachable or the cached token has expired.
.venv/bin/python scripts/garmin_sync.py --days 7 || echo "sync failed, continuing"
.venv/bin/python scripts/reminders.py
