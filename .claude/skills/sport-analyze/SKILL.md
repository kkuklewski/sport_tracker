---
name: sport-analyze
description: Sync Garmin activities, check the current goal in GOALS.md, analyze recent training load, and propose the next 3 workouts. Use when the user asks "what should I train next", "what are my next workouts", "plan my next sessions", or invokes /sport-analyze.
---

# Next Workouts

Propose the next 3 training sessions from real data: fresh Garmin activity,
the stated goal, and an actual load analysis — not a fixed template. Judge
what's needed from the numbers each time; don't assume the mix has to be
"3 bike rides" or "3 chronological slots" — decide from the data.

## Step 1 — Sync

Refresh `activities.db` before analyzing anything:

```
.venv/bin/python scripts/garmin_sync.py --days 30
```

- Auth error → cached token expired. Tell the user to run
  `.venv/bin/python scripts/garmin_sync.py --login` once for the MFA prompt,
  then retry. Do not loop retries — Garmin rate-limits repeated SSO hits.
- Garmin unreachable → fall back to `.venv/bin/python scripts/import_activities.py`
  if a fresh `Activities.csv` is available, and say explicitly the analysis is
  using whatever is already in `activities.db` if not.

## Step 2 — Read the goal and memory

Read `GOALS.md` in full: the primary goal, the target weekly structure, the
long-ride progression table, training philosophy, and — importantly — the
"Constraints and current state" and "Non-negotiables" sections. Work out
which week of the plan today falls into from the progression table's date
ranges, and what that week's target long ride is.

Also check your memory system (`MEMORY.md` and linked files) for context that
never made it into `GOALS.md` — home equipment (e.g. a jacuzzi for post-ride
recovery), venue proximity for each bike (forest for MTB, paved path for
gravel), what "mobility" actually means for this user (e.g. a specific
YouTube channel), and recurring commitments (e.g. a biweekly yoga class) that
could overlap a mobility slot instead of stacking a separate session that
week. Use it to make session suggestions concrete, not to override GOALS.md.

## Step 3 — Reconcile the "Sport Activity" reminders list

Before analyzing load, check what was actually planned and what actually
happened, since the user may train harder or easier than what was proposed
last time.

Read the incomplete reminders in the Apple Reminders list `Sport Activity`:

```
osascript -e 'tell application "Reminders" to get {name, due date} of (reminders in list "Sport Activity" whose completed is false)'
```

For each pending reminder (a previously-proposed session), check `activities.db`
for a matching logged activity around/after its due date:

- **Not yet logged and due date hasn't passed** — still upcoming, leave it,
  don't re-propose it as if it were new.
- **Logged and matches what was planned** (type/duration/intensity roughly
  in line) — treat it as done; it will naturally drop out of "next 3" once
  Step 4's load analysis sees it in `activities.db`.
- **Logged but harder/easier than planned** (e.g. planned an easy Zone 2
  spin but `avg_hr`/`max_hr`/`aerobic_te` came back at a hard-effort level,
  or a planned long ride was cut short) — this is the signal that matters
  most: the *actual* logged data overrides the old plan. Say so explicitly
  ("planned X, but you actually did Y") and let Step 4's stacking/ACWR logic
  key off the real numbers, not the stale reminder. A harder-than-planned
  session especially should trigger the intensity-stacking rule for the next
  pick.
- **Overdue and never logged** (skipped) — note it was skipped, don't carry
  it forward as a debt to repay; just factor the missed volume into Step 4's
  type-balance/progression check like any other gap.

Reminders are a planning aid, not a source of truth about training load —
`activities.db` always wins when the two disagree.

## Step 4 — Analyze load, not just volume

Query `activities.db` for at least the last 28 days (fall back to whatever
history exists if the log is shorter — say so, don't fake it). For each
activity, note `activity_type`, `date`, `duration`, `avg_hr`, `max_hr`,
`aerobic_te`. Remember these are stored as raw TEXT — parse `duration`
(`HH:MM:SS`) to minutes and strip `--`/thousands-separators before doing
math.

Build a real picture, not a vibe:

1. **Acute vs chronic load.** Sum training minutes for the last 7 days
   (acute) and the daily average over the last 28 days scaled to a week
   (chronic — use whatever history exists if less than 28 days, and note
   the ratio is unreliable until there's real chronic history to compare
   against). Flag ballpark zones: under ~0.8 suggests room to load more,
   0.8–1.3 is the sustainable range, above ~1.5 is a ramp-too-fast signal
   worth respecting even mid-plan. This is directional, not a hard gate —
   the stated goal's progression table takes precedence when they conflict,
   but call out the tension if the ratio looks like a genuine risk.
2. **Intensity stacking.** Look at the last 1–2 sessions specifically. A
   high `avg_hr`/`max_hr` or `aerobic_te` (roughly TE ≥ 4 or HR pushing into
   the high 170s+/max for this user) means the next session should not add
   more intensity — mobility slot or an easy spin, per the training
   philosophy in GOALS.md.
3. **Progression check.** Compare the long rides actually logged this week
   against the progression table's target for this week. Behind target?
   Ahead? On the back-to-back weekend pattern (weeks 3–4), check whether
   both days of a back-to-back have actually happened yet.
4. **Type balance.** Look at what's under-represented over the last ~14
   days relative to the target weekly structure (2× mobility, 2× bike,
   optional running).

## Step 5 — Propose the next 3 workouts

Decide the 3 sessions from the analysis above — don't default to a fixed
pattern. For each proposed session give: activity type, target
duration/distance (grounded in the progression table if it's a bike
session), target intensity (Zone 2 / easy vs a specific harder effort — only
prescribe intensity for bike sessions; mobility is schedule-only per GOALS.md
and running is never prescribed, so a running "slot" should read as
optional/by-feel, not a set workout), and one line of why (which specific
recent session, which ACWR signal, or which progression-table gap drove it).

If a long ride is among the 3, restate the non-negotiable: ride it on the
actual loaded touring setup (bike, bags, shorts, shoes, saddle). If memory
notes a home recovery resource (e.g. a jacuzzi), suggest it as a post-long-ride
recovery aid — not as a session of its own.

For every bike session (the only ones with a prescribed intensity), add a
**Garmin workout builder** line: the exact structured-workout spec to enter
in Garmin Connect (web or app) so the watch guides it live — steps as
duration-or-distance + target type (HR zone/range beats vague "Zone 2"),
e.g. "Warmup 10min HR 110-130 → Main 35km HR 125-140 → Cooldown 5min HR
<120". Use the HR ranges from GOALS.md's training philosophy (~125-140 for
Zone 2) rather than inventing new ones. Skip this line for mobility (no
prescribed content) and running (never prescribed).

## Step 6 — State the reasoning briefly

Lead with 2-4 sentences summarizing the load picture (acute/chronic ratio,
any stacking flag, where the week stands vs the progression table), then the
3 proposed sessions. Keep it tight — this is a data-driven call, not an
essay.

## Step 7 — Offer to save to Apple Reminders

After presenting the 3 sessions, ask the user if they want to save them to
the Apple Reminders list `Sport Activity`. If they accept, create one
reminder per proposed session using the `reminder` skill's mechanism
(`add_reminder.js`, list: `"Sport Activity"`), with:

- `title` — session type + the key number, e.g. "Bike (long) — 35km Zone 2"
  or "Mobility — Taz Dojo"
- `notes` — the one-line why plus the Garmin workout builder spec (for bike
  sessions), so the reminder is self-contained
- `due` — a reasonable date if the user gives one (e.g. "tomorrow", a named
  day); if the user doesn't specify timing, ask rather than guessing dates

Don't save anything the user didn't explicitly accept — if they only accept
1 or 2 of the 3, save only those.
