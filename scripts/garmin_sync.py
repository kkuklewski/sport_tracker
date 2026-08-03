#!/usr/bin/env python3
"""Pull activities straight from Garmin Connect into activities.db.

First run (interactive, answers the MFA prompt and caches a token):
    python3 scripts/garmin_sync.py --login

Every run after that is non-interactive and safe for cron:
    python3 scripts/garmin_sync.py --days 30

Check what would be written without touching the database:
    python3 scripts/garmin_sync.py --days 30 --dry-run

Credentials come from GARMIN_EMAIL / GARMIN_PASSWORD (a .env file in the project
root is read if present). Tokens are cached in GARMINTOKENS or ~/.garminconnect,
so the password is only needed for --login and after a token expires.

Values are formatted to match the Garmin Connect CSV export exactly, so rows
synced here are indistinguishable from rows imported by import_activities.py and
dedupe against them on (date, title).
"""
import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import ROOT, describe, get_connection, insert_activity

NA = "--"  # what Garmin's CSV export writes for a not-applicable field

# activityType.typeKey -> the activity_type labels already in the database.
# Anything unmatched falls back to "Other", which is where mobility lives.
TYPE_PREFIXES = {
    "running": "Running",
    "cycling": "Cycling",
    "biking": "Cycling",
    "walking": "Walking",
    "hiking": "Walking",
    "multi_sport": "Multisport",
}

# Types whose speed columns Garmin exports as pace (min/km) rather than km/h.
PACE_TYPES = {"Running", "Walking"}


def load_dotenv(path: Path) -> None:
    """Minimal .env reader — avoids a dependency for two variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --- formatting helpers: raw Garmin JSON (SI units) -> CSV display strings ---


def _num(value):
    """None/blank -> None, otherwise a float. Garmin uses null for missing."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first(activity: dict, *keys):
    """First key present with a usable value — Garmin names some fields per sport."""
    for key in keys:
        value = activity.get(key)
        if value is not None and value != "":
            return value
    return None


def fmt_decimal(value, places=1, scale=1.0) -> str:
    v = _num(value)
    return NA if v is None else f"{v * scale:.{places}f}"


def fmt_int(value, thousands=False) -> str:
    v = _num(value)
    if v is None:
        return NA
    return f"{int(round(v)):,}" if thousands else str(int(round(v)))


def to_seconds(value) -> float | None:
    """Garmin returns durations in seconds; guard against a milliseconds payload.

    No real session runs 55+ hours, so anything above that threshold is ms.
    """
    v = _num(value)
    if v is None:
        return None
    return v / 1000.0 if v > 200_000 else v


def fmt_duration(value) -> str:
    secs = to_seconds(value)
    if secs is None:
        return NA
    total = int(round(secs))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def fmt_pace(mps) -> str:
    """m/s -> M:SS per km, the way Garmin exports running and walking speed."""
    v = _num(mps)
    if not v:  # None or zero -> no meaningful pace
        return NA
    secs_per_km = 1000.0 / v
    if secs_per_km > 59 * 60:  # implausibly slow, Garmin leaves these blank
        return NA
    return f"{int(secs_per_km) // 60}:{int(round(secs_per_km)) % 60:02d}"


def fmt_speed(mps, activity_type: str) -> str:
    """Pace for running/walking, km/h for everything else."""
    if activity_type in PACE_TYPES:
        return fmt_pace(mps)
    return fmt_decimal(mps, places=1, scale=3.6)


def map_activity_type(activity: dict) -> str:
    type_key = (activity.get("activityType") or {}).get("typeKey", "") or ""
    for token, label in TYPE_PREFIXES.items():
        if token in type_key:
            return label
    return "Other"


def parse_start_time(activity: dict) -> str:
    """-> 'YYYY-MM-DD HH:MM:SS', the database's dedup key alongside title."""
    raw = activity.get("startTimeLocal") or ""
    return raw.replace("T", " ").split(".")[0]


def map_activity(activity: dict) -> dict:
    """Garmin JSON -> a row shaped exactly like the CSV export."""
    activity_type = map_activity_type(activity)

    return {
        "activity_type": activity_type,
        "date": parse_start_time(activity),
        "favorite": "true" if activity.get("favorite") else "false",
        "title": activity.get("activityName") or "",
        "distance_km": fmt_decimal(activity.get("distance"), 2, 1 / 1000),
        "calories": fmt_int(activity.get("calories"), thousands=True),
        "duration": fmt_duration(activity.get("duration")),
        "avg_hr": fmt_int(activity.get("averageHR")),
        "max_hr": fmt_int(activity.get("maxHR")),
        "aerobic_te": fmt_decimal(activity.get("aerobicTrainingEffect"), 1),
        # Garmin's CSV reuses the bike-cadence columns for running cadence
        # (steps/min), so fall back the same way or run cadence is lost.
        "avg_bike_cadence": fmt_int(
            first(activity, "averageBikingCadenceInRevPerMinute",
                  "averageRunningCadenceInStepsPerMinute")
        ),
        "max_bike_cadence": fmt_int(
            first(activity, "maxBikingCadenceInRevPerMinute",
                  "maxRunningCadenceInStepsPerMinute")
        ),
        "avg_speed": fmt_speed(activity.get("averageSpeed"), activity_type),
        "max_speed": fmt_speed(activity.get("maxSpeed"), activity_type),
        "total_ascent": fmt_int(activity.get("elevationGain")),
        "total_descent": fmt_int(activity.get("elevationLoss")),
        "avg_stride_length": fmt_decimal(activity.get("avgStrideLength"), 2, 1 / 100),
        "avg_gap": NA,  # grade-adjusted pace is not in the activity list payload
        "normalized_power": fmt_int(activity.get("normPower")),
        # CSV writes 0.0 rather than "--" when there is no TSS; match it.
        "training_stress_score": fmt_decimal(
            activity.get("trainingStressScore") or 0.0, 1
        ),
        "avg_power": fmt_int(activity.get("avgPower")),
        "max_power": fmt_int(activity.get("maxPower")),
        "steps": fmt_int(activity.get("steps"), thousands=True),
        "decompression": "No",
        "best_lap_time": NA,
        "number_of_laps": fmt_int(activity.get("lapCount")),
        "avg_resp": fmt_int(activity.get("avgRespirationRate")),
        "min_resp": fmt_int(activity.get("minRespirationRate")),
        "max_resp": fmt_int(activity.get("maxRespirationRate")),
        "moving_time": fmt_duration(activity.get("movingDuration")),
        "elapsed_time": fmt_duration(activity.get("elapsedDuration")),
        "min_elevation": fmt_int(activity.get("minElevation")),
        "max_elevation": fmt_int(activity.get("maxElevation")),
    }


# --- Garmin Connect session ---


def connect(interactive: bool):
    """Return a logged-in client, preferring the cached token over credentials.

    login() persists tokens itself on the plain credential path, but the MFA
    early-return path bails out before that, so the resume branch dumps by hand.
    """
    from garminconnect import Garmin

    tokenstore = os.path.expanduser(os.environ.get("GARMINTOKENS", "~/.garminconnect"))
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")

    client = Garmin(email=email, password=password, return_on_mfa=interactive)
    mfa_status, state = client.login(tokenstore)

    if mfa_status in ("needs_mfa", "MFA_REQUIRED"):
        if not interactive:
            raise SystemExit(
                "Garmin needs an MFA code, which cron cannot answer.\n"
                "Run 'python3 scripts/garmin_sync.py --login' once to refresh the token."
            )
        code = input("Garmin MFA code: ").strip()
        client.resume_login(state, code)
        client.client.dump(tokenstore)
        print(f"Token cached in {tokenstore} — future runs are non-interactive.")

    return client


def sync(days: int, dry_run: bool, interactive: bool) -> int:
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    end = date.today()
    start = end - timedelta(days=days)

    try:
        client = connect(interactive)
        activities = client.get_activities_by_date(start.isoformat(), end.isoformat())
    except GarminConnectAuthenticationError as exc:
        raise SystemExit(
            f"Garmin login rejected: {exc}\n"
            "Token has probably expired — run with --login to re-authenticate."
        )
    except GarminConnectTooManyRequestsError as exc:
        raise SystemExit(f"Rate limited by Garmin, try again later: {exc}")
    except GarminConnectConnectionError as exc:
        raise SystemExit(f"Could not reach Garmin: {exc}")

    rows = [map_activity(a) for a in activities]
    rows = [r for r in rows if r["date"]]
    print(f"Fetched {len(rows)} activity(ies) from {start} to {end}.")

    if dry_run:
        for row in rows:
            print(f"  ? {describe(row)}")
            print(
                f"      {row['distance_km']} km  {row['duration']}  "
                f"HR {row['avg_hr']}/{row['max_hr']}  TE {row['aerobic_te']}  "
                f"speed {row['avg_speed']}"
            )
        print("\nDry run — nothing written.")
        return 0

    conn = get_connection()
    cur = conn.cursor()
    new_rows = [describe(r) for r in rows if insert_activity(cur, r)]
    conn.commit()
    conn.close()

    if new_rows:
        print(f"{len(new_rows)} new training(s) added:")
        for r in new_rows:
            print(f"  + {r}")
    else:
        print("No new trainings found.")

    skipped = len(rows) - len(new_rows)
    if skipped:
        print(f"({skipped} activity(ies) already in database, skipped)")
    return len(new_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    parser.add_argument("--login", action="store_true", help="interactive login, answers MFA")
    parser.add_argument("--dry-run", action="store_true", help="print rows, write nothing")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    sync(days=args.days, dry_run=args.dry_run, interactive=args.login)
