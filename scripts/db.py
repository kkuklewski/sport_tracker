"""Shared storage layer for activities.db.

Both ingest paths — the Garmin Connect CSV export (import_activities.py) and the
live Garmin Connect API (garmin_sync.py) — go through insert_activity() so they
dedupe against each other on (date, title).
"""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# DB_PATH env var lets the VPS container keep the database on a persistent
# volume; without it the database lives next to the scripts as before.
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "activities.db"))

# CSV header -> sqlite column name. Also defines the table schema, so the column
# order here is the column order in the database.
COLUMN_MAP = {
    "Activity Type": "activity_type",
    "Date": "date",
    "Favorite": "favorite",
    "Title": "title",
    "Distance": "distance_km",
    "Calories": "calories",
    "Time": "duration",
    "Avg HR": "avg_hr",
    "Max HR": "max_hr",
    "Aerobic TE": "aerobic_te",
    "Avg Bike Cadence": "avg_bike_cadence",
    "Max Bike Cadence": "max_bike_cadence",
    "Avg Speed": "avg_speed",
    "Max Speed": "max_speed",
    "Total Ascent": "total_ascent",
    "Total Descent": "total_descent",
    "Avg Stride Length": "avg_stride_length",
    "Avg GAP": "avg_gap",
    "Normalized Power® (NP®)": "normalized_power",
    "Training Stress Score®": "training_stress_score",
    "Avg Power": "avg_power",
    "Max Power": "max_power",
    "Steps": "steps",
    "Decompression": "decompression",
    "Best Lap Time": "best_lap_time",
    "Number of Laps": "number_of_laps",
    "Avg Resp": "avg_resp",
    "Min Resp": "min_resp",
    "Max Resp": "max_resp",
    "Moving Time": "moving_time",
    "Elapsed Time": "elapsed_time",
    "Min Elevation": "min_elevation",
    "Max Elevation": "max_elevation",
}

COLUMNS = list(COLUMN_MAP.values())


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    columns_sql = ",\n            ".join(f"{col} TEXT" for col in COLUMNS)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS activities (
            {columns_sql},
            UNIQUE(date, title)
        )
        """
    )
    return conn


def insert_activity(cur: sqlite3.Cursor, values: dict) -> bool:
    """Insert one activity. Returns True if it was new, False if already stored.

    Unknown keys are dropped rather than raising, so a change in the Garmin API
    payload can't take down a sync.
    """
    values = {k: v for k, v in values.items() if k in COLUMNS}
    if not values.get("date"):
        return False

    cols = ", ".join(values)
    placeholders = ", ".join(f":{c}" for c in values)
    cur.execute(
        f"INSERT OR IGNORE INTO activities ({cols}) VALUES ({placeholders})",
        values,
    )
    return bool(cur.rowcount)


def describe(values: dict) -> str:
    """One-line summary of an activity, for sync output."""
    return (
        f"{values.get('date', '?')} - "
        f"{values.get('activity_type', '?')} - "
        f"{values.get('title', '?')}"
    )
