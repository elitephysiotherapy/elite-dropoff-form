"""End-of-day stats report.

Posts a ready-to-paste stats table to the #eod-claude Slack channel several
times a day (see config.EOD_REPORT_TIMES), so reception can drop it straight
into their end-of-shift handover email — replacing the manual tally today.

Every figure is an aggregate count. No patient data and no AI leave the clinic.

Sources:
  Cliniko                  Total Appts, IA counts, Reschedules, CDNR
  Drop-off master sheet    Reactivations (a drop-off patient rebooked today)
  Bookings sheet Leads tab Leads not booked
  Bookwhen iCal feed       Pilates class numbers

Time windows:
  This week / Next week    Mon-Sun, current and next
  Reschedules / CDNR       since the previous working day's final shift
  Reactivations            week-to-date — since Monday 08:00

Usage:
  python eod_stats.py            # preview — prints the table, posts nothing
  python eod_stats.py --post     # post the table to Slack
"""

import os
import re
import sys
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import phase2          # Cliniko client: fetch_all, parse_iso, id_from_link
import config

load_dotenv(override=True)

LONDON = ZoneInfo("Europe/London")
ISO = "%Y-%m-%dT%H:%M:%SZ"


# ===========================================================================
# Time windows
# ===========================================================================

def _utc(dt_london):
    return dt_london.astimezone(timezone.utc)


def _iso(dt_london):
    return _utc(dt_london).strftime(ISO)


def week_bounds(now):
    """Return (this_monday, next_monday, week_after) as London-local dates."""
    today = now.date()
    this_mon = today - timedelta(days=today.weekday())
    return this_mon, this_mon + timedelta(days=7), this_mon + timedelta(days=14)


def _midnight(d):
    """London-aware midnight at the start of date d."""
    return datetime(d.year, d.month, d.day, tzinfo=LONDON)


def reschedule_window_start(now):
    """London datetime of the previous working day's FINAL stat collection.

    Walks back day by day from yesterday to the first day with report times —
    so a Monday report reaches back to Friday's last collection and covers the
    weekend.
    """
    d = now.date() - timedelta(days=1)
    for _ in range(14):
        times = config.EOD_REPORT_TIMES.get(d.weekday())
        if times:
            hh, mm = (int(x) for x in times[-1].split(":"))
            return datetime(d.year, d.month, d.day, hh, mm, tzinfo=LONDON)
        d -= timedelta(days=1)
    return _midnight(now.date())   # fallback — should never hit


# ===========================================================================
# Cliniko — appointment counts
# ===========================================================================

def _clinic_of(appt):
    bid = phase2.id_from_link(appt.get("business"))
    return config.CLINIKO_BUSINESS_TO_CLINIC.get(str(bid))


def appointment_counts(this_mon, next_mon, week_after):
    """Total appts and IA counts per clinic, for this week and next week.

    Cliniko's default /individual_appointments query already excludes cancelled
    appointments. We additionally exclude DNAs (did_not_arrive) from the TOTAL,
    so 'Total Appts' = appointments attended OR still booked-in (matches the
    number Cliniko's own appointments report shows). DNAs cannot occur on future
    appointments, so this only trims this-week's past portion. (IA counts are
    left as-is — they count IAs booked regardless of attendance.)
    """
    appts = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={_iso(_midnight(this_mon))}"),
        ("q[]", f"starts_at:<{_iso(_midnight(week_after))}"),
    ]))

    out = {"total": {}, "ias": {}}   # out[kind][(clinic, 'this'|'next')] = count
    for a in appts:
        clinic = _clinic_of(a)
        if not clinic:
            continue
        starts = phase2.parse_iso(a.get("starts_at"))
        if not starts:
            continue
        d = starts.astimezone(LONDON).date()
        when = "this" if d < next_mon else "next"
        type_id = phase2.id_from_link(a.get("appointment_type"))

        if type_id not in config.EXCLUDED_FROM_TOTAL_APPTS and not a.get("did_not_arrive"):
            out["total"][(clinic, when)] = out["total"].get((clinic, when), 0) + 1
        if type_id in config.EOD_IA_TYPE_IDS:
            out["ias"][(clinic, when)] = out["ias"].get((clinic, when), 0) + 1
    return out


# ===========================================================================
# Cliniko — reschedules and CDNR (cancellations since the last shift)
# ===========================================================================

def _has_future_booking(patient_id, after_iso):
    """True if the patient has any non-cancelled appointment after after_iso."""
    if not patient_id:
        return False
    for _ in phase2.fetch_all("/individual_appointments", [
        ("q[]", f"patient_id:={patient_id}"),
        ("q[]", f"starts_at:>{after_iso}"),
    ]):
        return True
    return False


def cancellation_stats(window_start, now):
    """Count reschedules vs CDNR for appointments cancelled in the window.

    A cancelled appointment is a RESCHEDULE if the patient still has a future
    booking, otherwise it is a CDNR (cancelled, did not rebook — this naturally
    folds in IADNR, whose cancelled follow-up has no future booking).

    Returns (reschedules, cdnr, reschedule_patient_ids).
    """
    cancelled = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"cancelled_at:>={_iso(window_start)}"),
        ("q[]", f"cancelled_at:<{_iso(now)}"),
    ]))
    now_iso = _iso(now)
    reschedules = cdnr = 0
    resched_patients = set()
    future_cache = {}
    for a in cancelled:
        type_id = phase2.id_from_link(a.get("appointment_type"))
        if type_id in config.EXCLUDED_FROM_TOTAL_APPTS:
            continue   # class / workshop slot — not a patient cancellation
        pid = phase2.id_from_link(a.get("patient"))
        if pid not in future_cache:
            future_cache[pid] = _has_future_booking(pid, now_iso)
        if future_cache[pid]:
            reschedules += 1
            resched_patients.add(pid)
        else:
            cdnr += 1
    return reschedules, cdnr, resched_patients


# ===========================================================================
# Reactivations — a drop-off patient with a new appointment created today
# ===========================================================================

def dropoff_patient_ids():
    """Patient IDs of everyone on the drop-off master sheet.

    The 'Patient Name' cell is a HYPERLINK to the Cliniko patient page, so the
    patient ID is read straight out of the formula.
    """
    import phase1_fetch as master
    sh = master.open_spreadsheet()
    ids = set()
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            col = ws.get("C2:C", value_render_option="FORMULA")
        except Exception as e:
            print(f"  WARN couldn't read patients from {ws.title}: {e}")
            continue
        for row in col:
            if row:
                m = re.search(r"/patients/(\d+)", str(row[0]))
                if m:
                    ids.add(m.group(1))
    return ids


def reactivations(this_mon, now):
    """Week-to-date reactivations via the canonical engine (reactivations.py) —
    the same definition the weekly DM and the bookings sheet use. Counts each
    drop-off patient whose first rebooking was created since Monday 08:00.
    """
    import reactivations as react
    week_start = datetime(this_mon.year, this_mon.month, this_mon.day,
                          8, 0, tzinfo=LONDON)
    created = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={_iso(week_start)}"),
        ("q[]", f"created_at:<{_iso(now)}"),
    ]))
    created += list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={_iso(week_start)}"),
        ("q[]", f"created_at:<{_iso(now)}"),
        ("q[]", "cancelled_at:?"),   # include cancelled bookings (hidden by default)
    ]))
    cand_pids = {phase2.id_from_link(a.get("patient")) for a in created}
    cand_pids.discard(None)
    n = 0
    for pid in cand_pids:
        hist = phase2.fetch_patient_full_history(pid)
        n += len(react.reactivations_in_window(hist, week_start, now, now))
    return n


# ===========================================================================
# Reactivation target — 40% of the previous week's drop-off count
# ===========================================================================

def previous_week_dropoff_count(this_mon):
    """Drop-off rows in the previous completed week's tab, excluding the
    pre-IA types (config.REACTIVATION_TARGET_EXCLUDE)."""
    import phase1_fetch as master
    prev_mon = this_mon - timedelta(days=7)
    tab = f"W/C {prev_mon.strftime('%d %b %Y')}"
    try:
        ws = master.open_spreadsheet().worksheet(tab)
        rows = ws.get_all_records()
    except Exception as e:
        print(f"  WARN couldn't read previous-week tab '{tab}': {e}")
        return None
    n = 0
    for r in rows:
        kind = str(r.get("Drop-off Type") or r.get("dropoff_type") or "").strip().lower()
        if kind and kind not in config.REACTIVATION_TARGET_EXCLUDE:
            n += 1
    return n


# ===========================================================================
# Bookwhen — Pilates class numbers from the iCal feed
# ===========================================================================

def _unfold_ical(text):
    """Undo RFC-5545 line folding (continuation lines start with space/tab)."""
    lines = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ical_date(value):
    digits = re.sub(r"[^0-9]", "", value)[:8]
    if len(digits) == 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    return None


def _pilates_category(summary):
    s = summary.lower()
    if "reformer" in s and "cookstown" in s:
        return "pilates_reformer_cookstown"
    if "matwork" in s and "cookstown" in s:
        return "pilates_matwork_cookstown"
    if "matwork" in s and "maghera" in s:
        return "pilates_matwork_maghera"
    return None


def pilates_counts(this_mon, next_mon):
    """Booked attendees per Pilates category, for this week and the next week
    that actually has classes.

    Pilates is published in monthly blocks, so the literal next week is often
    empty — the report's Pilates 'next' column therefore shows the next week
    with classes (e.g. W/C 1 Jun while W/C 25 May is empty), matching how the
    admin team report it by hand.

    Reads ONLY the [booked/capacity] count in each event title — the attendee
    list in the event description is never touched.

    Returns (counts, pilates_next_monday) or (None, None) on fetch failure.
    """
    try:
        resp = requests.get(config.BOOKWHEN_ICAL_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  WARN Bookwhen feed fetch failed: {e}")
        return None, None

    events = []   # (date, category, booked)
    event = None
    for line in _unfold_ical(resp.text):
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT":
            if event:
                d = _ical_date(event.get("DTSTART", ""))
                summary = event.get("SUMMARY", "")
                cat = _pilates_category(summary)
                # Booked count = the FIRST number in the title's
                # [booked/capacity] tag. Bookwhen writes "+N" for waiting-list
                # entries (e.g. [12+1/12]) — the waiting list is NOT counted.
                m = re.search(r"\[\s*(\d+)[^/\]]*/", summary)
                if d and cat and m:
                    events.append((d, cat, int(m.group(1))))
            event = None
        elif event is not None and ":" in line:
            key, val = line.split(":", 1)
            event[key.split(";")[0]] = val

    counts = {}
    for d, cat, booked in events:
        if this_mon <= d < next_mon:
            counts[(cat, "this")] = counts.get((cat, "this"), 0) + booked

    # Manual corrections for known Bookwhen under-counts (this block only).
    for cat, adj in config.EOD_PILATES_ADJUSTMENTS.items():
        if adj:
            counts[(cat, "this")] = counts.get((cat, "this"), 0) + adj

    # Next week = the first week (Monday) on/after next_mon that has classes.
    future = sorted(d for d, _, _ in events if d >= next_mon)
    pilates_next_mon = None
    if future:
        first = future[0]
        pilates_next_mon = first - timedelta(days=first.weekday())
        pn_end = pilates_next_mon + timedelta(days=7)
        for d, cat, booked in events:
            if pilates_next_mon <= d < pn_end:
                counts[(cat, "next")] = counts.get((cat, "next"), 0) + booked
    return counts, pilates_next_mon


# ===========================================================================
# Leads not booked
# ===========================================================================

def new_bookings_this_week(this_mon, now):
    """Count of new-patient IA bookings MADE this week.

    Counts /individual_appointments whose booking was created since Monday
    00:00 (London) this week, of an IA type, and not cancelled — the same
    definition the New Patient Bookings tracker uses (bookings_fetch.py). This
    is keyed on when the booking was MADE (created_at), not when the
    appointment happens, so it answers 'how many new patients did we book this
    week?'. One count per IA appointment booked, matching the bookings sheet's
    weekly 'Total IAs'. Reactivations (a patient rebooking an IA after recently
    dropping one) are EXCLUDED here too, so a no-show-then-rebook doesn't
    double-count as two new IAs — same rule as the bookings tracker.
    """
    import bookings_fetch
    week_start = _midnight(this_mon)
    created = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={_iso(week_start)}"),
        ("q[]", f"created_at:<{_iso(now)}"),
    ]))
    history_cache = {}
    n = 0
    for a in created:
        type_id = phase2.id_from_link(a.get("appointment_type"))
        if type_id not in config.BOOKINGS_IA_TYPE_IDS:
            continue   # not an initial assessment
        if a.get("cancelled_at"):
            continue   # booking already cancelled — not a live new booking
        patient_id = phase2.id_from_link(a.get("patient"))
        if patient_id and patient_id not in history_cache:
            try:
                history_cache[patient_id] = phase2.fetch_patient_full_history(patient_id)
            except Exception:
                history_cache[patient_id] = None
        if bookings_fetch._is_reactivation(a, history_cache.get(patient_id)):
            continue   # reactivation, not a fresh IA
        n += 1
    return n


def leads_not_booked(this_mon, next_mon):
    """Count of Leads-tab rows dated within the current week that have NOT been
    booked. A lead is 'booked' once its Status column is set to "booked";
    everything else (pending / declined / lost / blank) counts as not booked,
    matching the definition used in the drop-off Ops summary and Dashboard."""
    import bookings_fetch
    try:
        ws = bookings_fetch.open_spreadsheet().worksheet(bookings_fetch.LEADS_TAB)
        records = ws.get_all_records()
    except Exception as e:
        print(f"  WARN couldn't read Leads tab: {e}")
        return None
    n = 0
    for rec in records:
        # Skip leads that have been booked (Status column on the Leads tab).
        status = str(rec.get("Status") or "").strip().lower()
        if status == "booked":
            continue
        raw = str(rec.get("Date") or "").strip()
        dt = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d %b %Y",
                    "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt)
                break
            except ValueError:
                continue
        if dt and this_mon <= dt.date() < next_mon:
            n += 1
    return n


# ===========================================================================
# Report
# ===========================================================================

def _n(v):
    return "?" if v is None else str(v)


def build_report(now, this_mon, next_mon, appt, resched, cdnr, react,
                 react_target, leads, new_bookings, pilates, pilates_next_mon):
    """Build the monospace stats table."""
    def cell(d, key):
        return "?" if d is None else _n(d.get(key, 0))

    def pcell(cat, when):
        if pilates is None:
            return "?"
        if when == "next" and pilates_next_mon is None:
            return "—"
        return _n(pilates.get((cat, when), 0))

    # Pilates 'next' may be a later week than the appointment 'next week'.
    if pilates_next_mon is None or pilates_next_mon == next_mon:
        pilates_next_hdr = "Next Wk"
    else:
        pilates_next_hdr = f"{pilates_next_mon.day} {pilates_next_mon:%b}"

    rows = [
        ("", "This Wk", "Next Wk", "Target"),
        ("New Bookings", _n(new_bookings), "—", ""),
        ("Leads not booked", _n(leads), "—", ""),
        ("Total Appts Cookstown",
         cell(appt["total"], ("Cookstown", "this")),
         cell(appt["total"], ("Cookstown", "next")),
         _n(config.EOD_TARGETS["total_appts_Cookstown"])),
        ("Total Appts Maghera",
         cell(appt["total"], ("Maghera", "this")),
         cell(appt["total"], ("Maghera", "next")),
         _n(config.EOD_TARGETS["total_appts_Maghera"])),
        ("IAs Cookstown",
         cell(appt["ias"], ("Cookstown", "this")),
         cell(appt["ias"], ("Cookstown", "next")),
         _n(config.EOD_TARGETS["ias_Cookstown"])),
        ("IAs Maghera",
         cell(appt["ias"], ("Maghera", "this")),
         cell(appt["ias"], ("Maghera", "next")),
         _n(config.EOD_TARGETS["ias_Maghera"])),
        ("Reactivations", _n(react), "—", _n(react_target)),
        None,   # blank line
        ("PILATES", "This Wk", pilates_next_hdr, "Target"),
        ("Matwork Cookstown",
         pcell("pilates_matwork_cookstown", "this"),
         pcell("pilates_matwork_cookstown", "next"),
         _n(config.EOD_TARGETS["pilates_matwork_cookstown"])),
        ("Matwork Maghera",
         pcell("pilates_matwork_maghera", "this"),
         pcell("pilates_matwork_maghera", "next"),
         _n(config.EOD_TARGETS["pilates_matwork_maghera"])),
        ("Reformer Cookstown",
         pcell("pilates_reformer_cookstown", "this"),
         pcell("pilates_reformer_cookstown", "next"),
         _n(config.EOD_TARGETS["pilates_reformer_cookstown"])),
    ]
    bottom = ["Reschedules", "CDNR", "Reactivations this shift"]
    lw = max([len(r[0]) for r in rows if r] + [len(x) for x in bottom])
    body = []
    for r in rows:
        if r is None:
            body.append("")
        else:
            body.append(f"{r[0]:<{lw}}  {r[1]:>8}  {r[2]:>8}  {r[3]:>7}")
    body.append("")
    body.append(f"{'Reschedules':<{lw}}  {_n(resched):>8}")
    body.append(f"{'CDNR':<{lw}}  {_n(cdnr):>8}")
    # Filled in by the admin at the end of each shift.
    body.append(f"{'Reactivations this shift':<{lw}}")

    header = f"EOD Stats — {now.strftime('%a %d %b %Y, %H:%M')}"
    sub = (f"This week W/C {this_mon.day} {this_mon:%b}  ·  "
           f"Next week W/C {next_mon.day} {next_mon:%b}")
    return header, sub, "\n".join(body)


# ===========================================================================
# Slack
# ===========================================================================

def post_to_slack(header, sub, table):
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("  WARN SLACK_BOT_TOKEN not set — not posting")
        return False
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    text = f"*{header}*\n_{sub}_\n```\n{table}\n```"
    client = WebClient(token=token)
    channel = config.EOD_SLACK_CHANNEL
    try:
        if config.SLACK_SAFE_MODE:
            channel = client.users_lookupByEmail(
                email=config.CEO_SLACK_EMAIL)["user"]["id"]
            text = f"*[TEST → would post to {config.EOD_SLACK_CHANNEL}]*\n\n" + text
        client.chat_postMessage(channel=channel, text=text, unfurl_links=False)
        print(f"  Posted to Slack ({channel}).")
        return True
    except SlackApiError as e:
        err = e.response.get("error")
        print(f"  WARN Slack post failed: {err}")
        if err in ("not_in_channel", "channel_not_found"):
            print(f"  → Invite the bot to {config.EOD_SLACK_CHANNEL} "
                  f"(type '/invite @<bot>' in that channel).")
        return False


def dm_to_user(header, sub, table, email):
    """DM the stats report to a single Slack user (looked up by email).

    Used by the Sunday personal weekly wrap — goes only to that person's DM,
    never to the team #eod-claude channel.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("  WARN SLACK_BOT_TOKEN not set — not DMing")
        return False
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    text = f"*{header}*\n_{sub}_\n```\n{table}\n```"
    client = WebClient(token=token)
    try:
        uid = client.users_lookupByEmail(email=email)["user"]["id"]
        client.chat_postMessage(channel=uid, text=text, unfurl_links=False)
        print(f"  DMed report to {email} ({uid}).")
        return True
    except SlackApiError as e:
        print(f"  WARN Slack DM failed: {e.response.get('error')}")
        return False


# ===========================================================================
# Persist the week-to-date reactivations figure to the drop-off master sheet
# ===========================================================================

REACTIVATIONS_LIVE_TAB = "Reactivations (Live)"


def write_reactivations_to_sheet(react, react_target, this_mon, now):
    """Write the week-to-date reactivations count to a small, self-explanatory
    panel on the drop-off master sheet, so the figure can be looked up at any
    time without waiting for the Slack message.

    Overwrites in place each EOD run, so it always shows the latest
    week-to-date number plus when it was last updated. Uses the exact value
    posted to Slack, so the sheet and the Slack report can never disagree.
    """
    import gspread
    import phase1_fetch as master

    sh = master.open_spreadsheet()
    try:
        ws = sh.worksheet(REACTIVATIONS_LIVE_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=REACTIVATIONS_LIVE_TAB, rows=20, cols=3)

    target_str = str(react_target) if react_target is not None else "—"
    out = [
        ["Reactivations — Live (EOD definition)", ""],
        ["Drop-off patients who rebooked this week (since Monday 08:00). "
         "Same number as the EOD Slack report.", ""],
        ["", ""],
        ["Week commencing", this_mon.strftime("%a %d %b %Y")],
        ["Reactivations this week", react],
        ["Weekly target", target_str],
        ["Last updated", now.strftime("%a %d %b %Y %H:%M")],
    ]
    ws.update(values=out, range_name="A1", value_input_option="RAW")


# ===========================================================================
# main
# ===========================================================================

def main():
    post = "--post" in sys.argv
    # --dm sends the report only to Martin's Slack DM (the Sunday weekly wrap),
    # never to the team #eod-claude channel.
    dm = "--dm" in sys.argv
    now = datetime.now(LONDON)
    this_mon, next_mon, week_after = week_bounds(now)

    print(f"Building EOD stats for {now.strftime('%a %d %b %Y %H:%M')}…")

    print("  Cliniko appointment counts…")
    appt = appointment_counts(this_mon, next_mon, week_after)

    win_start = reschedule_window_start(now)
    print(f"  Reschedules / CDNR since {win_start.strftime('%a %d %b %H:%M')}…")
    resched, cdnr, resched_patients = cancellation_stats(win_start, now)

    print("  Reactivations (week-to-date)…")
    react = reactivations(this_mon, now)

    prev_count = previous_week_dropoff_count(this_mon)
    react_target = (round(prev_count * config.REACTIVATION_TARGET_FRACTION)
                    if prev_count is not None else None)

    print("  Leads not booked…")
    leads = leads_not_booked(this_mon, next_mon)

    print("  New bookings this week…")
    new_bookings = new_bookings_this_week(this_mon, now)

    print("  Pilates (Bookwhen)…")
    pilates, pilates_next_mon = pilates_counts(this_mon, next_mon)

    header, sub, table = build_report(
        now, this_mon, next_mon, appt, resched, cdnr, react,
        react_target, leads, new_bookings, pilates, pilates_next_mon)
    print("\n" + header)
    print(sub)
    print(table + "\n")

    if post or dm:
        # Persist the week-to-date reactivations figure to the drop-off master
        # sheet so it can be found any time. Never let a sheet failure block
        # the Slack send — the report is the priority.
        try:
            write_reactivations_to_sheet(react, react_target, this_mon, now)
            print(f"  Wrote reactivations ({react}) to '{REACTIVATIONS_LIVE_TAB}' tab.")
        except Exception as e:
            print(f"  WARN couldn't write reactivations to sheet: {e}")

    if dm:
        # Sunday personal weekly wrap — DM Martin only, with a clearer header.
        dm_header = f"Weekly Wrap — week ending {now.strftime('%a %d %b %Y')}"
        ok = dm_to_user(dm_header, sub, table, config.CEO_SLACK_EMAIL)
        # Exit non-zero on failure so the cloud wrapper retries.
        sys.exit(0 if ok else 1)
    elif post:
        ok = post_to_slack(header, sub, table)
        # Exit non-zero on failure so the launchd wrapper retries.
        sys.exit(0 if ok else 1)
    else:
        print("(Preview only — re-run with --post to send to Slack.)")


if __name__ == "__main__":
    main()
