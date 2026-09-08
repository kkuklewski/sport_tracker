# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal training log + coaching assistant. Data originates from a Garmin watch and reaches `activities.db` two ways: live from the Garmin Connect API (`scripts/garmin_sync.py`, the normal path) or from a hand-exported `Activities.csv` (`scripts/import_activities.py`, the fallback). `activities.db` is the deduplicated, canonical store — always reason from it, never from the CSV directly.

Both paths share `scripts/db.py`, which formats and dedupes identically on `(date, title)`, so the same activity arriving from both sources is stored once.

## Remote MCP server (Claude.ai / mobile app)

`server/mcp_server.py` wraps the same pipeline as a FastMCP HTTP server so the Claude app can use it as a custom connector. It runs on the `vps-jarvis` VPS in Docker (deployed by Coolify from this repo's GitHub remote, `server/Dockerfile`), with `activities.db` and the Garmin token cache on a `/data` volume. Tools: `get_recent_activities`, `get_goals`, `get_coaching_procedure`, `sync_now`, `log_weight` /
`get_weight_history`, the planner tools `plan_sessions`, `get_plan`,
`get_plan_adherence`, `cancel_planned_session`, and the load/readiness tools
`get_training_load`, `get_load_series`, `get_wellness`, `assess_today`; a background thread also syncs
every 6 h.

Auth is the URL itself — the endpoint is `https://sport-tracker.146.59.127.12.sslip.io/<MCP_PATH_SECRET>/mcp` (secret set in Coolify env vars; never commit it). `GOALS.md` is baked into the image, so goal edits reach the VPS via commit + push + Coolify redeploy. The local Mac workflow below is independent of the VPS deployment — two DBs,
same dedup logic. The VPS syncs activities and wellness for itself, so
`get_training_load` and `assess_today` work identically there, but
`planned_sessions` exists only where a plan was written: plan on the Mac and
the phone's `assess_today` reports no slot for today, because the row is not
in its database.

## Deploying to the VPS

The VPS is `ssh vps-jarvis` (Tailscale 100.71.106.77, user `ubuntu`); its
public address is 146.59.127.12. Coolify runs there in Docker and builds this
app from the GitHub remote.

| | |
|---|---|
| Coolify project / app | `kuklewski` / `sport-tracker`, id **4** |
| App UUID | `v4wtqyksex3rvjtudqyu9l7h` |
| Container | `v4wtqyksex3rvjtudqyu9l7h-<n>`, image tagged with the deployed commit |
| Build | `/server/Dockerfile`, branch `main`, commit `HEAD` |

**Pushing to GitHub does not deploy.** Coolify has `is_auto_deploy_enabled`
set, but the repository has no webhook and no deploy key pointing at it, so
nothing ever tells Coolify a push happened. Every deploy has to be triggered
by hand — the image tag on the running container is the commit actually
serving traffic, and it sat five weeks behind `main` before anyone noticed.

Queue a deployment (there is no `artisan` deploy command; this is what the UI
button calls):

```
ssh vps-jarvis 'docker exec coolify php artisan tinker --execute="
\$a = App\Models\Application::find(4);
\$uuid = (string) new Visus\Cuid2\Cuid2();
queue_application_deployment(application: \$a, deployment_uuid: \$uuid, is_api: true);
echo \"queued: \$uuid\n\";
"'
```

Then poll `App\Models\ApplicationDeploymentQueue::where('deployment_uuid', ...)`
for `status` — `in_progress` until `finished` or `failed`, and `commit` shows
what was built. A build takes about a minute.

**Never print `MCP_PATH_SECRET`.** It is the only authentication the endpoint
has. Read it into a shell variable inside the VPS when a request needs it, and
keep it out of command output and logs. A wrong path returns 404, which is the
quick way to confirm the guard still works.

Verifying a deploy is real means checking the tools over the wire, not just
that the container restarted: MCP needs an `initialize` handshake before
`tools/list`, so a bare POST correctly answers 400.

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

## The planner

`scripts/plan.py` stores intended sessions in a `planned_sessions` table in the
same `activities.db`. It exists because tracking alone cannot see a training
block evaporate: nothing was logged between 2026-08-10 and 2026-09-02, and no
part of the system noticed, because nothing recorded that sessions had been
intended.

```
.venv/bin/python scripts/plan.py                 # upcoming plan, with status
.venv/bin/python scripts/plan.py --adherence 28  # last 28 days, plan vs actual
```

- A plan row is `(date, session_type)` — `cycling`, `running`, or `mobility` —
  plus optional focus, target distance/duration, intensity, and notes. That
  pair is the primary key, so replanning a slot overwrites it and replanning a
  week is idempotent, like both ingest paths.
- Status is **derived, never stored**: a slot is `done` when an activity of a
  matching type exists that day, `missed` when the day has passed without one,
  `pending` otherwise. Nothing is ticked off by hand — a synced ride completes
  its slot. `Walking` and `Multisport` match nothing, so a walk never completes
  a planned session.
- `adherence()` also reports **unplanned** sessions. Training done off-plan is
  still training; a block that is all unplanned means the plan is wrong.
- The plan is data, `GOALS.md` is intent. Keep the weekly structure and
  progression in `GOALS.md`; keep concrete dated slots in the table.

The local `activities.db` and the VPS one are separate files, so a plan written
locally is not visible to the remote MCP server, and vice versa. Plan on
whichever surface you will actually be reading it from.

## Load and readiness

Three layers decide what to train, and each answers a question the others
cannot. `GOALS.md` fixes *what* the week contains, `scripts/load.py` says how
deep the hole is, `scripts/wellness.py` says whether the body agrees this
morning, and `scripts/advise.py` combines all three into one verdict.

```
.venv/bin/python scripts/wellness.py --days 7     # pull recovery data
.venv/bin/python scripts/load.py                  # CTL / ATL / TSB now
.venv/bin/python scripts/load.py --days 30        # the daily series
.venv/bin/python scripts/advise.py                # today's verdict
```

- **Load is Banister TRIMP, not TSS.** There is no cycling power meter, and
  `training_stress_score` is `0.0` on all 129 rows — as is `decompression`
  (`No` everywhere). Both columns are dead; never compute from them. The 23
  rows with power are all *running*, wrist-estimated.
- Resting and max HR are **measured, not assumed**: resting is the median of
  the last 90 days of `wellness` (median, so one freak night cannot inflate
  every TRIMP since), max is the highest ever recorded in `activities`.
- `CTL` is a 42-day and `ATL` a 7-day exponential average of daily TRIMP;
  `TSB = CTL - ATL` uses the *previous* day's values, since form is what you
  woke up with, before today's session counts against it.
- **TSB bands are calibrated per-athlete.** Textbook cycling bands assume a
  CTL of 60–100; peak here is ~24, so `calibrated_bands()` reads TSB
  percentiles from this athlete's own last year instead. Compare against
  `bands`, never against remembered numbers.
- `wellness` is expensive — six endpoints per day — so stored days are skipped
  unless within `REFRESH_DAYS`, because Garmin keeps revising the last day or
  two as the watch uploads the rest of the night. A day where the watch was off
  is left absent rather than stored empty, so gaps stay visible.
- `advise.assess()` takes the **strictest** verdict any single signal asks for.
  These are vetoes, not votes: downgrading a session costs a day, while
  overriding a buried body cost days two and three of the September tour.

## Apple Reminders bridge (plan -> iPhone)

`scripts/reminders.py` pushes planned sessions into the **Sport Activity**
Reminders list. Reminders has no HTTP API, so the only way in is AppleScript
against the Reminders app on this Mac.

```
.venv/bin/python scripts/reminders.py --dry-run   # show what would change
.venv/bin/python scripts/reminders.py             # reconcile the list
.venv/bin/python scripts/reminders.py --prune     # also clear stranded reminders
```

- **The lists are CalDAV, not iCloud** — every list on this Mac belongs to the
  `cloud.easecrafted.com` account. The plan reaches the phone because that
  account is configured there, not through iCloud sync. A list created under
  the local "On My Mac" account would never leave the machine.
- The bridge is a reconciler, not an appender. It creates slots that have no
  reminder, updates ones whose targets changed, deletes reminders retired by
  `cancel_planned_session`, and **rewrites nothing that already matches** — an
  unconditional update would bump every modification date and push needless
  CalDAV traffic on every scheduled run.
- **The loop closes both ways**: a synced Garmin activity marks the plan slot
  done, and the next reminders pass ticks the reminder off on the phone. Never
  complete one by hand.
- A reminder deleted on the phone is absent from the fetch, so its slot is
  recreated rather than updated into nothing.
- `--prune` deletes past-due reminders that were never completed and that no
  plan row owns — the wreckage an abandoned block leaves behind. It is opt-in
  and deliberately **not** part of `daily.sh`: overdue entries are still the
  user's data, and deleting them silently would look exactly like the bridge
  losing the plan. Undated reminders are left alone, being hand-typed rather
  than stranded slots.
- `planned_sessions.reminder_id` is the link, deliberately excluded from the
  upsert's update set so replanning a slot reuses its reminder. Deleting a slot
  parks the id in `retired_reminders` until the next pass can delete it for
  real.

`scripts/daily.sh` chains sync then reminders, in that order, and continues
past a failed Garmin sync so the plan still reaches the phone.
`scripts/com.easecrafted.sport-tracker.plist` runs it nightly at 21:30 —
copy it to `~/Library/LaunchAgents/` and `launchctl load` it to enable.

**Only this Mac can write Reminders.** The VPS has no Reminders app, so a
session planned from the phone through the remote MCP connector lands in the
VPS database and never becomes a reminder. Plan on the Mac, or accept the gap.

## Answering "what should I train today"

1. Sync (above) to make sure `activities.db` is current.
2. Read `GOALS.md` for the user's stated goal, target weekly structure, and constraints.
   Check `scripts/plan.py` for what was already planned — if today has a slot,
   the job is usually to confirm or adjust it, not to invent something else.
3. Query `activities.db` for recent sessions (last ~7-14 days) — look at `activity_type`, `date`, `aerobic_te`, `avg_hr`/`max_hr`, and `duration` to gauge recent load and recovery, not just volume.
4. Recommend cycling, running, or mobility (the three activity types in scope) based on: what's under-represented lately, whether the last 1-2 sessions were hard (avoid stacking intensity — recommend mobility/easy work after a high-HR or high-TE session), and how it fits the stated goal in `GOALS.md`.
5. State the reasoning briefly (which recent sessions drove the call), not just the verdict.

## Data notes

- `activity_type` values seen so far: `Cycling`, `Running`, `Walking`, `Multisport`, `Other` (mobility sessions are logged as `Other`, titled "Mobility").
- `activities.db` also holds `weight` (one row per day, `log_weight` overwrites
  the same day), `planned_sessions`, and `wellness` (see above). All are created
  lazily on first use, so an older database file picks them up without a
  migration.
- Dead columns, confirmed across all 129 rows: `training_stress_score` is always
  `0.0` and `decompression` always `No`. They look populated but carry nothing.
- Numeric-looking fields (`distance_km`, `calories`, `steps`, etc.) are stored as raw TEXT exactly as Garmin exports them — some contain thousands-separators (e.g. `"1,563"`) or `"--"` for not-applicable. Strip/parse before doing math.
- `date` is `YYYY-MM-DD HH:MM:SS` and unique per activity to the second; it's the dedup key together with `title`.
- Ignore any `*:Zone.Identifier` files alongside the CSV — that's just Windows/NTFS "downloaded from the internet" metadata, not activity data.
