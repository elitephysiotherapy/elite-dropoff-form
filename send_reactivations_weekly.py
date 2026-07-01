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
import reactivations
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
    """Return list of dicts (one per reactivated patient, sorted by booking time).

    Uses the canonical reactivation engine (reactivations.py) — the single source
    of truth shared with the EOD number and the bookings sheet. A reactivation is
    counted when a drop-off patient's FIRST rebooking was created in this window.
    """
    s_iso, e_iso = eod_stats._iso(start_local), eod_stats._iso(end_local)
    now = datetime.now(LONDON)

    # Candidates = everyone who created an appointment in the window. Must include
    # CANCELLED bookings too (Cliniko's default list hides them), else a patient
    # whose reactivation booking was later cancelled is silently dropped.
    created = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={s_iso}"), ("q[]", f"created_at:<{e_iso}")]))
    created += list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={s_iso}"), ("q[]", f"created_at:<{e_iso}"),
        ("q[]", "cancelled_at:?")]))
    cand_pids = {phase2.id_from_link(a.get("patient")) for a in created}
    cand_pids.discard(None)

    type_id_to_name = {str(t["id"]): t.get("name", "?")
                       for t in phase2.fetch_all("/appointment_types", [])}

    out = []
    for pid in cand_pids:
        hist = phase2.fetch_patient_full_history(pid)
        for r in reactivations.reactivations_in_window(hist, start_local, end_local, now):
            rb = r["rebook"]
            tid = phase2.id_from_link(rb.get("appointment_type")) or ""
            cr = rb.get("created_at") or ""
            out.append({
                "patient": (rb.get("patient_name") or "").strip() or pid,
                "booked_at": cr[:16].replace("T", " "),
                "starts_at": (rb.get("starts_at") or "")[:16].replace("T", " "),
                "appt_type": type_id_to_name.get(str(tid), "?"),
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
