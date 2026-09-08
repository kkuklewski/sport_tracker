# Training Goals

<!-- The coaching agent reads this file alongside activities.db before
     recommending a session. Keep it short and current; update it whenever
     the goal or constraints change. -->

## Primary goal

**94 kg → 86 kg** (188 cm; BMI 26.6 → 24.3). Set 2026-08-03 at 92 kg,
restated 2026-09-08 at 94 kg — the number went the wrong way during a month
with almost no training, which is the whole argument for the plan below.

At a sustainable ~0.4–0.5 kg per week this lands around **mid-January 2027**.
That is the honest arithmetic, not a stretch target. History says it works:
Nov–Dec 2025 dropped ~10 kg on regular strength/mobility sessions almost
every other day.

There is deliberately **no event** on the calendar right now. The September
tour is done (see below); the next one gets scheduled once the weekly rhythm
is holding, not before.

## Target weekly structure

The lever is consistency, not volume. Four slots a week, planned in advance:

- **2× mobility** — in practice strength training with mobility elements
  (the Nov–Dec 2025 format that drove the -10 kg cut), not light stretching.
  Content is coached externally. Schedule the slot, never prescribe the
  content.
- **2× bike** — alternate gravel and MTB. One longer endurance ride, one
  short/easy Zone 2.
- **Running** — optional, purely by feel. Mostly relaxation; occasional hard
  efforts self-selected, never prescribed.

A week where all four slots happen beats a week with one heroic session.
The metric that matters is adherence over the last 28 days, not any single
ride.

## Rebuilding the base — driven by CTL, not by dates

The previous version of this section was a fixed table of distances by week.
That is exactly the shape of plan that failed in August: a number written in
advance cannot know what the body absorbed, so it is either ignored or
obeyed into a hole. The long ride now grows on a condition instead.

**Grow the long ride only when all three hold on the morning of the ride:**

- `get_training_load` shows `ctl_change_7d` **at or below +3** — the last
  week's load has been absorbed, not just survived.
- `tsb` is **above the `low` band** returned in the same call (your own
  fatigued threshold, not a textbook one).
- `assess_today` returns `as_planned` or `can_push`.

**When they hold:** next long ride is the last completed one **+10%**, capped
at +5 km. **When they don't:** repeat the same distance, or drop to the short
easy ride. Never skip forward to "catch up" — the ladder has no schedule to
be behind.

Rough shape this implies, if nothing gets interrupted: 20 → 22 → 25 → 27 →
30 km and onward, reaching 55–60 km around late November. Slower than the
August table promised, and unlike that table it is allowed to be wrong
without costing anything.

**Ceiling:** hold at 55–60 km until a new event sets a target. CTL should sit
around 30–35 by then — meaningfully above the 24.7 peak on record, which is
the real measure of whether this worked.

## Training philosophy

- Progressive volume increase, not aggressive.
- Recovery and listening to the body take priority over hitting a number —
  and "listening" now has numbers of its own: TSB, CTL ramp, Garmin readiness
  and HRV status, combined by `assess_today`.
- Most riding is easy/endurance **Zone 2** (~avg HR 125–140 based on history).
- Never stack intensity: after a high-HR or high-TE session, next session is
  mobility or easy.
- Weight is driven by the weekly structure holding, not by extra volume and
  never by prescribed dieting.

## Current state (2026-09-08)

- **The September tour happened, partially.** Sep 3: 78.2 km / 5h36 (avg HR
  124, TE 3.4) — the target distance, ridden off a 27 km lifetime max. Sep 4
  scaled back to 13.1 km, Sep 5 to 27.4 km. Day one proved the distance is
  reachable; days two and three proved the base wasn't there to repeat it.
- **The build never happened.** Nothing was logged between 2026-08-10 and
  2026-09-02 — a 24-day gap in what was meant to be the four-week block. This
  is the failure mode the plan tracking now exists to catch.
- Back training since: Sep 7 ride 31.5 km, avg HR 157, TE 5.0 (hard for the
  current base), Sep 8 mobility.
- Longest ride on record: **78.2 km / 5h36 (2026-09-03).**
- 5 km PB 28:47 (2026-08-02), max HR 200.
- No injuries reported.

## Non-negotiables

- Every long ride is ridden on the **actual loaded setup** — same bike, bags,
  shorts, shoes, saddle. Saddle tolerance and fueling end tours; fitness
  rarely does.
- The week is planned before it starts. An unplanned week is the one that
  disappears.
