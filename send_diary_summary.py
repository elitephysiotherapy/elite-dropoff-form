"""Diary summary per physio → Slack DMs to Sinead Rocks, Rox and Reception.

Sinead and the front desk can't see Cliniko's per-practitioner reports on
their logins, so this reads the diary through the API and DMs them, for each
physio, how many IAs, classes and total appointments are booked for a week.

Schedule (Europe/London, via run_cloud.py):
  Mon 08:00  this week's diary
  Wed 09:00  mid-week update
  Fri 08:00  this week's wrap-up, then next week's diary so reception can
             balance it (two separate DMs)

What each number means:
  IAs      the 4 real IA types (config.PHASE1_DROPOFF_IA_TYPE_IDS), the same
           definition as the EOD report and its IA targets.
  1:1      individual appointments, not cancelled and not DNA'd (so for past
           days it's who actually came, and for future days who's booked).
           Class, event and Recovery Suite types (EXCLUDED_FROM_TOTAL_APPTS)
           are left out.
  Pilates  Cliniko group appointments whose type name contains "Pilates".
  Rehab    every other class: group appointments that aren't Pilates.
  Total    1:1 + Pilates + Rehab. A class counts once per session, not once
           per attendee — "2 Pilates classes" means two sessions on the diary.

Modes:
  python send_diary_summary.py                      preview (auto: this week,
                                                    + next week on Fridays)
  python send_diary_summary.py --week next          preview one week
  python send_diary_summary.py --post               send the DMs (the cron)

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DMs go to the CEO instead.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import phase2
import slack_notifier

LONDON = ZoneInfo("Europe/London")


def week_window(which, now=None):
    """(monday, next_monday) as dates for this week or next week."""
    now = now or datetime.now(LONDON)
    monday = now.date() - timedelta(days=now.weekday())
    if which == "next":
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=7)


def _utc_iso(d):
    """London midnight at the start of date d, as a Cliniko UTC timestamp."""
    dt = datetime(d.year, d.month, d.day, tzinfo=LONDON).astimezone(ZoneInfo("UTC"))
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _member_by_practitioner_id():
    return {pid: m for m in config.TEAM for pid in m["practitioner_ids"]}


def _name_of(pid, members, pracs):
    """Physio's name: their roster name (secondary 'CS' profiles fold in),
    else whatever Cliniko calls them (e.g. a locum not on the roster)."""
    m = members.get(pid)
    if m:
        return m["full_names"][0]
    p = pracs.get(pid)
    if p:
        return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return "Unassigned"


def diary_counts(monday, next_monday):
    """{physio name: {"ias", "one2one", "pilates", "rehab"}} for the week."""
    lo, hi = _utc_iso(monday), _utc_iso(next_monday)
    members = _member_by_practitioner_id()
    pracs = phase2.all_practitioners()
    counts = defaultdict(lambda: {"ias": 0, "one2one": 0, "pilates": 0, "rehab": 0})

    # Cliniko's default listing already leaves out cancelled appointments.
    for a in phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={lo}"), ("q[]", f"starts_at:<{hi}"),
    ]):
        if a.get("did_not_arrive") or a.get("cancelled_at"):
            continue
        type_id = phase2.id_from_link(a.get("appointment_type"))
        if type_id in config.EXCLUDED_FROM_TOTAL_APPTS:
            continue
        c = counts[_name_of(phase2.id_from_link(a.get("practitioner")), members, pracs)]
        c["one2one"] += 1
        if type_id in config.PHASE1_DROPOFF_IA_TYPE_IDS:
            c["ias"] += 1

    type_names = {str(t["id"]): t.get("name", "")
                  for t in phase2.fetch_all("/appointment_types")}
    for g in phase2.fetch_all("/group_appointments", [
        ("q[]", f"starts_at:>={lo}"), ("q[]", f"starts_at:<{hi}"),
    ]):
        if g.get("cancelled_at") or g.get("archived_at") or g.get("deleted_at"):
            continue
        type_name = type_names.get(phase2.id_from_link(g.get("appointment_type")), "")
        c = counts[_name_of(phase2.id_from_link(g.get("practitioner")), members, pracs)]
        c["pilates" if "pilates" in type_name.lower() else "rehab"] += 1

    return counts


def _roster_rows(counts, monday, next_monday):
    """Every physio on the team that week (roster order), plus anyone else
    with appointments. Anyone on leave all week is marked rather than shown
    as a physio with an empty diary."""
    rows, seen = [], set()
    for m in config.TEAM:
        if not config.is_active_in_period(m, monday, next_monday - timedelta(days=1)):
            continue
        name = m["full_names"][0]
        seen.add(name)
        on_leave = config.on_leave_whole_period(m, monday, next_monday)
        if on_leave and name not in counts:
            rows.append((name, None))
        else:
            rows.append((name, counts.get(name)))
    for name in sorted(counts):
        if name not in seen:
            rows.append((name, counts[name]))
    return rows


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "es" if word.endswith("s") else "s")


def build_message(which, monday, next_monday, counts, now=None):
    now = now or datetime.now(LONDON)
    wc = monday.strftime("%a %-d %b")
    if which == "next":
        title = f"Next week's diary: w/c {wc}"
        note = "Booked for next week. Use it to balance the diaries before Monday."
    elif now.weekday() == 4:
        title = f"This week's wrap-up: w/c {wc}"
        note = "Mon–Thu = patients who came (DNAs left out) · Fri = booked today."
    else:
        title = f"This week's diary: w/c {wc}"
        note = "Past days = patients who came (DNAs left out) · rest of week = booked."

    lines = [f"*📅 {title}*", f"_{note}_", ""]
    tot = {"ias": 0, "one2one": 0, "pilates": 0, "rehab": 0}
    for name, c in _roster_rows(counts, monday, next_monday):
        if c is None:
            lines.append(f"• *{name}*: on leave")
            continue
        for k in tot:
            tot[k] += c[k]
        total = c["one2one"] + c["pilates"] + c["rehab"]
        parts = [_plural(c["ias"], "IA"), f"{total} total appts"]
        if c["pilates"]:
            parts.append(_plural(c["pilates"], "Pilates class"))
        if c["rehab"]:
            parts.append(_plural(c["rehab"], "rehab class"))
        lines.append(f"• *{name}*: " + " | ".join(parts))

    grand = tot["one2one"] + tot["pilates"] + tot["rehab"]
    lines += ["", f"*Team total:* {_plural(tot['ias'], 'IA')} | {grand} total appts"
              f" ({tot['one2one']} 1:1, {tot['pilates']} Pilates, {tot['rehab']} rehab classes)",
              "_IAs = Initial, Club, Private Health Insurance and ACL IAs. "
              "A class counts once per session, not per person._"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", choices=["this", "next", "auto"], default="auto",
                    help="auto (the cron) = this week, plus next week on Fridays")
    ap.add_argument("--post", action="store_true", help="send the DMs (default: preview)")
    args = ap.parse_args()

    if args.week == "auto":
        weeks = ["this", "next"] if datetime.now(LONDON).weekday() == 4 else ["this"]
    else:
        weeks = [args.week]

    for which in weeks:
        monday, next_monday = week_window(which)
        text = build_message(which, monday, next_monday, diary_counts(monday, next_monday))
        print(text + "\n")
        if args.post:
            slack_notifier._send_dm_to_recipients(
                config.DIARY_SUMMARY_SLACK_EMAILS, text,
                target_label=f"Diary summary ({which} week)")
    if not args.post:
        print("(preview only — add --post to send)")


if __name__ == "__main__":
    main()
