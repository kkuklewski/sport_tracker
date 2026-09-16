#!/usr/bin/env python3
"""Mirror the VPS database onto this Mac.

The VPS is the canonical store: its container syncs Garmin every few hours and
holds every plan written through the MCP connector. This Mac only needs a copy,
because it is the one machine that can write Apple Reminders. The pull first
runs a sync inside the container so the mirror reflects today's session, not
the last background cycle.

Usage:
    python3 scripts/pull_db.py            # sync Garmin on the VPS, then pull
    python3 scripts/pull_db.py --no-sync  # just pull
"""
import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import DB_PATH

VPS_HOST = "vps-jarvis"
APP_UUID = "v4wtqyksex3rvjtudqyu9l7h"
REMOTE_DB = "/data/activities.db"

# Coolify renames the container on every redeploy, so it is resolved on the
# VPS each time rather than pinned here.
_IN_CONTAINER = (
    f"N=$(docker ps --filter name={APP_UUID} --format '{{{{.Names}}}}' | head -1); "
    "[ -n \"$N\" ] || { echo 'sport-tracker container not running' >&2; exit 1; }; "
    "docker exec $N "
)


def _remote(command: str, capture_binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", VPS_HOST,
         _IN_CONTAINER + command],
        capture_output=True,
        text=not capture_binary,
    )


def sync_remote(days: int) -> None:
    for script in ("garmin_sync.py", "wellness.py"):
        result = _remote(f"python scripts/{script} --days {days}")
        if result.returncode != 0:
            print(f"{script} on VPS failed, continuing: {result.stderr.strip()}")
        elif result.stdout.strip():
            print(result.stdout.strip())


def pull() -> dict:
    result = _remote(
        'python -c "import sqlite3, sys; '
        f"sys.stdout.buffer.write(sqlite3.connect('{REMOTE_DB}').serialize())\"",
        capture_binary=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"Pull failed: {result.stderr.decode().strip()}")

    tmp = DB_PATH.with_name(DB_PATH.name + ".tmp")
    tmp.write_bytes(result.stdout)
    try:
        conn = sqlite3.connect(tmp)
        activities, latest = conn.execute(
            "SELECT count(*), max(date) FROM activities"
        ).fetchone()
        planned = conn.execute("SELECT count(*) FROM planned_sessions").fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Pulled file is not a usable database, keeping the old one: {exc}")

    os.replace(tmp, DB_PATH)
    return {"activities": activities, "latest": latest, "planned": planned}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="how far back the VPS sync looks (default 7)")
    parser.add_argument("--no-sync", action="store_true",
                        help="skip the Garmin sync on the VPS, just copy the database")
    args = parser.parse_args()

    if not args.no_sync:
        sync_remote(args.days)
    summary = pull()
    print(
        f"Mirrored {DB_PATH.name}: {summary['activities']} activities "
        f"(latest {summary['latest']}), {summary['planned']} planned session(s)."
    )
