#!/usr/bin/env python3
"""Planned training sessions, and how they compare to what was actually ridden.

activities.db records what happened; this module records what was *supposed* to
happen, in the same database. Without it a training block can quietly evaporate
— the Aug 10 - Sep 2 2026 gap before the September tour was invisible to the
coaching agent, because nothing anywhere said a session had been intended.

A plan entry is deliberately coarse: a day, a session type, and a target. It is
a slot to fill, not a prescription — mobility content is coached externally and
running is by feel (see COACHING.md).

Usage:
    python3 scripts/plan.py                 # upcoming plan + status
    python3 scripts/plan.py --adherence 28  # last 28 days, plan vs actual
"""
import argparse
import sys
from datetime import date as _date
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection, parse_number

# The three session types in scope, and the activities.db activity_type values
# that count as having completed one. Mobility is logged by the watch as
# "Other"; Walking deliberately matches nothing, so a walk never marks a
# planned session done.
SESSION_TYPES = ("cycling", "running", "mobility")
COMPLETED_BY = {
    "cycling": ("Cycling",),
    "running": ("Running",),
    "mobility": ("Other",),
}

PLAN_COLUMNS = (
    "date", "session_type", "focus", "target_distance_km",
    "target_duration_min", "intensity", "notes", "created_at", "reminder_id",
)


def get_plan_connection():
    """Connection with both activities and planned_sessions guaranteed to exist."""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS planned_sessions (
            date TEXT NOT NULL,
            session_type TEXT NOT NULL,
            focus TEXT,
            target_distance_km REAL,
            target_duration_min INTEGER,
            intensity TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            reminder_id TEXT,
            PRIMARY KEY (date, session_type)
        )
        """
    )
    # Databases created before the Apple Reminders bridge existed predate the
    # reminder_id column; add it in place rather than asking for a migration.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(planned_sessions)")}
    if "reminder_id" not in columns:
        conn.execute("ALTER TABLE planned_sessions ADD COLUMN reminder_id TEXT")
    # Deleting a plan row would otherwise strand its reminder on the phone with
    # nothing left pointing at it, so the id is parked here until the next
    # reminders sync can delete it for real.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS retired_reminders (
            reminder_id TEXT PRIMARY KEY
        )
        """
    )
    return conn


def upsert_session(cur, session: dict) -> None:
    """Write one planned session. Re-planning the same day+type overwrites it.

    Keyed on (date, session_type) so re-running a week's plan is idempotent the
    same way the two ingest paths are — you can replan freely without piling up
    duplicate slots.
    """
    session_type = str(session.get("session_type", "")).lower().strip()
    if session_type not in SESSION_TYPES:
        raise ValueError(
            f"Unknown session_type {session.get('session_type')!r}; "
            f"expected one of {', '.join(SESSION_TYPES)}"
        )
    day = str(session.get("date", "")).strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date must be YYYY-MM-DD, got {day!r}") from None

    values = {
        "date": day,
        "session_type": session_type,
        "focus": session.get("focus"),
        "target_distance_km": session.get("target_distance_km"),
        "target_duration_min": session.get("target_duration_min"),
        "intensity": session.get("intensity"),
        "notes": session.get("notes"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reminder_id": None,
    }
    # reminder_id is intentionally absent from the update set: replanning a slot
    # rewrites its targets but keeps pointing at the same reminder, which the
    # bridge then updates in place instead of leaving a duplicate on the phone.
    assignments = ", ".join(
        f"{c} = excluded.{c}"
        for c in PLAN_COLUMNS
        if c not in ("date", "session_type", "reminder_id")
    )
    cur.execute(
        f"INSERT INTO planned_sessions ({', '.join(PLAN_COLUMNS)}) "
        f"VALUES ({', '.join(':' + c for c in PLAN_COLUMNS)}) "
        f"ON CONFLICT(date, session_type) DO UPDATE SET {assignments}",
        values,
    )


def delete_session(cur, day: str, session_type: str) -> bool:
    """Drop a planned slot, queueing its reminder for deletion if it had one."""
    session_type = session_type.lower().strip()
    row = cur.execute(
        "SELECT reminder_id FROM planned_sessions WHERE date = ? AND session_type = ?",
        (day, session_type),
    ).fetchone()
    if row and row[0]:
        cur.execute(
            "INSERT OR IGNORE INTO retired_reminders (reminder_id) VALUES (?)",
            (row[0],),
        )
    cur.execute(
        "DELETE FROM planned_sessions WHERE date = ? AND session_type = ?",
        (day, session_type),
    )
    return bool(cur.rowcount)


def _actuals_by_day(conn, start: str, end: str) -> dict:
    """{(day, session_type): [activity summary, ...]} for the window.

    activities.date is 'YYYY-MM-DD HH:MM:SS'; the plan is day-resolution, so
    everything collapses to the date prefix. Several rides on one day all
    attach to that day's cycling slot rather than fighting over it.
    """
    rows = conn.execute(
        "SELECT date, activity_type, title, distance_km, duration, avg_hr, "
        "max_hr, aerobic_te FROM activities "
        "WHERE substr(date, 1, 10) BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()

    type_to_session = {
        activity: session
        for session, activities in COMPLETED_BY.items()
        for activity in activities
    }

    actuals = {}
    for date_time, activity_type, title, dist, duration, avg_hr, max_hr, te in rows:
        session_type = type_to_session.get(activity_type)
        if session_type is None:
            continue  # Walking, Multisport — never completes a planned slot
        actuals.setdefault((date_time[:10], session_type), []).append(
            {
                "date": date_time,
                "activity_type": activity_type,
                "title": title,
                "distance_km": parse_number(dist),
                "duration": duration,
                "avg_hr": parse_number(avg_hr),
                "max_hr": parse_number(max_hr),
                "aerobic_te": parse_number(te),
            }
        )
    return actuals


def plan_with_status(conn, start: str, end: str, today: str = "") -> list[dict]:
    """Planned sessions in [start, end], each tagged done / missed / pending.

    'missed' is only ever assigned to a day that has already passed — today's
    unfinished slot is 'pending', not a failure.
    """
    today = today or _date.today().isoformat()
    actuals = _actuals_by_day(conn, start, end)

    cur = conn.execute(
        "SELECT * FROM planned_sessions WHERE date BETWEEN ? AND ? "
        "ORDER BY date, session_type",
        (start, end),
    )
    columns = [c[0] for c in cur.description]

    plan = []
    for row in cur.fetchall():
        entry = dict(zip(columns, row))
        matched = actuals.get((entry["date"], entry["session_type"]), [])
        if matched:
            entry["status"] = "done"
            entry["completed_by"] = matched
        elif entry["date"] < today:
            entry["status"] = "missed"
        else:
            entry["status"] = "pending"
        plan.append({k: v for k, v in entry.items() if v is not None})
    return plan


def adherence(conn, start: str, end: str, today: str = "") -> dict:
    """Plan vs actual over a window, including sessions done that weren't planned.

    Unplanned sessions are reported rather than ignored: training that happens
    off-plan is still training, and a block where everything is unplanned means
    the plan is wrong, not the athlete.
    """
    today = today or _date.today().isoformat()
    plan = plan_with_status(conn, start, end, today)

    planned_slots = {(p["date"], p["session_type"]) for p in plan}
    unplanned = [
        activity
        for slot, activities in sorted(_actuals_by_day(conn, start, end).items())
        if slot not in planned_slots
        for activity in activities
    ]

    counts = {"done": 0, "missed": 0, "pending": 0}
    for entry in plan:
        counts[entry["status"]] += 1
    resolved = counts["done"] + counts["missed"]

    return {
        "window": {"start": start, "end": end},
        "planned": len(plan),
        **counts,
        # Pending slots are excluded from the rate — a week still in progress
        # shouldn't read as failure.
        "adherence_pct": round(100 * counts["done"] / resolved) if resolved else None,
        "sessions": plan,
        "unplanned_sessions": unplanned,
    }


def _format(entry: dict) -> str:
    mark = {"done": "x", "missed": "!", "pending": " "}[entry["status"]]
    target = []
    if entry.get("target_distance_km"):
        target.append(f"{entry['target_distance_km']:g} km")
    if entry.get("target_duration_min"):
        target.append(f"{entry['target_duration_min']:g} min")
    bits = [entry["date"], f"[{mark}]", entry["session_type"]]
    if entry.get("focus"):
        bits.append(f"- {entry['focus']}")
    if target:
        bits.append(f"({', '.join(target)})")
    return " ".join(bits)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adherence", type=int, metavar="DAYS",
        help="report plan vs actual over the last DAYS instead of the upcoming plan",
    )
    parser.add_argument(
        "--ahead", type=int, default=14, metavar="DAYS",
        help="how far forward to list the plan (default 14)",
    )
    args = parser.parse_args()

    conn = get_plan_connection()
    today = _date.today()

    if args.adherence:
        start = (today.toordinal() - args.adherence)
        report = adherence(
            conn,
            _date.fromordinal(start).isoformat(),
            today.isoformat(),
        )
        print(
            f"{report['window']['start']} to {report['window']['end']}: "
            f"{report['done']}/{report['planned']} done, "
            f"{report['missed']} missed, {report['pending']} pending"
            + (f" ({report['adherence_pct']}%)" if report["adherence_pct"] is not None else "")
        )
        for entry in report["sessions"]:
            print("  " + _format(entry))
        if report["unplanned_sessions"]:
            print(f"{len(report['unplanned_sessions'])} unplanned session(s):")
            for activity in report["unplanned_sessions"]:
                print(f"  + {activity['date']} - {activity['activity_type']} - {activity['title']}")
    else:
        end = _date.fromordinal(today.toordinal() + args.ahead)
        plan = plan_with_status(conn, today.isoformat(), end.isoformat())
        if not plan:
            print(f"No sessions planned between {today} and {end}.")
        else:
            for entry in plan:
                print(_format(entry))
    conn.close()
