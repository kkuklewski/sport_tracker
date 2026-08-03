#!/usr/bin/env python3
"""Ingest a Garmin Connect Activities.csv export into activities.db, skipping rows already stored.

Usage: python3 scripts/import_activities.py [path/to/Activities.csv]
Defaults to Activities.csv in the project root.
"""
import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "activities.db"

# CSV header -> sqlite column name
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


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    columns_sql = ",\n            ".join(f"{col} TEXT" for col in COLUMN_MAP.values())
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS activities (
            {columns_sql},
            UNIQUE(date, title)
        )
        """
    )
    return conn


def import_csv(csv_path: Path) -> tuple[list[str], int]:
    conn = get_connection()
    cur = conn.cursor()

    new_rows = []
    skipped = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("Date"):
                continue
            values = {COLUMN_MAP[k]: v for k, v in row.items() if k in COLUMN_MAP}
            cols = ", ".join(values.keys())
            placeholders = ", ".join(f":{c}" for c in values.keys())
            cur.execute(
                f"INSERT OR IGNORE INTO activities ({cols}) VALUES ({placeholders})",
                values,
            )
            if cur.rowcount:
                new_rows.append(f"{values['date']} - {values['activity_type']} - {values['title']}")
            else:
                skipped += 1

    conn.commit()
    conn.close()
    return new_rows, skipped


if __name__ == "__main__":
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Activities.csv"
    if not csv_path.exists():
        print(f"No such file: {csv_path}")
        sys.exit(1)

    new_rows, skipped = import_csv(csv_path)

    if new_rows:
        print(f"{len(new_rows)} new training(s) added:")
        for r in new_rows:
            print(f"  + {r}")
    else:
        print("No new trainings found.")
    if skipped:
        print(f"({skipped} row(s) already in database, skipped)")
