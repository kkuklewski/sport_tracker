#!/usr/bin/env python3
"""Ingest a Garmin Connect Activities.csv export into activities.db, skipping rows already stored.

Usage: python3 scripts/import_activities.py [path/to/Activities.csv]
Defaults to Activities.csv in the project root.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import COLUMN_MAP, ROOT, describe, get_connection, insert_activity


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
            if insert_activity(cur, values):
                new_rows.append(describe(values))
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
