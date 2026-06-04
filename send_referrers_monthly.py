"""Monthly referrer analysis Slack DM to Sinead Rocks (runs on the 1st).

Sinead wants a monthly read on who the clinic's best referrers are. The data
lives in the bookings Google Sheet (NOT the drop-off sheet): every booked IA row
carries a "Referrer" column (filled from reception's `Ref: …` booking note, or
"Online" for self-bookings). This script aggregates that column across the
previous calendar month and DMs Sinead a ranked list.

Scope: booked appointments only (the W/C weekly tabs) — the Leads tab is excluded.

Modes:
  python send_referrers_monthly.py            preview only — prints the DM
  python send_referrers_monthly.py --post     send the DM to Sinead

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DM is rerouted to the CEO
with a "[TEST → …]" prefix (handled by slack_notifier._send_dm).
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import bookings_fetch as bk

LONDON = ZoneInfo("Europe/London")

# Sinead Rocks (Ops Manager) — same address used for the drop-off Ops digest.
SINEAD_EMAIL = "sinead@elitephysiocookstown.co.uk"


def previous_month_window(now=None):
    """(start_local, end_local) for the previous calendar month, Europe/London."""
    if now is None:
        now = datetime.now(LONDON)
    end = datetime(now.year, now.month, 1, tzinfo=LONDON)
    if end.month == 1:
        start = datetime(end.year - 1, 12, 1, tzinfo=LONDON)
    else:
        start = datetime(end.year, end.month - 1, 1, tzinfo=LONDON)
    return start, end


def _parse_dt(s):
    """Parse a bookings-sheet date cell ('YYYY-MM-DD HH:MM' or 'YYYY-MM-DD')."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _tab_sunday(title):
    """Parse the Sunday date out of a 'W/C DD Mon YYYY' tab title (or None)."""
    try:
        return datetime.strptime(title.replace("W/C ", "").strip(), "%d %b %Y")
    except ValueError:
        return None


def collect_referrers(start_local, end_local):
    """Return (counts, total_with_ref, total_rows) for booked IAs whose
    Appointment Date falls in [start_local, end_local). counts: referrer → n."""
    sh = bk.open_spreadsheet()
    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    counts = defaultdict(int)
    display_name = {}          # lowercased key → first-seen original spelling
    total_with_ref = 0
    total_rows = 0

    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        sunday = _tab_sunday(ws.title)
        # A Sunday-anchored week spans Sunday..Saturday; only read tabs that can
        # overlap the target month (cheap filter to avoid reading every tab).
        if sunday is not None and not (
                start_naive - timedelta(days=8) <= sunday <= end_naive):
            continue
        try:
            values = ws.get_all_values()
        except Exception as e:
            print(f"  WARN couldn't read {ws.title}: {e}")
            continue
        if not values:
            continue
        header = values[0]
        try:
            ref_i = header.index("Referrer")
            appt_i = header.index("Appointment Date")
        except ValueError:
            continue
        booked_i = header.index("Date Booked") if "Date Booked" in header else None

        for row in values[1:]:
            if len(row) <= ref_i:
                continue
            dt = _parse_dt(row[appt_i] if len(row) > appt_i else "")
            if dt is None and booked_i is not None and len(row) > booked_i:
                dt = _parse_dt(row[booked_i])       # fall back to Date Booked
            if dt is None or not (start_naive <= dt < end_naive):
                continue
            total_rows += 1
            ref = (row[ref_i] or "").strip()
            if not ref:
                continue
            total_with_ref += 1
            key = ref.lower()
            display_name.setdefault(key, ref)
            counts[key] += 1

    named = {display_name[k]: n for k, n in counts.items()}
    return named, total_with_ref, total_rows


def build_dm_text(counts, total_with_ref, total_rows, month_label):
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    lines = [
        f"Good morning Sinead,",
        "",
        f"*Referrer analysis — {month_label}*",
        f"(Based on booked appointments in the bookings sheet, by appointment date.)",
        "",
    ]
    if not ranked:
        lines.append("No referrers were recorded for booked appointments last month.")
    else:
        lines.append(f"Top referrers ({total_with_ref} of {total_rows} bookings had a "
                     f"referrer recorded):")
        lines.append("")
        for i, (ref, n) in enumerate(ranked, 1):
            share = (n / total_with_ref * 100) if total_with_ref else 0
            lines.append(f"  {i}. {ref} — {n} ({share:.0f}%)")
        no_ref = total_rows - total_with_ref
        if no_ref:
            lines.append("")
            lines.append(f"⚠️ {no_ref} booking(s) had no referrer logged — these are "
                         f"excluded. Reception adds the referrer via `Ref: …` in the "
                         f"Cliniko booking note, so coverage depends on that being filled.")
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv
    start_local, end_local = previous_month_window()
    month_label = start_local.strftime("%B %Y")
    print(f"Building referrer analysis for {month_label}…", flush=True)

    counts, total_with_ref, total_rows = collect_referrers(start_local, end_local)
    text = build_dm_text(counts, total_with_ref, total_rows, month_label)
    print()
    print(f"--- Referrer analysis → Sinead ({SINEAD_EMAIL}) ---")
    print(text)
    print()

    if not post:
        print("(Preview only — re-run with --post to send to Sinead.)")
        return

    import slack_notifier
    ok = slack_notifier._send_dm(
        SINEAD_EMAIL, text,
        target_label=f"Monthly referrer analysis ({month_label})",
    )
    print("Sent." if ok else "FAILED to send.")


if __name__ == "__main__":
    main()
