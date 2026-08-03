# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal training log + coaching assistant. Data originates from a Garmin watch, exported by hand from Garmin Connect as `Activities.csv` (no automatic sync is configured — see project memory for why). The CSV gets dropped into this repo periodically with newer rows prepended; `activities.db` is the deduplicated, canonical store built from it.

## Workflow — always do this first

Whenever `Activities.csv` has changed (new export dropped in), run the importer **before** answering any training question:

```
python3 scripts/import_activities.py
```

It's idempotent: it dedupes against `activities.db` on `(date, title)` and only inserts rows it hasn't seen, printing what's new. Always run it first so recommendations use current data — never reason from the CSV directly, reason from `activities.db`.

To import a differently-named/located export: `python3 scripts/import_activities.py path/to/file.csv`.

## Answering "what should I train today"

1. Run the importer (above) to make sure `activities.db` is current.
2. Read `GOALS.md` for the user's stated goal, target weekly structure, and constraints.
3. Query `activities.db` for recent sessions (last ~7-14 days) — look at `activity_type`, `date`, `aerobic_te`, `avg_hr`/`max_hr`, and `duration` to gauge recent load and recovery, not just volume.
4. Recommend cycling, running, or mobility (the three activity types in scope) based on: what's under-represented lately, whether the last 1-2 sessions were hard (avoid stacking intensity — recommend mobility/easy work after a high-HR or high-TE session), and how it fits the stated goal in `GOALS.md`.
5. State the reasoning briefly (which recent sessions drove the call), not just the verdict.

## Data notes

- `activity_type` values seen so far: `Cycling`, `Running`, `Walking`, `Multisport`, `Other` (mobility sessions are logged as `Other`, titled "Mobility").
- Numeric-looking fields (`distance_km`, `calories`, `steps`, etc.) are stored as raw TEXT exactly as Garmin exports them — some contain thousands-separators (e.g. `"1,563"`) or `"--"` for not-applicable. Strip/parse before doing math.
- `date` is `YYYY-MM-DD HH:MM:SS` and unique per activity to the second; it's the dedup key together with `title`.
- Ignore any `*:Zone.Identifier` files alongside the CSV — that's just Windows/NTFS "downloaded from the internet" metadata, not activity data.
