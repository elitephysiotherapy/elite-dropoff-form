"""Day-before IA call list → Slack DM to reception at 09:00 (Mon–Fri).

Martin, 20 Nov 2026. We used to ask patients to reply YES/NO the day before; that
gave them a way out and cancellations went UP. What worked was a PERSON ringing the
day before to check everything was fine and fix any obstacle. So: no reply text —
reception gets this list and makes the calls.

Not every IA — only the ones at higher risk of not turning up:
  Meta lead              the patient came from a Meta ad lead (phone matches the
                         "Meta Leads (raw)" tab, lead created before the booking)
  Brand new patient      never attended an appointment with us before
  Previous poor attender a previous patient with ANY no-show, or 2+ cancellations,
                         in the 2 years before this appointment

Whoever is on the front desk that day makes the calls. The list covers the NEXT
WORKING DAY's appointments (Friday's 09:00 list is Monday's), skipping weekends and
config.CLINIC_CLOSED_DAYS. Bookings made after 09:00 for tomorrow are not on it —
those are handled on the booking call.

Which appointments: config.FORM_CHASE_TYPE_IDS (the first-consultation types:
2. Initial Appointment, 3. Club IA, 7. ACL IA, 2. Sports & MSK Consultation,
5. PHI IA). Not cancelled, not a no-show already.

Modes:
  python send_day_before_calls.py --counts          counts only, NO names (safe to paste)
  python send_day_before_calls.py --dm-ceo          real list as a DM to Martin only (preview)
  python send_day_before_calls.py --post            send to reception (the cron)
  add --date YYYY-MM-DD to build the list for a specific day (default: next working day)
"""

import argparse
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import phase2

LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

HISTORY_DAYS = 730          # "last 2 years"
MIN_CANCELS = 2             # 2+ cancellations => poor attender (any no-show also counts)

META_LABEL = "Meta lead"
NEW_LABEL = "Brand new patient"
POOR_LABEL = "Previous poor attender"


# ---------------------------------------------------------------- dates

def is_working_day(d):
    return d.weekday() < 5 and d not in config.CLINIC_CLOSED_DAYS


def next_working_day(today):
    """The next day the clinic is open after `today` (Fri -> Mon, skips closures)."""
    d = today + timedelta(days=1)
    while not is_working_day(d):
        d += timedelta(days=1)
    return d


def _utc_iso(d):
    dt = datetime(d.year, d.month, d.day, tzinfo=LONDON).astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- classification

def _last10(s):
    return re.sub(r"\D", "", s or "")[-10:]


def _lead_time(text):
    """Meta lead 'Created' value -> aware UTC datetime (first 19 chars, whatever
    the offset suffix looks like). None if unparseable."""
    try:
        return datetime.strptime(text.replace("Z", "").replace(" ", "T")[:19],
                                 "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None


def classify(appt, history, meta_lead_times, now):
    """(reasons, detail) for one appointment.

    reasons : list of labels (empty => not on the call list)
    detail  : counts used for the note, e.g. {"dna": 1, "cna": 2}
    history : every appointment of this patient (any status)
    meta_lead_times : datetimes of Meta leads whose phone matches this patient
    """
    start = phase2.parse_iso(appt.get("starts_at"))
    created = phase2.parse_iso(appt.get("created_at"))
    window_from = start - timedelta(days=HISTORY_DAYS)
    attended_before = False
    dna = cna = 0
    for x in history:
        if str(x.get("id")) == str(appt.get("id")):
            continue
        xs = phase2.parse_iso(x.get("starts_at"))
        if not xs or xs >= start:
            continue
        if x.get("cancelled_at"):
            if xs >= window_from:
                cna += 1
        elif x.get("did_not_arrive"):
            if xs >= window_from:
                dna += 1
        elif xs <= now:
            attended_before = True

    reasons = []
    if created and any(t <= created for t in meta_lead_times):
        reasons.append(META_LABEL)
    if not attended_before:
        reasons.append(NEW_LABEL)
    elif dna >= 1 or cna >= MIN_CANCELS:
        reasons.append(POOR_LABEL)
    return reasons, {"dna": dna, "cna": cna}


def _history_note(detail):
    bits = []
    if detail["dna"]:
        bits.append(f"{detail['dna']} no-show{'s' if detail['dna'] != 1 else ''}")
    if detail["cna"]:
        bits.append(f"{detail['cna']} cancellation{'s' if detail['cna'] != 1 else ''}")
    return " + ".join(bits) + " in the last 2 years" if bits else ""


# ---------------------------------------------------------------- data

def _meta_lead_times():
    """{last-10-digit phone: [lead created datetimes]} from the 'Meta Leads (raw)'
    tab. Returns (map, ok) — ok False if the sheet couldn't be read."""
    try:
        import gspread
        import meta_leads
        import sheets_retry
        gc = gspread.authorize(meta_leads._credentials())
        sh = gc.open_by_key(config.BOOKINGS_SPREADSHEET_ID)
        ws = sh.worksheet("Meta Leads (raw)")
        values = sheets_retry.gs_retry(ws.get_all_values, "day-before meta leads")
    except Exception as e:  # noqa: BLE001
        print(f"  WARN: Meta leads sheet unavailable: {e}")
        return {}, False
    if not values:
        return {}, True
    head = values[0]
    try:
        pi, ci = head.index("Phone"), head.index("Created")
    except ValueError:
        return {}, False
    out = {}
    for r in values[1:]:
        if len(r) <= max(pi, ci):
            continue
        t = _lead_time(r[ci])
        k = _last10(r[pi])
        if t and k:
            out.setdefault(k, []).append(t)
    return out, True


def build_list(target):
    """[{time, clinic, physio, name, phone, reasons, note, form}] for `target`,
    sorted by time. Also returns meta_ok. Reads Cliniko; never writes."""
    from marketing import cliniko, common, forms

    now = datetime.now(UTC)
    start, end = target, target + timedelta(days=1)
    appts = [a for a in cliniko.appointments_started_between(
                 phase2.parse_iso(_utc_iso(start)), phase2.parse_iso(_utc_iso(end)))
             if not cliniko.is_cancelled(a) and not a.get("did_not_arrive")
             and cliniko.appt_type_id(a) in config.FORM_CHASE_TYPE_IDS]
    meta_map, meta_ok = _meta_lead_times()
    form_map = forms.by_appointment()

    rows = []
    for a in appts:
        pid = cliniko.patient_id_of(a)
        if not pid:
            continue
        patient = cliniko.get_patient(pid) or {}
        history = list(phase2.fetch_patient_full_history(pid))
        reasons, detail = classify(a, history, meta_map.get(_last10(patient.get("mobile")), []), now)
        if not reasons:
            continue
        form = None
        if form_map is not None:
            fm = form_map.get(str(a["id"]))
            if fm and fm.get("completed_at"):
                form = "form done"
            elif forms.pending_url(form_map, a["id"], now):
                form = "form NOT done"
            else:
                form = "no usable form"
        rows.append({
            "starts": phase2.parse_iso(a["starts_at"]),
            "time": common.fmt_time(a["starts_at"]),
            "clinic": cliniko.clinic_for(a),
            "physio": cliniko.practitioner_name(a) or "physio TBC",
            "name": patient.get("full_name") or "(name missing)",
            "phone": patient.get("mobile") or "no mobile on file",
            "reasons": reasons,
            "note": _history_note(detail),
            "form": form,
        })
    rows.sort(key=lambda r: (r["starts"], r["clinic"]))
    return rows, meta_ok


# ---------------------------------------------------------------- message

def _day_label(d):
    return d.strftime("%A %d %B").replace(" 0", " ")


def build_message(target, rows, meta_ok=True):
    day = _day_label(target)
    if not rows:
        return (f"*IA calls for {day}*\nNo calls needed. None of tomorrow's first "
                f"visits are Meta leads, brand new patients or previous poor attenders.")
    n = len(rows)
    lines = [f"*IA calls for {day}: {n} to make*",
             "Please ring today. The aim is to help them get ready (parking, time off, "
             "any worries, the form), not to ask if they're still coming. If they can't "
             "make it, offer two times that week and rebook before you end the call.",
             ""]
    for r in rows:
        why = " + ".join(r["reasons"])
        bits = [f"*{r['time']}* · {r['clinic']} · {r['physio']}", r["name"], r["phone"]]
        head = " · ".join(bits)
        tail = [f":warning: {why}"]
        if r["note"]:
            tail.append(r["note"])
        if r["form"]:
            tail.append(r["form"])
        lines.append(f"• {head}\n    {'  |  '.join(tail)}")
    lines.append("")
    lines.append("_If nobody answers, try again late afternoon and leave a note on "
                 "the patient. Bookings made after 9am today aren't on this list._")
    if not meta_ok:
        lines.append("_Heads up: I couldn't check Meta leads today, so that tag may be missing._")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true", help="DM reception (the cron)")
    ap.add_argument("--dm-ceo", action="store_true", help="DM Martin only (preview)")
    ap.add_argument("--counts", action="store_true", help="print counts only, no names")
    ap.add_argument("--date", help="build the list for this date (YYYY-MM-DD)")
    args = ap.parse_args()

    today = datetime.now(LONDON).date()
    if args.date:
        target = date.fromisoformat(args.date)
    else:
        if args.post and not is_working_day(today):
            print(f"{today} is not a working day — no list today.")
            return
        target = next_working_day(today)

    rows, meta_ok = build_list(target)

    if args.counts:
        by = {}
        for r in rows:
            for why in r["reasons"]:
                by[why] = by.get(why, 0) + 1
        print(f"{target}: {len(rows)} call(s) | by reason: {by} | meta check ok: {meta_ok}")
        return

    msg = build_message(target, rows, meta_ok)
    if args.dm_ceo:
        import slack_notifier
        ok = slack_notifier._send_dm(
            config.CEO_SLACK_EMAIL, "*[PREVIEW — not sent to reception]*\n\n" + msg,
            target_label="day-before calls preview")
        print("sent preview to Martin" if ok else "FAILED to send preview")
        return
    if args.post:
        import slack_notifier
        slack_notifier._send_dm_to_recipients(
            config.DAY_BEFORE_CALLS_SLACK_EMAILS, msg, target_label="day-before IA calls")
        print(f"posted {len(rows)} call(s) for {target}")
        return
    print(f"{target}: {len(rows)} call(s). Use --counts, --dm-ceo or --post.")


if __name__ == "__main__":
    main()
