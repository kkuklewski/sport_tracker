#!/usr/bin/env python3
"""The daily verdict: does today's planned slot run, soften, or move?

Three layers meet here. GOALS.md fixes *what* the week contains, load.py says
how deep the hole is, and Garmin's wellness data says whether the body agrees
this morning. Any one of them alone gives bad advice: the plan alone is blind
to fatigue, load alone cannot see a sleepless night, and readiness alone has no
idea a 5-hour ride is scheduled.

The verdict is deliberately conservative in one direction only. Downgrading a
session costs a day; overriding a body that is already buried cost this athlete
the second and third days of the September tour.

Usage:
    python3 scripts/advise.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load import DETRAINED_FRACTION, current
from plan import get_plan_connection, plan_with_status

# Ordered from most to least restrictive; the final verdict is the strictest
# any single signal asks for, because these are veto signals rather than votes.
VERDICTS = ("rest", "mobility_only", "easy_only", "as_planned", "can_push")

# CTL rising faster than this in a week is a ramp the body has not agreed to.
SAFE_CTL_RAMP = 5.0


def _strictest(*verdicts) -> str:
    return min(verdicts, key=VERDICTS.index)


def wellness_today(conn) -> dict:
    row = conn.execute(
        "SELECT date, readiness_score, readiness_level, readiness_feedback, "
        "hrv_status, hrv_last_night, hrv_baseline_low, resting_hr, sleep_seconds, "
        "sleep_score, recovery_time_min FROM wellness ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    keys = ("date", "readiness_score", "readiness_level", "readiness_feedback",
            "hrv_status", "hrv_last_night", "hrv_baseline_low", "resting_hr",
            "sleep_seconds", "sleep_score", "recovery_time_min")
    return dict(zip(keys, row))


def assess(day: str = "") -> dict:
    """Today's load, wellness, planned slot, and what to do about all three."""
    day = day or date.today().isoformat()
    conn = get_plan_connection()

    load = current(conn)
    wellness = wellness_today(conn)
    slots = [
        entry for entry in plan_with_status(conn, day, day)
        if entry["status"] != "done"
    ]
    conn.close()

    verdict = "as_planned"
    reasons = []

    bands = load.get("bands") or {}
    tsb = load.get("tsb")
    if tsb is not None and bands:
        if tsb <= bands["deep_low"]:
            verdict = _strictest(verdict, "easy_only")
            reasons.append(
                f"TSB {tsb:+} is below your own 10th percentile ({bands['deep_low']:+}) — "
                f"deeper fatigue than all but a tenth of the last year."
            )
        elif tsb <= bands["low"]:
            # Informational only. The bands are percentiles of a year with long
            # idle gaps, so a steady four-sessions-a-week rhythm lives between
            # deep_low and low (about -5 to -9 at CTL ~19). Vetoing here would
            # cap every day of a consistent week at easy_only.
            reasons.append(
                f"TSB {tsb:+} sits in your fatigued band (below {bands['low']:+}) — "
                f"normal for a consistent week, not a veto."
            )
        elif tsb >= bands["high"]:
            # The fresh band is the one signal that raises the ceiling rather
            # than lowering it, so it cannot go through _strictest, which would
            # always keep as_planned. It runs before every veto below, so any
            # of them still pulls the verdict back down.
            #
            # A layoff decays CTL and ATL alike, so TSB climbs into this band
            # precisely when there is no fitness left to spend: 65 of the 93
            # can_push days in this athlete's history were issued while CTL sat
            # below DETRAINED_FRACTION of its own 90-day peak, 2026-09-02 among
            # them. Freshness only earns a higher ceiling on top of fitness.
            fraction = load.get("ctl_fraction_of_peak")
            if fraction is not None and fraction < DETRAINED_FRACTION:
                reasons.append(
                    f"TSB {tsb:+} is in your fresh band, but CTL {load.get('ctl')} is "
                    f"only {fraction:.0%} of its 90-day peak ({load.get('ctl_peak_90d')}) "
                    f"— that is detrained, not rested. Rebuild before pushing."
                )
            else:
                verdict = "can_push"
                reasons.append(
                    f"TSB {tsb:+} is in your fresh band (above {bands['high']:+}), "
                    f"on CTL {load.get('ctl')} at {fraction:.0%} of its 90-day peak."
                )

    ramp = load.get("ctl_change_7d")
    if ramp is not None and ramp > SAFE_CTL_RAMP:
        verdict = _strictest(verdict, "easy_only")
        reasons.append(
            f"CTL climbed {ramp:+} in a week, past the {SAFE_CTL_RAMP:g} that is "
            f"absorbable — the load is arriving faster than the adaptation."
        )

    # Garmin's own read of the night. It sees sleep and overnight HRV, which
    # nothing in the activity log can infer.
    level = (wellness.get("readiness_level") or "").upper()
    if level == "POOR":
        verdict = _strictest(verdict, "mobility_only")
        reasons.append(
            f"Garmin readiness POOR (score {wellness.get('readiness_score')}"
            + (f", {wellness['readiness_feedback'].replace('_', ' ').lower()}"
               if wellness.get("readiness_feedback") else "") + ")."
        )
    elif level == "LOW":
        verdict = _strictest(verdict, "easy_only")
        reasons.append(f"Garmin readiness LOW (score {wellness.get('readiness_score')}).")

    if (wellness.get("hrv_status") or "").upper() == "UNBALANCED":
        verdict = _strictest(verdict, "easy_only")
        reasons.append(
            f"HRV unbalanced — {wellness.get('hrv_last_night')} ms against a "
            f"baseline floor of {wellness.get('hrv_baseline_low')}."
        )

    recovery = wellness.get("recovery_time_min")
    if recovery and recovery > 1440:
        verdict = _strictest(verdict, "mobility_only")
        reasons.append(f"Garmin still wants {recovery // 60} h of recovery.")

    if not reasons:
        reasons.append("Nothing in load or wellness argues against the plan.")

    return {
        "date": day,
        "verdict": verdict,
        "reasons": reasons,
        "planned_today": slots,
        "load": load,
        "wellness": wellness,
    }


if __name__ == "__main__":
    result = assess()
    load = result["load"]
    print(f"{result['date']}  verdict: {result['verdict'].replace('_', ' ').upper()}")
    print(f"  CTL {load.get('ctl')}  ATL {load.get('atl')}  TSB {load.get('tsb'):+} "
          f"({load.get('state')})")
    if result["planned_today"]:
        for slot in result["planned_today"]:
            print(f"  planned: {slot['session_type']} - {slot.get('focus', '')}")
    else:
        print("  planned: nothing today")
    for reason in result["reasons"]:
        print(f"  - {reason}")
