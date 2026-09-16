#!/bin/sh
# Nightly job: mirror the VPS database (which syncs Garmin itself), then
# reconcile Apple Reminders with the plan. Order matters — the fresh mirror is
# what marks a planned slot done, and the reminders pass is what ticks the
# matching reminder off on the phone.
#
# Install as a launchd agent (see com.easecrafted.sport-tracker.plist):
#     cp scripts/com.easecrafted.sport-tracker.plist ~/Library/LaunchAgents/
#     launchctl load ~/Library/LaunchAgents/com.easecrafted.sport-tracker.plist
set -u
cd "$(dirname "$0")/.." || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
# A failed pull must not stop the reminders pass: yesterday's plan still needs
# pushing from the last mirror even when the VPS is unreachable.
.venv/bin/python scripts/pull_db.py || echo "pull from VPS failed, continuing"
.venv/bin/python scripts/reminders.py
.venv/bin/python scripts/advise.py || true
