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
from db import ROOT, get_connection, parse_number  # noqa: E402
from plan import (  # noqa: E402
    SESSION_TYPES,
    adherence,
    delete_session,
    get_plan_connection,
    plan_with_status,
    upsert_session,
)

from fastmcp import FastMCP  # noqa: E402

GOALS_PATH = ROOT / "GOALS.md"
COACHING_PATH = ROOT / "COACHING.md"

mcp = FastMCP(
    "sport-tracker",
    instructions=COACHING_PATH.read_text(encoding="utf-8")
    if COACHING_PATH.exists()
    else None,
)


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
            record[key] = parse_number(record.get(key))
        rows.append({k: v for k, v in record.items() if v not in (None, "--")})
    conn.close()
    return rows


@mcp.tool
def get_goals() -> str:
    """The user's training goals, target weekly structure, and constraints."""
    if not GOALS_PATH.exists():
        return "No GOALS.md found."
    return GOALS_PATH.read_text(encoding="utf-8")


def _weight_connection():
    """Same database file as activities; separate table for body weight."""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weight (
            date TEXT PRIMARY KEY,
            weight_kg REAL NOT NULL
        )
        """
    )
    return conn


@mcp.tool
def log_weight(weight_kg: float, date: str = "") -> str:
    """Record body weight in kg. Date is YYYY-MM-DD, defaulting to today.

    One entry per day — logging again the same day overwrites.
    """
    from datetime import date as _date

    if not 30 <= weight_kg <= 250:
        return f"Refusing implausible weight {weight_kg} kg — expected 30–250."
    day = date or _date.today().isoformat()
    conn = _weight_connection()
    conn.execute(
        "INSERT INTO weight (date, weight_kg) VALUES (?, ?) "
        "ON CONFLICT(date) DO UPDATE SET weight_kg = excluded.weight_kg",
        (day, weight_kg),
    )
    conn.commit()
    previous = conn.execute(
        "SELECT date, weight_kg FROM weight WHERE date < ? ORDER BY date DESC LIMIT 1",
        (day,),
    ).fetchone()
    conn.close()
    if previous:
        delta = weight_kg - previous[1]
        return f"Logged {weight_kg} kg on {day} ({delta:+.1f} kg since {previous[0]})."
    return f"Logged {weight_kg} kg on {day} (first entry)."


@mcp.tool
def get_weight_history(days: int = 90) -> list[dict]:
    """Body-weight entries for the last N days, newest first."""
    conn = _weight_connection()
    rows = conn.execute(
        "SELECT date, weight_kg FROM weight WHERE date >= date('now', ?) "
        "ORDER BY date DESC",
        (f"-{int(days)} days",),
    ).fetchall()
    conn.close()
    return [{"date": d, "weight_kg": w} for d, w in rows]


@mcp.tool
def get_coaching_procedure() -> str:
    """How to coach: the exact procedure and personal context for answering
    "what should I train" questions. Read this before recommending a session.
    """
    if not COACHING_PATH.exists():
        return "No COACHING.md found."
    return COACHING_PATH.read_text(encoding="utf-8")


@mcp.tool
def plan_sessions(sessions: list[dict]) -> str:
    """Write planned training sessions into the plan.

    Each session is a dict with:
        date                 required, YYYY-MM-DD
        session_type         required, one of cycling | running | mobility
        focus                optional, e.g. "long ride", "zone 2", "strength+mobility"
        target_distance_km   optional number
        target_duration_min  optional number
        intensity            optional, e.g. "easy", "zone2", "hard"
        notes                optional free text

    Planning the same date and session_type again overwrites that slot, so a
    week can be replanned without creating duplicates. Plan slots, not content:
    mobility is coached externally and running is by feel.
    """
    conn = get_plan_connection()
    cur = conn.cursor()
    try:
        for session in sessions:
            upsert_session(cur, session)
    except ValueError as exc:
        conn.close()
        return f"Nothing written: {exc}"
    conn.commit()
    conn.close()
    return f"{len(sessions)} planned session(s) written."


@mcp.tool
def get_plan(days_ahead: int = 14, days_back: int = 0) -> list[dict]:
    """Planned sessions, each tagged done / missed / pending.

    Defaults to the upcoming fortnight. Pass days_back to include recent past
    days — useful for seeing what was skipped before recommending what is next.
    A session counts as done when an activity of a matching type was recorded
    that day (mobility is logged by the watch as "Other").
    """
    from datetime import date as _date

    today = _date.today()
    start = _date.fromordinal(today.toordinal() - int(days_back))
    end = _date.fromordinal(today.toordinal() + int(days_ahead))
    conn = get_plan_connection()
    try:
        return plan_with_status(conn, start.isoformat(), end.isoformat())
    finally:
        conn.close()


@mcp.tool
def get_plan_adherence(days: int = 28) -> dict:
    """Plan vs actual over the last N days: counts, an adherence percentage,
    every planned session with its status, and sessions that happened off-plan.

    Pending (future or today) slots are excluded from the percentage. Read this
    before judging a training block — a low percentage with many unplanned
    sessions means the plan needs changing, not the athlete.
    """
    from datetime import date as _date

    today = _date.today()
    start = _date.fromordinal(today.toordinal() - int(days))
    conn = get_plan_connection()
    try:
        return adherence(conn, start.isoformat(), today.isoformat())
    finally:
        conn.close()


@mcp.tool
def cancel_planned_session(date: str, session_type: str) -> str:
    """Remove one planned session, identified by its date and session type.

    Use when a slot is dropped outright. To change a session instead, call
    plan_sessions again for the same date and type.
    """
    if session_type.lower().strip() not in SESSION_TYPES:
        return f"Unknown session_type {session_type!r}; expected one of {', '.join(SESSION_TYPES)}."
    conn = get_plan_connection()
    cur = conn.cursor()
    removed = delete_session(cur, date, session_type)
    conn.commit()
    conn.close()
    return (
        f"Removed the {session_type} session planned for {date}."
        if removed
        else f"No {session_type} session was planned for {date}."
    )


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
