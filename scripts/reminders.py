#!/usr/bin/env python3
"""Push planned sessions into Apple Reminders, so the plan reaches the phone.

Reminders has no HTTP API — the only way in is AppleScript against the local
Reminders app, which then syncs the list through whatever account owns it. The
lists on this Mac live on a CalDAV account (cloud.easecrafted.com), not iCloud,
so the same account must be configured on the iPhone for anything to arrive.

The loop closes by itself: a planned slot becomes a reminder, and once Garmin
syncs a matching activity that day the reminder is ticked off automatically.
Nothing is ever completed by hand on either side.

Usage:
    python3 scripts/reminders.py                    # sync the next 21 days
    python3 scripts/reminders.py --dry-run          # show what would change
    python3 scripts/reminders.py --list "Sport Activity" --time 17:00
"""
import argparse
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import get_plan_connection, plan_with_status

DEFAULT_LIST = "Sport Activity"
DEFAULT_TIME = "17:00"

# A reminder alerting at the same hour every time is easy to ignore, but the
# plan is day-resolution, so one configurable default time is all there is.
SESSION_LABEL = {
    "cycling": "Bike",
    "running": "Run",
    "mobility": "Mobility",
}


def _escape(text: str) -> str:
    """Quote a Python string for literal inclusion in AppleScript source."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _run_applescript(source: str) -> str:
    result = subprocess.run(
        ["osascript", "-"],
        input=source,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"AppleScript failed: {result.stderr.strip()}")
    return result.stdout.strip()


# Field/record separators for reading reminders back. A note can contain tabs
# and newlines, so the record separator has to be something a human will not
# type into a training note.
FS = "\t"
RS = "@@REC@@"


def fetch_reminders(list_name: str) -> dict:
    """{id: {name, body, due}} for the list, so the sync can skip no-op writes.

    Two jobs: a reminder the user swiped away on the phone is missing here, so
    its slot gets recreated rather than silently updated into the void; and
    rewriting a reminder whose fields already match would bump its modification
    date and push pointless CalDAV traffic to the phone on every scheduled run.
    """
    output = _run_applescript(
        f'''
        on pad(n)
            set s to (n as integer) as string
            if length of s < 2 then set s to "0" & s
            return s
        end pad

        tell application "Reminders"
            set out to ""
            repeat with theReminder in (every reminder of list "{_escape(list_name)}")
                set theBody to body of theReminder
                if theBody is missing value then set theBody to ""
                set theDue to due date of theReminder
                if theDue is missing value then
                    set dueText to ""
                else
                    set dueText to ((year of theDue) as string) & "-" & my pad(month of theDue) ¬
                        & "-" & my pad(day of theDue) & " " & my pad(hours of theDue) ¬
                        & ":" & my pad(minutes of theDue)
                end if
                set out to out & (id of theReminder) & "{FS}" & (name of theReminder) ¬
                    & "{FS}" & theBody & "{FS}" & dueText & "{RS}"
            end repeat
            return out
        end tell
        '''
    )
    reminders = {}
    for record in output.split(RS):
        if not record.strip():
            continue
        reminder_id, name, body, due = record.split(FS)
        reminders[reminder_id] = {"name": name, "body": body, "due": due}
    return reminders


def title_for(entry: dict) -> str:
    """e.g. 'Bike (long) — 32 km, zone 2'. Readable at a glance on a lock screen."""
    label = SESSION_LABEL.get(entry["session_type"], entry["session_type"].title())
    focus = entry.get("focus")
    if focus:
        label = f"{label} — {focus}"
    detail = []
    if entry.get("target_distance_km"):
        detail.append(f"{entry['target_distance_km']:g} km")
    if entry.get("target_duration_min"):
        detail.append(f"{entry['target_duration_min']:g} min")
    if entry.get("intensity") and entry["intensity"] not in (focus or ""):
        detail.append(entry["intensity"])
    return f"{label} ({', '.join(detail)})" if detail else label


def body_for(entry: dict) -> str:
    return entry.get("notes") or ""


def _differs(entry: dict, current: dict, hour: int, minute: int) -> bool:
    """Whether the reminder needs rewriting to match the plan."""
    due = f"{entry['date']} {hour:02d}:{minute:02d}"
    return (
        current["name"] != title_for(entry)
        or current["body"] != body_for(entry)
        or current["due"] != due
    )


def build_script(list_name: str, hour: int, minute: int, creates, updates,
                 completes, deletions) -> str:
    """One AppleScript for the whole sync — launching osascript per slot is slow.

    Created reminders report their new id back on stdout, keyed by date and
    session type, so the caller can store the link.
    """
    lines = [
        "on mkdate(y, m, d, hh, mm)",
        "    set theDate to current date",
        "    set day of theDate to 1",  # avoid overflow when the month changes
        "    set year of theDate to y",
        "    set month of theDate to m",
        "    set day of theDate to d",
        "    set time of theDate to (hh * hours + mm * minutes)",
        "    return theDate",
        "end mkdate",
        "",
        'tell application "Reminders"',
        f'    set theList to list "{_escape(list_name)}"',
        "    set out to {}",
    ]

    for reminder_id in deletions:
        lines += [
            "    try",
            f'        delete (first reminder of theList whose id is "{_escape(reminder_id)}")',
            "    end try",
        ]

    for entry in creates:
        y, m, d = entry["date"].split("-")
        lines += [
            f"    set dueDate to my mkdate({int(y)}, {int(m)}, {int(d)}, {hour}, {minute})",
            "    set newReminder to make new reminder at end of theList with properties "
            f'{{name:"{_escape(title_for(entry))}", body:"{_escape(body_for(entry))}", due date:dueDate}}',
            f'    set end of out to "{entry["date"]}\\t{entry["session_type"]}\\t" & (id of newReminder)',
        ]

    for entry in updates:
        y, m, d = entry["date"].split("-")
        lines += [
            "    try",
            f'        set theReminder to (first reminder of theList whose id is "{_escape(entry["reminder_id"])}")',
            f"        set dueDate to my mkdate({int(y)}, {int(m)}, {int(d)}, {hour}, {minute})",
            f'        set name of theReminder to "{_escape(title_for(entry))}"',
            f'        set body of theReminder to "{_escape(body_for(entry))}"',
            "        set due date of theReminder to dueDate",
            "    end try",
        ]

    for entry in completes:
        lines += [
            "    try",
            f'        set completed of (first reminder of theList whose id is "{_escape(entry["reminder_id"])}") to true',
            "    end try",
        ]

    lines += [
        "    set AppleScript's text item delimiters to linefeed",
        "    return out as string",
        "end tell",
    ]
    return "\n".join(lines)


def sync(list_name=DEFAULT_LIST, due_time=DEFAULT_TIME, days_ahead=21,
         dry_run=False) -> dict:
    hour, _, minute = due_time.partition(":")
    hour, minute = int(hour), int(minute or 0)

    conn = get_plan_connection()
    today = _date.today()
    end = _date.fromordinal(today.toordinal() + days_ahead)
    # Reach a little into the past so a session completed yesterday still gets
    # its reminder ticked off rather than lingering unchecked on the phone.
    start = _date.fromordinal(today.toordinal() - 7)
    plan = plan_with_status(conn, start.isoformat(), end.isoformat())

    live = fetch_reminders(list_name)
    deletions = [
        row[0] for row in conn.execute("SELECT reminder_id FROM retired_reminders")
        if row[0] in live
    ]

    creates, updates, completes = [], [], []
    for entry in plan:
        reminder_id = entry.get("reminder_id")
        linked = reminder_id in live if reminder_id else False
        if entry["status"] == "done":
            # Past slots that were never pushed stay unpushed — creating a
            # reminder just to tick it off would be noise on the phone.
            if linked:
                completes.append(entry)
            continue
        if entry["date"] < today.isoformat():
            continue  # a missed day needs no future alert
        if not linked:
            creates.append(entry)
        elif _differs(entry, live[reminder_id], hour, minute):
            updates.append(entry)

    report = {
        "created": [title_for(e) for e in creates],
        "updated": [title_for(e) for e in updates],
        "completed": [title_for(e) for e in completes],
        "deleted": len(deletions),
    }
    if dry_run or not (creates or updates or completes or deletions):
        conn.close()
        return report

    output = _run_applescript(
        build_script(list_name, hour, minute, creates, updates, completes, deletions)
    )
    cur = conn.cursor()
    for line in output.splitlines():
        if not line.strip():
            continue
        day, session_type, reminder_id = line.split("\t")
        cur.execute(
            "UPDATE planned_sessions SET reminder_id = ? "
            "WHERE date = ? AND session_type = ?",
            (reminder_id, day, session_type),
        )
    if deletions:
        cur.executemany(
            "DELETE FROM retired_reminders WHERE reminder_id = ?",
            [(r,) for r in deletions],
        )
    conn.commit()
    conn.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", default=DEFAULT_LIST, dest="list_name",
                        help=f"Reminders list to write to (default: {DEFAULT_LIST})")
    parser.add_argument("--time", default=DEFAULT_TIME, dest="due_time",
                        help=f"due time for each reminder (default: {DEFAULT_TIME})")
    parser.add_argument("--days", type=int, default=21, dest="days_ahead",
                        help="how far ahead to push the plan (default: 21)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching Reminders")
    args = parser.parse_args()

    report = sync(args.list_name, args.due_time, args.days_ahead, args.dry_run)
    prefix = "Would " if args.dry_run else ""
    for action in ("created", "updated", "completed"):
        for title in report[action]:
            verb = action[:-1] if args.dry_run else action
            print(f"  {prefix}{verb}: {title}")
    if report["deleted"]:
        print(f"  {prefix}delete{'' if args.dry_run else 'd'}: "
              f"{report['deleted']} retired reminder(s)")
    if not any(report[a] for a in ("created", "updated", "completed")) and not report["deleted"]:
        print("Reminders already match the plan.")
