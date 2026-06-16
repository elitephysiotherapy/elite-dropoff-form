"""Weekly reactivations → Slack DM to Sinead Rocks (Monday mornings, 8am London).

Same "reactivation" definition as eod_stats.reactivations() — a patient who
has appeared on any historical drop-off W/C tab AND created a new appointment
in the previous Mon 08:00 → Mon 08:00 window AND did not cancel anything
that week (which would make it a reschedule, not a true reactivation).

Output: Slack DM to Sinead Rocks with a numbered patient list, each row
carrying:
  - Patient name
  - Booking timestamp
  - New appointment date/time
  - Appointment type

Modes:
  python send_reactivations_weekly.py            preview only — prints the DM
  python send_reactivations_weekly.py --post     send the DM to Sinead

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DM is rerouted to the CEO.
"""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import eod_stats
import phase2
import slack_notifier

LONDON = ZoneInfo("Europe/London")
SINEAD_EMAIL = "sinead@elitephysiocookstown.co.uk"


def previous_week_window(now=None):
    """(start_local, end_local) for the previous Mon 08:00 → this Mon 08:00.

    Uses an 08:00 boundary (not midnight) so the window matches what
    eod_stats.reactivations() already counts during the week. Patients
    booked overnight Sun→Mon morning land in *next* week's report.
    """
    if now is None:
        now = datetime.now(LONDON)
    today_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
    this_monday_8am = today_8am - timedelta(days=today_8am.weekday())
    last_monday_8am = this_monday_8am - timedelta(days=7)
    return last_monday_8am, this_monday_8am


def collect_reactivations(start_local, end_local):
    """Return list of dicts (one per reactivated patient, sorted by booking time)."""
    dropoffs = eod_stats.dropoff_patient_ids()
    created = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={eod_stats._iso(start_local)}"),
        ("q[]", f"created_at:<{eod_stats._iso(end_local)}"),
    ]))
    cancelled = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"cancelled_at:>={eod_stats._iso(start_local)}"),
        ("q[]", f"cancelled_at:<{eod_stats._iso(end_local)}"),
    ]))
    rescheduled = {phase2.id_from_link(a.get("patient")) for a in cancelled}

    # First new booking per reactivated patient (earliest within the window)
    by_pid: dict[str, dict] = {}
    for a in created:
        pid = phase2.id_from_link(a.get("patient"))
        if not pid or pid not in dropoffs or pid in rescheduled:
            continue
        if pid not in by_pid or a.get("created_at","") < by_pid[pid].get("created_at",""):
            by_pid[pid] = a

    # Resolve appointment type names + patient names
    type_id_to_name = {str(t["id"]): t.get("name", "?")
                       for t in phase2.fetch_all("/appointment_types", [])}

    out = []
    for pid, a in by_pid.items():
        # Name comes straight off the appointment object — Cliniko returns
        # patient_name inline, so there's no need for a per-patient GET. The
        # old GET-per-patient was getting rate-limited (429) after the first
        # few, which left raw patient IDs in the list instead of names.
        name = (a.get("patient_name") or "").strip() or pid
        tid = phase2.id_from_link(a.get("appointment_type")) or ""
        out.append({
            "patient": name,
            "booked_at": (a.get("created_at") or "")[:16].replace("T", " "),
            "starts_at": (a.get("starts_at") or "")[:16].replace("T", " "),
            "appt_type": type_id_to_name.get(tid, "?"),
        })
    out.sort(key=lambda r: r["booked_at"])
    return out


def build_dm_text(rows, week_start, week_end):
    week_label = f"W/C {week_start.strftime('%d %b %Y')}"
    span = (f"{week_start.strftime('%a %d %b')} 08:00 – "
            f"{(week_end - timedelta(days=1)).strftime('%a %d %b')}")

    lines = [
        "Good morning Sinead,",
        "",
        f"*Patient reactivations — {week_label}*  ({span})",
        "",
        f"• Total: *{len(rows)}*",
    ]
    if rows:
        lines.append("")
        lines.append("```")
        # Compact monospace table
        lines.append(f"{'#':>2}  {'Patient':<26}  {'Booked':16}  {'New appt':16}  Type")
        lines.append("-" * 110)
        for i, r in enumerate(rows, 1):
            lines.append(
                f"{i:>2}  {r['patient'][:26]:<26}  {r['booked_at']:16}  "
                f"{r['starts_at']:16}  {r['appt_type'][:40]}")
        lines.append("```")
    else:
        lines.append("")
        lines.append("No reactivations recorded last week.")
    lines.append("")
    lines.append("_A reactivation is a patient who's appeared on a drop-off W/C tab AND "
                 "created a new appointment last week without cancelling anything "
                 "(cancellations + rebooks count as reschedules, not reactivations)._")
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv
    week_start, week_end = previous_week_window()
    print(f"Collecting reactivations for W/C {week_start.strftime('%d %b %Y')}…",
          flush=True)

    rows = collect_reactivations(week_start, week_end)
    text = build_dm_text(rows, week_start, week_end)

    print()
    print(f"--- Weekly reactivations → Sinead ({SINEAD_EMAIL}) ---")
    print(text)
    print()

    if not post:
        print("(Preview only — re-run with --post to send to Sinead.)")
        return

    ok = slack_notifier._send_dm(
        SINEAD_EMAIL, text,
        target_label=f"Weekly reactivations (W/C {week_start.strftime('%d %b %Y')})",
    )
    print("Sent." if ok else "FAILED to send.")


if __name__ == "__main__":
    main()
