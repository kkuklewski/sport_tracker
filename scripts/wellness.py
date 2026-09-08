#!/usr/bin/env python3
"""Daily recovery data from Garmin — the half of the picture activities miss.

activities.db records what was ridden; this records what the body was doing
around it. Garmin already computes training readiness, HRV status, resting HR,
Body Battery, sleep and VO2max every night, and none of it was reaching the
coaching agent, which is why "should I ride today" could only ever be answered
from last week's mileage.

Costly compared to the activity sync — the summaries live behind six separate
endpoints, so each day costs six calls. Days already stored are therefore
skipped unless they fall inside REFRESH_DAYS, because Garmin keeps revising
the last day or two as the watch uploads more of the night.

Usage:
    python3 scripts/wellness.py --days 7
    python3 scripts/wellness.py --days 30 --refresh-all   # backfill
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection
from garmin_sync import connect, load_dotenv, ROOT

# Garmin revises recent days as the watch syncs the rest of the night, so the
# newest days are always re-fetched even when already stored.
REFRESH_DAYS = 2

COLUMNS = (
    "date", "readiness_score", "readiness_level", "readiness_feedback",
    "recovery_time_min", "acute_load", "hrv_last_night", "hrv_weekly_avg",
    "hrv_status", "hrv_baseline_low", "hrv_baseline_upper", "resting_hr",
    "body_battery_charged", "body_battery_drained", "sleep_seconds",
    "sleep_score", "vo2max", "synced_at",
)


def get_wellness_connection():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wellness (
            date TEXT PRIMARY KEY,
            readiness_score INTEGER,
            readiness_level TEXT,
            readiness_feedback TEXT,
            recovery_time_min INTEGER,
            acute_load INTEGER,
            hrv_last_night INTEGER,
            hrv_weekly_avg INTEGER,
            hrv_status TEXT,
            hrv_baseline_low INTEGER,
            hrv_baseline_upper INTEGER,
            resting_hr INTEGER,
            body_battery_charged INTEGER,
            body_battery_drained INTEGER,
            sleep_seconds INTEGER,
            sleep_score INTEGER,
            vo2max REAL,
            synced_at TEXT
        )
        """
    )
    return conn


def _safe(fn, default=None):
    """Endpoints 404 or return empty for days the watch wasn't worn.

    A missing signal must never abort the whole day — a night without HRV is
    normal, and the readiness score for that day is still worth storing.
    """
    try:
        result = fn()
    except Exception:
        return default
    return default if result in (None, [], {}) else result


def fetch_day(client, day: str) -> dict:
    """All six wellness summaries for one date, flattened to one row."""
    row = {"date": day}

    readiness = _safe(lambda: client.get_training_readiness(day), [])
    if readiness:
        r = readiness[0]
        row.update(
            readiness_score=r.get("score"),
            readiness_level=r.get("level"),
            readiness_feedback=r.get("feedbackShort"),
            recovery_time_min=r.get("recoveryTime"),
            acute_load=r.get("acuteLoad"),
        )

    hrv = _safe(lambda: client.get_hrv_data(day), {}) or {}
    summary = hrv.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    row.update(
        hrv_last_night=summary.get("lastNightAvg"),
        hrv_weekly_avg=summary.get("weeklyAvg"),
        hrv_status=summary.get("status"),
        hrv_baseline_low=baseline.get("balancedLow"),
        hrv_baseline_upper=baseline.get("balancedUpper"),
    )

    rhr = _safe(lambda: client.get_rhr_day(day), {}) or {}
    metrics = ((rhr.get("allMetrics") or {}).get("metricsMap") or {})
    entries = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
    if entries and entries[0].get("value") is not None:
        row["resting_hr"] = int(entries[0]["value"])

    battery = _safe(lambda: client.get_body_battery(day, day), [])
    if battery:
        row.update(
            body_battery_charged=battery[0].get("charged"),
            body_battery_drained=battery[0].get("drained"),
        )

    sleep = (_safe(lambda: client.get_sleep_data(day), {}) or {}).get("dailySleepDTO") or {}
    row["sleep_seconds"] = sleep.get("sleepTimeSeconds")
    row["sleep_score"] = ((sleep.get("sleepScores") or {}).get("overall") or {}).get("value")

    status = _safe(lambda: client.get_training_status(day), {}) or {}
    vo2 = (status.get("mostRecentVO2Max") or {}).get("generic") or {}
    row["vo2max"] = vo2.get("vo2MaxPreciseValue")

    return row


def sync_wellness(days: int = 7, refresh_all: bool = False,
                  interactive: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    conn = get_wellness_connection()

    today = date.today()
    wanted = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    if not refresh_all:
        stored = {
            row[0] for row in conn.execute("SELECT date FROM wellness")
        }
        cutoff = (today - timedelta(days=REFRESH_DAYS)).isoformat()
        wanted = [d for d in wanted if d not in stored or d >= cutoff]
    if not wanted:
        conn.close()
        return 0

    client = connect(interactive)
    from datetime import datetime

    written = 0
    for day in sorted(wanted):
        row = fetch_day(client, day)
        # A row with nothing but a date means the watch was off; storing it
        # would only mask the gap behind a full-looking table.
        if all(v is None for k, v in row.items() if k != "date"):
            continue
        row["synced_at"] = datetime.now().isoformat(timespec="seconds")
        values = {c: row.get(c) for c in COLUMNS}
        assignments = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "date")
        conn.execute(
            f"INSERT INTO wellness ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join(':' + c for c in COLUMNS)}) "
            f"ON CONFLICT(date) DO UPDATE SET {assignments}",
            values,
        )
        written += 1
    conn.commit()
    conn.close()
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="how many days back (default 7)")
    parser.add_argument("--refresh-all", action="store_true",
                        help="re-fetch days already stored, not just the recent ones")
    parser.add_argument("--login", action="store_true", help="interactive login, answers MFA")
    args = parser.parse_args()

    count = sync_wellness(args.days, args.refresh_all, args.login)
    print(f"{count} wellness day(s) written." if count else "Wellness data already current.")
