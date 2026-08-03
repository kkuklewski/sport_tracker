# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal training log + coaching assistant. Data originates from a Garmin watch and reaches `activities.db` two ways: live from the Garmin Connect API (`scripts/garmin_sync.py`, the normal path) or from a hand-exported `Activities.csv` (`scripts/import_activities.py`, the fallback). `activities.db` is the deduplicated, canonical store — always reason from it, never from the CSV directly.

Both paths share `scripts/db.py`, which formats and dedupes identically on `(date, title)`, so the same activity arriving from both sources is stored once.

## Remote MCP server (Claude.ai / mobile app)

`server/mcp_server.py` wraps the same pipeline as a FastMCP HTTP server so the Claude app can use it as a custom connector. It runs on the `vps-jarvis` VPS in Docker (deployed by Coolify from this repo's GitHub remote, `server/Dockerfile`), with `activities.db` and the Garmin token cache on a `/data` volume. Tools: `get_recent_activities`, `get_goals`, `sync_now`; a background thread also syncs every 6 h.

Auth is the URL itself — the endpoint is `https://sport-tracker.146.59.127.12.sslip.io/<MCP_PATH_SECRET>/mcp` (secret set in Coolify env vars; never commit it). `GOALS.md` is baked into the image, so goal edits reach the VPS via commit + push + Coolify redeploy. The local Mac workflow below is independent of the VPS deployment — two DBs, same dedup logic.

## Workflow — always do this first

Refresh the database **before** answering any training question:

```
.venv/bin/python scripts/garmin_sync.py --days 30
```

If that fails with an auth error, the cached token expired — run `--login` once interactively to answer the MFA prompt, then retry. If Garmin is unreachable, fall back to the CSV path and say so rather than answering from stale data.

**Never loop or retry logins.** Garmin IP-rate-limits the SSO endpoint and returns 429 (this already happens on a normal first login; the library recovers via a fallback strategy). Normal runs reuse the cached token in `~/.garminconnect` and never hit SSO at all — keep it that way.

Two fields are absent from the activity-list payload and are stored as `--`: `best_lap_time` and `avg_gap`. Fetching them would cost one extra API call per activity; neither is used for coaching.

When a new `Activities.csv` is dropped in instead:

```
.venv/bin/python scripts/import_activities.py            # or: <path/to/file.csv>
```

Both are idempotent — they only insert rows they haven't seen and print what's new.

## Answering "what should I train today"

1. Sync (above) to make sure `activities.db` is current.
2. Read `GOALS.md` for the user's stated goal, target weekly structure, and constraints.
3. Query `activities.db` for recent sessions (last ~7-14 days) — look at `activity_type`, `date`, `aerobic_te`, `avg_hr`/`max_hr`, and `duration` to gauge recent load and recovery, not just volume.
4. Recommend cycling, running, or mobility (the three activity types in scope) based on: what's under-represented lately, whether the last 1-2 sessions were hard (avoid stacking intensity — recommend mobility/easy work after a high-HR or high-TE session), and how it fits the stated goal in `GOALS.md`.
5. State the reasoning briefly (which recent sessions drove the call), not just the verdict.

## Data notes

- `activity_type` values seen so far: `Cycling`, `Running`, `Walking`, `Multisport`, `Other` (mobility sessions are logged as `Other`, titled "Mobility").
- Numeric-looking fields (`distance_km`, `calories`, `steps`, etc.) are stored as raw TEXT exactly as Garmin exports them — some contain thousands-separators (e.g. `"1,563"`) or `"--"` for not-applicable. Strip/parse before doing math.
- `date` is `YYYY-MM-DD HH:MM:SS` and unique per activity to the second; it's the dedup key together with `title`.
- Ignore any `*:Zone.Identifier` files alongside the CSV — that's just Windows/NTFS "downloaded from the internet" metadata, not activity data.
