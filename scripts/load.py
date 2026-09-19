#!/usr/bin/env python3
"""Training load: how much work went in, and how recovered you are from it.

There is no cycling power meter here and Garmin's training_stress_score column
is 0.0 on every row, so the usual power-based load model is unavailable. Heart
rate is present on 96% of activities, so load is Banister TRIMP instead —
duration weighted by heart-rate reserve, exponentially, so an hour hard counts
for far more than an hour easy.

From the daily TRIMP series:
    CTL  42-day exponential average — fitness, what the body is used to
    ATL   7-day exponential average — fatigue, what it is still paying off
    TSB  CTL - ATL, yesterday's — form; negative means buried

Thresholds are calibrated against this athlete's own history rather than
imported from cycling literature, which assumes a CTL of 60-100. Peak CTL here
is about 24, so textbook bands would call every hard week a catastrophe.

Usage:
    python3 scripts/load.py              # current state
    python3 scripts/load.py --days 30    # daily series
"""
import argparse
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_connection, parse_number

CTL_DAYS, ATL_DAYS = 42, 7
FALLBACK_REST_HR, FALLBACK_MAX_HR = 60, 190

# TRIMP reads a session as continuous aerobic work — minutes above resting,
# weighted exponentially by intensity. That assumption fails where heart rate
# is raised by being upright and moving rather than by training: a 47-minute
# mobility session at 103 bpm scored 20, and a two-hour holiday walk at 99 bpm
# scored 49, outscoring an 18 km gravel ride. Both fed ATL, and the verdict
# read "deeply fatigued" on a day when every wellness signal was green.
#
# plan.py already draws this line — mobility completes a mobility slot, while
# Walking and Multisport match nothing at all. These weights put load.py on the
# same side of it, rather than counting as fatigue what the plan refuses to
# count as training.
MOBILITY_WEIGHT, WALKING_WEIGHT = 0.3, 0.3
MOBILITY_TITLE = re.compile(r"mobility|pilates|stretch|yoga", re.IGNORECASE)

# Days this far from a session are not evidence about training form, so they
# are kept out of the band calibration below.
TRAINING_NEIGHBOURHOOD = 3

# CTL this far below its own 90-day peak means detrained, whatever TSB says.
DETRAINED_FRACTION = 0.70


def heart_rate_bounds(conn) -> tuple:
    """Resting and max HR measured from this athlete, not from a formula.

    Resting HR is the median of recent wellness readings rather than the
    minimum, so one freakishly low night cannot inflate every TRIMP since.
    """
    resting = [
        row[0] for row in conn.execute(
            "SELECT resting_hr FROM wellness WHERE resting_hr IS NOT NULL "
            "AND date >= date('now', '-90 days') ORDER BY resting_hr"
        )
    ] if _has_table(conn, "wellness") else []
    rest_hr = resting[len(resting) // 2] if resting else FALLBACK_REST_HR

    observed = [
        parse_number(row[0])
        for row in conn.execute("SELECT max_hr FROM activities WHERE max_hr != '--'")
    ]
    observed = [v for v in observed if isinstance(v, (int, float))]
    max_hr = int(max(observed)) if observed else FALLBACK_MAX_HR
    return rest_hr, max_hr


def _has_table(conn, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def duration_minutes(value: str) -> float:
    if not value or value == "--":
        return 0.0
    parts = [float(p) for p in value.split(":")]
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) / 60
    if len(parts) == 2:
        return (parts[0] * 60 + parts[1]) / 60
    return 0.0


def trimp(avg_hr, minutes: float, rest_hr: int, max_hr: int) -> float:
    """Banister TRIMP, male weighting factor.

    Sessions without heart rate score zero rather than guessing — better a
    known gap than a fabricated load.
    """
    if not avg_hr or minutes <= 0 or max_hr <= rest_hr:
        return 0.0
    reserve = (avg_hr - rest_hr) / (max_hr - rest_hr)
    reserve = max(0.0, min(1.0, reserve))
    return minutes * reserve * 0.64 * math.exp(1.92 * reserve)


def session_weight(activity_type: str, title: str) -> float:
    """How much of a session's TRIMP is training stress the body pays off.

    Keyed on the title rather than the type alone, because "Other" is the
    watch's catch-all: it holds the mobility work *and* the four "Kardio"
    sessions, which are real aerobic work and keep their full weight.
    """
    if activity_type == "Walking":
        return WALKING_WEIGHT
    if activity_type == "Other" and MOBILITY_TITLE.search(title or ""):
        return MOBILITY_WEIGHT
    return 1.0


def daily_loads(conn, rest_hr: int, max_hr: int) -> dict:
    """{date: total TRIMP} — several sessions in a day sum into that day."""
    loads = {}
    for day, duration, avg_hr, activity_type, title in conn.execute(
        "SELECT date, duration, avg_hr, activity_type, title FROM activities "
        "ORDER BY date"
    ):
        score = trimp(parse_number(avg_hr), duration_minutes(duration), rest_hr, max_hr)
        score *= session_weight(activity_type, title)
        if score:
            loads[day[:10]] = loads.get(day[:10], 0.0) + score
    return loads


def series(conn, until: str = "") -> list:
    """[{date, load, ctl, atl, tsb}] from the first activity to `until`.

    TSB uses the *previous* day's CTL and ATL, which is the convention: form is
    what you woke up with, before today's session is counted against it.
    """
    rest_hr, max_hr = heart_rate_bounds(conn)
    loads = daily_loads(conn, rest_hr, max_hr)
    if not loads:
        return []

    start = date.fromisoformat(min(loads))
    end = date.fromisoformat(until or date.today().isoformat())
    ctl = atl = 0.0
    out = []
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        load = loads.get(day, 0.0)
        out.append({
            "date": day,
            "load": round(load, 1),
            "tsb": round(ctl - atl, 1),
        })
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        out[-1]["ctl"] = round(ctl, 1)
        out[-1]["atl"] = round(atl, 1)
    return out


def _near_training(history: list, i: int) -> bool:
    """Is day `i` within TRAINING_NEIGHBOURHOOD days of an actual session?"""
    lo = max(0, i - TRAINING_NEIGHBOURHOOD)
    hi = min(len(history), i + TRAINING_NEIGHBOURHOOD + 1)
    return any(history[j]["load"] > 0 for j in range(lo, hi))


def calibrated_bands(history: list) -> dict:
    """TSB percentiles from this athlete's own last year of *training* days.

    Cycling's usual -30/-10/+5/+25 bands assume a CTL of 60-100. Reading this
    athlete's own distribution keeps "buried" meaning buried *for them*.

    Days more than TRAINING_NEIGHBOURHOOD from any session are dropped first.
    Without that filter 282 of the last 351 days were rest — idle runs of 41,
    36 and 27 days — so the percentiles described detraining, not training, and
    a tenth of every year scored "deeply fatigued" by construction, whatever
    the athlete did. The threshold moved with the athlete instead of anchoring
    them, which is the opposite of what a threshold is for.
    """
    start = max(0, len(history) - 365)
    values = sorted(
        entry["tsb"]
        for i, entry in enumerate(history)
        if i >= start and entry["ctl"] > 1 and _near_training(history, i)
    )
    if len(values) < 30:
        return {}

    def pct(p):
        return round(values[min(len(values) - 1, int(p * len(values)))], 1)

    return {"deep_low": pct(0.10), "low": pct(0.30), "high": pct(0.80)}


def current(conn) -> dict:
    """Today's load state, with the interpretation attached."""
    history = series(conn)
    if not history:
        return {"error": "No activities with heart rate to compute load from."}

    rest_hr, max_hr = heart_rate_bounds(conn)
    today = history[-1]
    bands = calibrated_bands(history)
    week = sum(e["load"] for e in history[-7:])
    prior_week = sum(e["load"] for e in history[-14:-7])
    ramp = round(today["ctl"] - history[-8]["ctl"], 1) if len(history) > 8 else None

    tsb = today["tsb"]
    if not bands:
        state = "unknown"
    elif tsb <= bands["deep_low"]:
        state = "deeply fatigued"
    elif tsb <= bands["low"]:
        state = "fatigued"
    elif tsb >= bands["high"]:
        state = "fresh"
    else:
        state = "normal"

    # TSB goes to zero after a layoff, because CTL and ATL both decay to zero —
    # so it reads "fresh" at the exact moment the body is least prepared. On
    # 2026-09-02, after 24 idle days, CTL was 5.3 and TSB +5.0: the fresh band,
    # can_push. The next day was a 78 km ride off a 50 km lifetime maximum, and
    # days two and three of the tour collapsed. CTL against its own recent peak
    # is the number that could tell detrained from fit; TSB structurally cannot.
    peak_90 = max(e["ctl"] for e in history[-90:])
    return {
        "date": today["date"],
        "ctl": today["ctl"], "atl": today["atl"], "tsb": tsb,
        "state": state,
        "bands": bands,
        "ctl_change_7d": ramp,
        "ctl_peak_90d": round(peak_90, 1),
        "ctl_fraction_of_peak": round(today["ctl"] / peak_90, 2) if peak_90 else None,
        "load_last_7d": round(week),
        "load_prior_7d": round(prior_week),
        "peak_ctl": max(e["ctl"] for e in history),
        "rest_hr": rest_hr, "max_hr": max_hr,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, help="print the daily series for the last N days")
    args = parser.parse_args()

    conn = get_connection()
    if args.days:
        print(f"{'date':12}{'load':>7}{'CTL':>7}{'ATL':>7}{'TSB':>8}")
        for entry in series(conn)[-args.days:]:
            print(f"{entry['date']:12}{entry['load']:7.0f}{entry['ctl']:7.1f}"
                  f"{entry['atl']:7.1f}{entry['tsb']:+8.1f}")
    else:
        state = current(conn)
        if "error" in state:
            print(state["error"])
        else:
            print(f"{state['date']}  CTL {state['ctl']}  ATL {state['atl']}  "
                  f"TSB {state['tsb']:+}  -> {state['state']}")
            print(f"  load 7d {state['load_last_7d']} (prior 7d {state['load_prior_7d']}), "
                  f"CTL change 7d {state['ctl_change_7d']:+}")
            print(f"  bands from own history: {state['bands']}  peak CTL {state['peak_ctl']}")
            print(f"  HR {state['rest_hr']}-{state['max_hr']}")
    conn.close()
