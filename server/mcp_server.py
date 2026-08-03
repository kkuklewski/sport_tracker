#!/usr/bin/env python3
"""Remote MCP server exposing the training log to Claude.ai custom connectors.

Runs on the VPS inside Docker. Claude.ai custom connectors cannot send custom
headers, so authentication is an unguessable URL: the MCP endpoint is mounted
at /<MCP_PATH_SECRET>/mcp and everything else 404s. TLS is terminated by the
Coolify Traefik proxy in front of this container.

Environment:
    MCP_PATH_SECRET   required — random URL path segment (openssl rand -hex 16)
    DB_PATH           where activities.db lives (default: repo root)
    GARMINTOKENS      Garmin token cache dir (default: ~/.garminconnect)
    GARMIN_EMAIL / GARMIN_PASSWORD  only needed until a token is cached
    SYNC_INTERVAL_HOURS  background sync cadence (default 6, 0 disables)
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from db import ROOT, get_connection  # noqa: E402

from fastmcp import FastMCP  # noqa: E402

GOALS_PATH = ROOT / "GOALS.md"

mcp = FastMCP("sport-tracker")


def _parse_number(raw: str):
    """Garmin export strings -> numbers: '--' -> None, '1,563' -> 1563."""
    if raw is None or raw in ("--", ""):
        return None
    cleaned = raw.replace(",", "")
    try:
        value = float(cleaned)
        return int(value) if value.is_integer() else value
    except ValueError:
        return raw  # durations like "01:02:03" and paces stay as strings


NUMERIC_FIELDS = {
    "distance_km", "calories", "avg_hr", "max_hr", "aerobic_te",
    "avg_bike_cadence", "max_bike_cadence", "total_ascent", "total_descent",
    "avg_stride_length", "normalized_power", "training_stress_score",
    "avg_power", "max_power", "steps", "number_of_laps",
    "avg_resp", "min_resp", "max_resp", "min_elevation", "max_elevation",
}


@mcp.tool
def get_recent_activities(days: int = 14) -> list[dict]:
    """Recent training sessions from the activity log, newest first.

    Numeric fields are parsed (None where Garmin recorded no value); durations
    stay as HH:MM:SS strings and running/walking speeds as M:SS pace strings.
    """
    conn = get_connection()
    cur = conn.execute(
        "SELECT * FROM activities WHERE date >= date('now', ?) ORDER BY date DESC",
        (f"-{int(days)} days",),
    )
    columns = [c[0] for c in cur.description]
    rows = []
    for row in cur.fetchall():
        record = dict(zip(columns, row))
        for key in NUMERIC_FIELDS:
            record[key] = _parse_number(record.get(key))
        rows.append({k: v for k, v in record.items() if v not in (None, "--")})
    conn.close()
    return rows


@mcp.tool
def get_goals() -> str:
    """The user's training goals, target weekly structure, and constraints."""
    if not GOALS_PATH.exists():
        return "No GOALS.md found."
    return GOALS_PATH.read_text(encoding="utf-8")


@mcp.tool
def sync_now(days: int = 7) -> str:
    """Pull the latest activities from Garmin Connect into the database.

    Call this before analyzing recent training if freshness matters.
    """
    return _run_sync(days)


def _run_sync(days: int) -> str:
    from garmin_sync import sync

    try:
        added = sync(days=days, dry_run=False, interactive=False)
        return f"Sync complete: {added} new activity(ies) added."
    except SystemExit as exc:
        # garmin_sync raises SystemExit with a human-readable reason
        # (expired token, rate limit, unreachable). Never retry logins here —
        # Garmin IP-rate-limits its SSO endpoint.
        return f"Sync failed: {exc}"


def _background_sync() -> None:
    interval = float(os.environ.get("SYNC_INTERVAL_HOURS", "6"))
    if interval <= 0:
        return
    while True:
        print(f"[background-sync] {_run_sync(days=7)}", flush=True)
        time.sleep(interval * 3600)


if __name__ == "__main__":
    secret = os.environ.get("MCP_PATH_SECRET")
    if not secret:
        raise SystemExit("MCP_PATH_SECRET is required (openssl rand -hex 16)")

    threading.Thread(target=_background_sync, daemon=True).start()
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
        path=f"/{secret}/mcp",
    )
