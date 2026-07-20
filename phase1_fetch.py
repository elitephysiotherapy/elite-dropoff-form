"""Phase 1 — pull yesterday's drop-offs from Cliniko.

Default mode: preview only. Pass --write to append into the Google Sheet.

Window: yesterday's calendar day in Europe/London.

Drop-off categories (priority order):
  - cancelled       cancelled_at set AND patient has no future booking
                    (cancelled + future booking = reschedule, excluded)
  - did_not_attend  did_not_arrive=true (always included)
  - no_rebook       attended initial-assessment AND no future booking

Sheet routing: one tab per week ('W/C DD Mon YYYY'), auto-created with headers
and the two helper columns (appointment_id, pulled_at) hidden. Re-runs are
idempotent — rows already present in the tab (matched by appointment_id) are
skipped, so existing human edits to rows are never overwritten.
"""

import os
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import requests

import gspread
from google.oauth2.service_account import Credentials

import config

load_dotenv(override=True)
API_KEY = os.environ["CLINIKO_API_KEY"]
SHARD = os.environ["CLINIKO_SHARD"]
USER_AGENT = os.environ["CLINIKO_USER_AGENT"]

BASE = f"https://api.{SHARD}.cliniko.com/v1"
SESSION = requests.Session()
SESSION.auth = (API_KEY, "")
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

LONDON = ZoneInfo("Europe/London")
ID_RE = re.compile(r"/(\d+)(?:/[^/]*)?/?$")

# Used ONLY for no_rebook detection (a real IA that expects a follow-up).
# Phase 2 uses a broader list for "find episode start to read notes".
PHASE1_DROPOFF_IA_TYPE_IDS = {
    "382563815654429852",   # 1. Initial Appointment
    "392015278608749674",   # 3. Club Initial Assessment
    "1558530673046721630",  # 5. Private Health Insurance Initial Assessment
    "945551547020874765",   # 7. ACL Initial Assessment
}

SPREADSHEET_ID = "1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI"
SERVICE_ACCOUNT_FILE = "service_account.json"
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CLINIKO_WEB_DOMAIN = "elite-physiotherapy.uk1.cliniko.com"

SHEET_COLUMNS = [
    "appointment_date",        # A
    "cancellation_date",       # B
    "patient",                 # C
    "physio",                  # D
    "clinic",                  # E
    "appointment_type",        # F
    "dropoff_type",            # G
    "session_number",          # H — moved here per Martin 2026-05-12
    "notice",                  # I
    "cancellation_reason",     # J
    "body_area",               # K
    "clinical_non_clinical",   # L (Phase 4)
    "next_step_physio",        # M (Phase 4)
    "reactivation_status",     # N
    "reactivation_notes",      # O
    "martys_comments",         # P
    "actioned",                # Q
    "appointment_id",          # R (hidden)
    "pulled_at",               # S (hidden)
]
HIDDEN_COLUMNS = ("appointment_id", "pulled_at")

HEADER_LABELS = {
    "appointment_date": "Appointment Date",
    "cancellation_date": "Cancellation Date",
    "patient": "Patient Name",
    "physio": "Physio",
    "clinic": "Clinic",
    "appointment_type": "Appointment Type",
    "dropoff_type": "Drop-off Type",
    "notice": "Notice Given",
    "cancellation_reason": "Cancellation Reason",
    "session_number": "Session #",
    "body_area": "Body Area",
    "clinical_non_clinical": "Clinical / Non-Clinical",
    "next_step_physio": "Next Step (Physio)",
    "reactivation_status": "Reactivation Status",
    "reactivation_notes": "Reactivation Notes",
    "martys_comments": "Marty's Comments",
    "actioned": "Actioned",
    "appointment_id": "appointment_id",
    "pulled_at": "pulled_at",
}


# ---------- Cliniko helpers ----------

def yesterday_london_window_utc():
    now_london = datetime.now(LONDON)
    today_local_start = now_london.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_local_start = today_local_start - timedelta(days=1)
    return (
        yesterday_local_start.astimezone(timezone.utc),
        today_local_start.astimezone(timezone.utc),
    )


def day_london_window_utc(yyyy_mm_dd):
    """Window covering the given London-local calendar day."""
    y, m, d = (int(x) for x in yyyy_mm_dd.split("-"))
    day_start = datetime(y, m, d, tzinfo=LONDON)
    day_end = day_start + timedelta(days=1)
    return day_start.astimezone(timezone.utc), day_end.astimezone(timezone.utc)


import time


def fetch_all(path, params=None):
    url = f"{BASE}{path}"
    qp = list(params or []) + [("per_page", 100)]
    first = True
    while url:
        r = None
        for attempt in range(12):
            try:
                r = SESSION.get(url, params=qp if first else None, timeout=30)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                wait = min(5 * (attempt + 1), 60)
                print(f"  network error ({type(e).__name__}) on {path}, "
                      f"retry {attempt + 1}/12 in {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "5"))
                time.sleep(wait + 1)
                continue
            break
        first = False
        if r is None:
            print(f"ERROR network failed after 12 retries on {url}")
            sys.exit(1)
        if r.status_code != 200:
            print(f"ERROR {r.status_code} on {r.url}\n{r.text[:500]}")
            sys.exit(1)
        data = r.json()
        coll_key = next(k for k, v in data.items() if isinstance(v, list))
        for item in data[coll_key]:
            yield item
        url = (data.get("links") or {}).get("next")


_ALL_PRACS_CACHE = [None]


def all_practitioners():
    """Every practitioner keyed by str(id) — ACTIVE **AND** INACTIVE.

    Cliniko's default /practitioners listing returns only active staff, and
    GET /practitioners/<id> 404s once someone is deactivated. Their appointments
    are NOT deleted — they still come back from /individual_appointments — but
    without the inactive records here, a leaver's practitioner_id can't be
    resolved to a name, so their drop-offs land against "?" instead of them.
    That's what happened when Daire McKenna was deactivated (2026-07-13).

    Mirrors phase2.all_practitioners(); kept local so this script stays
    self-contained. Always use this for practitioner_id -> name lookups.
    """
    if _ALL_PRACS_CACHE[0] is None:
        pracs = {str(p["id"]): p for p in fetch_all("/practitioners")}
        for p in fetch_all("/practitioners", [("q[]", "active:=false")]):
            pracs.setdefault(str(p["id"]), p)
        _ALL_PRACS_CACHE[0] = pracs
    return _ALL_PRACS_CACHE[0]


def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def id_from_link(rel):
    if not rel:
        return None
    self_url = (rel.get("links") or {}).get("self") or ""
    m = ID_RE.search(self_url)
    return m.group(1) if m else None


def has_future_booking_in_history(appt, history):
    """True if patient has any non-cancelled appointment with start > this appt's start.

    Only valid for an ATTENDED appointment (the IADNR "did they rebook after their IA?"
    test), where the appointment's start is effectively "now". Do NOT use it for a
    cancellation — see still_booked_in."""
    appt_id = str(appt.get("id"))
    appt_start = appt.get("starts_at") or ""
    return any(
        (a.get("starts_at") or "") > appt_start
        and not a.get("cancelled_at")
        and str(a.get("id")) != appt_id
        for a in history
    )


def _now_utc():
    return datetime.now(timezone.utc)


def still_booked_in(appt, history):
    """True if the patient STILL has an appointment in the diary — so they rescheduled
    or kept other care and there is nothing for a physio to chase.

    The reference point is NOW, not the cancelled appointment's own start. The old test
    asked "is there a booking later than the slot they cancelled?", which breaks for a
    patient who cancels the FURTHEST-OUT appointments in their diary: they hold nothing
    after the cancelled slot, so they were flagged as a drop-off while still actively
    attending. (Niamh O'Donnell, Jul 2026 — bulk-cancelled her standing 13:30 series on
    7 Jul, kept a 16 Jul evening slot and rebooked 27 Jul + 10 Aug. Sessions 57/60/61.)

    Deliberately NOT the stricter "no future appointment at end of the cancellation day"
    rule that reactivations.py uses to COUNT drop-offs: someone who cancels and rebooks
    three days later is a genuine drop-off-then-reactivation for the stats, but there is
    no point DMing their physio to chase them — they are already back in the diary."""
    now_iso = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    appt_id = str(appt.get("id"))
    return any(
        (a.get("starts_at") or "") > now_iso
        and not a.get("cancelled_at")
        and not a.get("did_not_arrive")
        and str(a.get("id")) != appt_id
        for a in history
    )


# >180 days since last attended visit = new episode of care. Was 60 (Martin
# 2026-07-13), raised to 180 on 2026-07-20 to match phase2's
# GAP_DAYS_FOR_NEW_EPISODE: 60 days was cutting off patients the physio HAD
# genuinely treated and then lost — Ciaran Moran (118d), Aileen Wilson (91d),
# Aidan Hughes (83d), Peter Scullion (65d) are all real IADNRs that 60 days
# wrongly demoted to pre-IA. 180 days still catches the true returners
# (Paddy Kelly 286d, Julieann Bell 2358d).
NEW_EPISODE_GAP_DAYS = 180


def _appt_start_dt(a):
    s = a.get("starts_at") or ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def no_attendance_this_episode(appt, history):
    """True if the patient has NOT attended anything in their CURRENT episode of
    care before this drop-off event. A gap of more than NEW_EPISODE_GAP_DAYS days
    since the last attended visit starts a new episode, so a long-gap return that
    cancels/DNAs before attending is pre-IA (IACNA/IADNA) — the physio never saw
    them this episode. (Rebecca McConnell never attended; Peter Small last
    attended years earlier. Martin 2026-07.)"""
    appt_start = appt.get("starts_at") or ""
    attended = [h for h in (history or [])
                if (h.get("starts_at") or "") < appt_start
                and not h.get("cancelled_at")
                and not h.get("did_not_arrive")]
    if not attended:
        return True  # never attended anything, ever
    last = max(attended, key=lambda h: h.get("starts_at") or "")
    ev, ls = _appt_start_dt(appt), _appt_start_dt(last)
    if ev is None or ls is None:
        return False
    return (ev - ls).days > NEW_EPISODE_GAP_DAYS


def is_ia_only_patient_at(appt, history):
    """At the time of `appt`, has the patient attended only their IA in the current episode?

    Note: this is EPISODE-relative and deliberately `<= 1` — a patient who has
    dropped early in the CURRENT episode (incl. 0 attended within a fresh
    post-gap episode) is an IADNR. The pre-IA case (patient who never attended
    ANYTHING, ever) is handled separately in classify_dropoff via
    attended_before_count() == 0, so it doesn't reach the IADNR label."""
    import phase2 as p2
    _, episode, _ = p2.find_episode(history)
    if not episode:
        return False
    appt_start = appt.get("starts_at") or ""
    attended_before = sum(
        1 for a in episode
        if (a.get("starts_at") or "") < appt_start
        and not a.get("cancelled_at")
        and not a.get("did_not_arrive")
    )
    return attended_before <= 1


def responsible_physio_attended(appt, history):
    """Has the physio this drop-off will be ATTRIBUTED to actually attended with
    the patient before it?

    This is the gate for IADNR (Martin 2026-07-20). An IADNR charges a physio
    with losing a patient, so it only holds if that physio had the patient in
    front of them. What was attended doesn't matter — a diagnostic counts:

      - Aidan Hughes attended an Ultrasound with Martin, then cancelled an
        Injection with Martin. Martin saw him -> IADNR for Martin.
      - Peter McNicholl attended an Ultrasound with JULIE, then cancelled a
        Review with AOIFE. Aoife never saw him -> IACNA for Aoife.

    Structurally identical events; the physio is what separates them. An earlier
    version of this gate asked whether an IA *type* had been attended, which got
    Peter right and Aidan wrong.

    The "has the episode gone cold" half of the rule is handled separately by
    no_attendance_this_episode (NEW_EPISODE_GAP_DAYS)."""
    rp = responsible_physio_id(appt, history)
    if not rp:
        return False
    appt_start = appt.get("starts_at") or ""
    return any(
        (a.get("starts_at") or "") < appt_start
        and not a.get("cancelled_at")
        and not a.get("did_not_arrive")
        and id_from_link(a.get("practitioner")) == rp
        for a in history or []
    )


def responsible_physio_id(appt, history):
    """The physio responsible for a drop-off = physio of the most recent
    ATTENDED appointment in the patient's CURRENT episode of care.

    Episode definition (Martin 2026-06-01): begins at the patient's most
    recent attended STRICT 4 IA (Initial Appt, Club Initial Assessment, PHI
    Initial Assessment, ACL Initial Assessment). Earlier appointments belong
    to a prior episode and don't count. So a new IA always resets the
    responsibility back to the IA physio.

    Excluded from "attendance" because they're not part of physio treatment:
      - Classes / workshops / group sessions (EXCLUDED_FROM_TOTAL_APPTS) —
        e.g. Pilates classes, Back Class, ACL Class etc.
      - Sports Massage (EXCLUDED_FROM_DROPOFF_STATS)

    Examples (Martin's actual cases, 2026-06-01):
      - Conan Milne: new IA with Shannagh, no follow-up → Shannagh
        (IA is the only attended event in the episode)
      - Aidan McNicholl: IA with Aoife, no later treatment attendance → Aoife
      - Thomas Donnelly: IA, then US attended with Julie, then follow-up not
        attended → Julie (US is the most recent attended in episode)
      - Sylvia Mawhinney: IA with Julie, then Pilates → Julie (Pilates is
        excluded, IA is the most recent treatment attendance)

    Falls back to the scheduled physio if the patient has no strict-4 IA in
    their history (true IACNA/pre-IA territory)."""
    appt_type = str(id_from_link(appt.get("appointment_type")))
    wider8 = {str(x) for x in config.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS}
    strict4 = {str(x) for x in config.PHASE1_DROPOFF_IA_TYPE_IDS}
    excluded_from_attendance = (
        {str(x) for x in config.EXCLUDED_FROM_TOTAL_APPTS} |
        {str(x) for x in config.EXCLUDED_FROM_DROPOFF_STATS}
    )

    # CASE A — the drop-off event's appointment IS an IA-type appointment.
    # Responsible = scheduled physio. This covers:
    #   • IACNA (cancelled IA, never attended) — the booked-with physio owns it
    #   • IADNA (DNA'd IA) — same
    #   • IADNR-via-attended-IA (attended strict-4 IA, no rebook) — the IA
    #     physio (= scheduled physio for an attended appointment) is who saw
    #     them, so they own the retention failure
    # This is what makes "new IA → stays with the IA physio" work for Conan
    # Milne / Shea Quinn / Aidan McNicholl / Cadhan Rocks etc.
    if appt_type in wider8:
        return id_from_link(appt.get("practitioner"))

    # CASE B — event is a non-IA cancellation/DNA. Use the episode rule below.
    appt_start = appt.get("starts_at") or ""

    # Step 1: find the patient's most recent attended STRICT 4 IA strictly
    # before this drop-off event. That defines the current episode.
    ia_start = None
    for h in (history or []):
        h_start = h.get("starts_at") or ""
        if not h_start or h_start >= appt_start:
            continue
        if h.get("cancelled_at") or h.get("did_not_arrive"):
            continue
        h_type = str(id_from_link(h.get("appointment_type")))
        if h_type not in strict4:
            continue
        if ia_start is None or h_start > ia_start:
            ia_start = h_start

    if ia_start is None:
        # No strict-4 IA in history — pre-IA territory, scheduled physio owns it.
        return id_from_link(appt.get("practitioner"))

    # Step 2: of the attended appointments in the current episode (i.e. since
    # the IA, including the IA itself), excluding Pilates/classes/Sports
    # Massage, find the most recent one.
    in_episode = [h for h in (history or [])
                  if (h.get("starts_at") or "") >= ia_start
                  and (h.get("starts_at") or "") < appt_start
                  and not h.get("cancelled_at")
                  and not h.get("did_not_arrive")
                  and str(id_from_link(h.get("appointment_type"))) not in excluded_from_attendance]

    if in_episode:
        in_episode.sort(key=lambda x: x.get("starts_at") or "")
        prev_prac = id_from_link(in_episode[-1].get("practitioner"))
        if prev_prac:
            return prev_prac

    # Edge case (every in-episode appt was excluded somehow) — fall back.
    return id_from_link(appt.get("practitioner"))


# IA type set used for classify_dropoff's "is this an IA cancellation/DNA?"
# decision. Wider than PHASE1_DROPOFF_IA_TYPE_IDS (the strict 4 used for NPs):
# also includes Club Consultation, Sports & MSK Consult, Mummy MOT, Pelvic
# Health — so cancelling/DNA-ing one of those gets flagged IACNA/IADNA, not
# the generic "cancelled"/"did_not_attend" bucket (Hugh McGurk case).
#
# NOTE: this set is deliberately NOT the broader-13 new-patient set. Diagnostic
# types (Ultrasound, Profiling, Injury Update Testing) can be booked mid-care by
# an ESTABLISHED patient, so "cancelled X = IACNA" only holds when the patient
# has never attended. The never-attended case is handled in classify_dropoff's
# non-IA branch via attended_before_count() == 0, not by widening this set.
import phase2 as _p2_module
_IA_TYPES_FOR_CLASSIFY = _p2_module.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS


def classify_dropoff(appt, type_id, history):
    """Returns one of: 'iacna', 'iadna', 'iadnr', 'cancelled', 'did_not_attend', None."""
    # Sports Massage IS a drop-off for the weekly list — reception still needs to
    # see and chase it — but it must never reach clinic or individual physio
    # clinical stats. The stats exclusion lives in phase2 (Weekly Snapshot,
    # Performance Dashboard, monthly per-physio), which filters
    # EXCLUDED_FROM_DROPOFF_STATS at source, so classifying it here is safe.
    # (Martin 2026-07-20 — previously returned None, keeping it out of the sheet
    # entirely.) Slack DMs are suppressed separately in the notifier.
    is_ia = type_id in _IA_TYPES_FOR_CLASSIFY
    is_cancelled = bool(appt.get("cancelled_at"))
    is_dna = bool(appt.get("did_not_arrive"))

    if is_ia:
        if is_cancelled:
            # Reschedule rule applies uniformly: if patient still held a future booking
            # (incl. another IA), it's a reschedule, not IACNA. Anna Carberry case.
            if still_booked_in(appt, history):
                return None
            return "iacna"
        if is_dna:
            return "iadna"
        # Attended IA → IADNR only if it's a STRICT 4 IA type (Initial Appt,
        # Club Initial, PHI Initial, ACL Initial) — these are the IAs that
        # expect a follow-up. Sports & MSK Consult, Mummy MOT, Pelvic Health,
        # Club Consultation are one-and-done by design, so no-rebook isn't a
        # drop-off. (Ultrasound Assessment etc. are even narrower NEW_PATIENT
        # types not in is_ia, so they never reach this branch — a cancelled/
        # DNA'd one is handled in the non-IA branch below.)
        if type_id not in PHASE1_DROPOFF_IA_TYPE_IDS:
            return None
        return None if has_future_booking_in_history(appt, history) else "iadnr"

    # Non-IA appointment. If the patient has NEVER attended anything before this
    # event, the physio never actually saw them — it's a pre-IA drop-off
    # (IACNA/IADNA), not a physio-responsible IADNR/CNA/DNA. This is what stops a
    # cancelled first-ever Ultrasound (or any never-attended first booking) being
    # mislabelled IADNR against the scheduled physio (Rebecca McConnell, 2026-07).
    # An IADNR also requires that the physio it gets attributed to actually SAW
    # the patient. If the booked-with physio never had them in front of them,
    # the drop is pre-IA (IACNA/IADNA) — it belongs on the list for reception,
    # but not against anyone's clinical stats. An established patient stays a
    # plain review CNA/DNA as before.
    if is_cancelled:
        if still_booked_in(appt, history):
            return None  # reschedule — still has care booked in the diary
        if no_attendance_this_episode(appt, history):
            return "iacna"
        if not is_ia_only_patient_at(appt, history):
            return "cancelled"
        return "iadnr" if responsible_physio_attended(appt, history) else "iacna"

    if is_dna:
        if no_attendance_this_episode(appt, history):
            return "iadna"
        if not is_ia_only_patient_at(appt, history):
            return "did_not_attend"
        return "iadnr" if responsible_physio_attended(appt, history) else "iadna"

    return None  # attended non-IA — not a drop-off


# ---------- Formatting helpers ----------

def humanise_notice(hours):
    if hours is None or hours == "":
        return ""
    if hours <= 0:
        return "0"
    if hours < 1:
        m = int(round(hours * 60))
        return f"{m} minute{'s' if m != 1 else ''}"
    if hours < 24:
        h = int(round(hours))
        return f"{h} hour{'s' if h != 1 else ''}"
    d = int(round(hours / 24))
    return f"{d} day{'s' if d != 1 else ''}"


def full_patient_name(name):
    return (name or "?").strip()


def fmt_local_dt(ts):
    dt = parse_iso(ts)
    return dt.astimezone(LONDON).strftime("%Y-%m-%d %H:%M") if dt else ""


def business_name(biz):
    if not biz:
        return ""
    for k in ("business_name", "label", "name", "display_name"):
        v = biz.get(k)
        if v:
            return v
    return f"business#{biz.get('id', '?')}"


def week_tab_name(appointment_date_str):
    """E.g. '2026-05-08 14:30' → 'W/C 04 May 2026'."""
    dt = datetime.strptime(appointment_date_str, "%Y-%m-%d %H:%M")
    monday = dt - timedelta(days=dt.weekday())
    return f"W/C {monday.strftime('%d %b %Y')}"


def dropoff_event_dt(row):
    """When the drop-off event happened — used for sorting AND W/C tab grouping.

    Use cancellation_date if set (covers cancelled, iacna, and iadnr-via-cancellation
    — all three are 'patient cancelled, reception sees today'). Otherwise use
    appointment_date (covers DNAs and attended-IA-with-no-rebook).
    """
    s = row.get("cancellation_date") or row.get("appointment_date") or ""
    return datetime.strptime(s, "%Y-%m-%d %H:%M") if s else datetime.min


# ---------- Core pipeline ----------

def collect_dropoffs(date_override=None, lookback_days=None, skip_appointment_ids=None):
    """Collect drop-off rows.

    - date_override="YYYY-MM-DD": just that calendar day (manual runs / backfill)
    - lookback_days=N: rolling window of the last N days (the daily cron uses this,
      so late-marked DNAs / late-logged cancellations get picked up on a later run)
    - skip_appointment_ids: appointment IDs already in the sheet — skipped before any
      history fetch / AI call, so re-scanning is cheap.
    """
    skip_appointment_ids = skip_appointment_ids or set()
    if date_override:
        start_utc, end_utc = day_london_window_utc(date_override)
    elif lookback_days:
        now_london = datetime.now(LONDON)
        today_start = now_london.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = (today_start - timedelta(days=lookback_days)).astimezone(timezone.utc)
        end_utc = today_start.astimezone(timezone.utc)
    else:
        start_utc, end_utc = yesterday_london_window_utc()
    s_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    pulled_at = datetime.now(LONDON).strftime("%Y-%m-%d %H:%M")

    print(f"Window: Europe/London  "
          f"({start_utc.astimezone(LONDON).strftime('%Y-%m-%d')} → "
          f"{end_utc.astimezone(LONDON).strftime('%Y-%m-%d')})  "
          f"already-in-sheet to skip: {len(skip_appointment_ids)}")

    types_by_id = {str(t["id"]): t for t in fetch_all("/appointment_types")}
    pracs_by_id = all_practitioners()   # incl. inactive — leavers keep their name
    biz_by_id = {str(b["id"]): b for b in fetch_all("/businesses")}

    # Two queries:
    #   A) cancellations MADE in window (any appointment date) — drives reactivation workflow
    #   B) appointments STARTING in window (default excludes cancelled) — for DNAs + attended IAs
    cancelled = list(fetch_all("/individual_appointments", [
        ("q[]", f"cancelled_at:>={s_iso}"),
        ("q[]", f"cancelled_at:<{e_iso}"),
    ]))
    in_window = list(fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={s_iso}"),
        ("q[]", f"starts_at:<{e_iso}"),
    ]))
    by_id = {a["id"]: a for a in in_window}
    for a in cancelled:
        by_id[a["id"]] = a  # cancelled record takes priority (has cancelled_at set)
    appts = list(by_id.values())
    print(f"Cliniko: cancellations_in_window={len(cancelled)}  "
          f"appts_starting_in_window={len(in_window)}  unique={len(appts)}")

    import phase2 as p2
    history_cache = {}

    # Bulk cancellations that ALREADY have a row from an earlier run. Keyed on the
    # Cliniko patient id — never the patient name, because duplicate patient records
    # share names. Built from the window we just fetched, so it costs no extra calls.
    #
    # Without this, the rolling re-scan skips the one appointment already in the sheet
    # and lets the NEXT appointment of the same bulk cancel through the dedup below —
    # leaking one more row, and one more Slack DM, every single morning.
    already_logged_keys = set()
    for a in appts:
        if str(a.get("id")) not in skip_appointment_ids or not a.get("cancelled_at"):
            continue
        logged_pid = id_from_link(a.get("patient"))
        if logged_pid:
            already_logged_keys.add((logged_pid, fmt_local_dt(a["cancelled_at"])[:10]))

    rows = []
    excluded_reschedules = []
    for a in appts:
        # Already captured in a previous run — skip before any expensive work
        if str(a.get("id")) in skip_appointment_ids:
            continue

        type_id = id_from_link(a.get("appointment_type"))
        is_cancelled = bool(a.get("cancelled_at"))
        is_dna = bool(a.get("did_not_arrive"))

        # Attended non-IA appointment is never a drop-off — skip before history fetch.
        # Uses the wider IA set (incl Club Consultation etc.) so we don't skip
        # an attended wider-IA that might be an IADNR.
        if not is_cancelled and not is_dna and type_id not in _IA_TYPES_FOR_CLASSIFY:
            continue

        biz_id = id_from_link(a.get("business"))
        patient_id = id_from_link(a.get("patient"))

        type_name = (types_by_id.get(type_id) or {}).get("name", "?")
        clinic = business_name(biz_by_id.get(biz_id) or {})
        patient = full_patient_name(a.get("patient_name"))
        appt_date = fmt_local_dt(a.get("starts_at"))

        notice_hours_val = ""
        if is_cancelled:
            c = parse_iso(a["cancelled_at"])
            s = parse_iso(a.get("starts_at"))
            if c and s:
                notice_hours_val = max((s - c).total_seconds() / 3600, 0.0)
        elif is_dna:
            notice_hours_val = 0

        # Fetch patient history once (cached) — needed for classification AND
        # for the responsible-physio rule (most recent attending physio).
        # None (not []) marks a FAILED fetch so we skip rather than misclassify.
        if patient_id and patient_id not in history_cache:
            try:
                history_cache[patient_id] = p2.fetch_patient_full_history(patient_id)
            except Exception as e:
                print(f"  WARN history fetch failed for {patient}: {e} "
                      f"— skipping, next run's re-scan will retry")
                history_cache[patient_id] = None
        history = history_cache.get(patient_id)
        if history is None:
            continue  # fetch failed — leave for the next run's rolling re-scan

        # Responsible physio = most recent attending physio (NOT the scheduled
        # physio of the cancelled appointment). Falls back to scheduled physio
        # if the patient has no prior attended history (IACNA/IADNA case).
        prac_id = responsible_physio_id(a, history)
        prac = pracs_by_id.get(prac_id) or {}

        kind = classify_dropoff(a, type_id, history)
        if kind is None:
            # Either: reschedule (cancelled + future booking) OR attended IA with future booking
            if is_cancelled:
                excluded_reschedules.append((patient, appt_date, type_name))
            continue

        rows.append({
            "appointment_date": appt_date,
            "cancellation_date": fmt_local_dt(a.get("cancelled_at")) if is_cancelled else "",
            "patient": patient,
            "physio": f"{prac.get('first_name','?')} {prac.get('last_name','')}".strip(),
            "clinic": clinic,
            "appointment_type": type_name,
            "dropoff_type": kind,
            "notice": humanise_notice(notice_hours_val),
            "cancellation_reason": a.get("cancellation_reason_description") or "" if is_cancelled else "",
            "session_number": "",
            "body_area": "",
            "clinical_non_clinical": "",
            "next_step_physio": "",
            "reactivation_status": "pending",
            "reactivation_notes": "",
            "martys_comments": "",
            "actioned": "",
            "appointment_id": str(a.get("id")),
            "pulled_at": pulled_at,
            # internal-only (not written to sheet):
            "_patient_id": patient_id,
            "_appointment_type_id": type_id,
        })

    # Same-day bulk-cancel dedup — if one patient cancels multiple future appts in a single
    # call, keep only the row for the EARLIEST upcoming appointment. Reception calls them once.
    rows = _dedup_same_day_cancellations(rows, already_logged_keys=already_logged_keys)
    return rows, excluded_reschedules


def _dedup_same_day_cancellations(rows, already_logged_keys=None):
    """Keep one row per (patient_id, cancellation date) — the one for the earliest appt.

    `already_logged_keys` is the set of (patient_id, YYYY-MM-DD) bulk cancellations that
    already have a row from an EARLIER run. Without it this dedup only sees the current
    run's new rows: the daily re-scan skips the appointment already in the sheet, so the
    next-earliest appointment of the SAME bulk cancellation survives the dedup and gets
    written — leaking one more row, and one more Slack DM, every morning. That's how one
    7 Jul bulk cancel DM'd Martin about Niamh O'Donnell on the 8th, 9th and 10th as
    sessions 57, 60 and 61."""
    already_logged_keys = already_logged_keys or set()
    by_key = {}
    others = []
    for r in rows:
        canc_date = r.get("cancellation_date") or ""
        pid = r.get("_patient_id")
        if not canc_date or not pid:
            others.append(r)
            continue
        if (pid, canc_date[:10]) in already_logged_keys:
            continue  # this bulk cancellation already has a row from an earlier run
        key = (pid, canc_date[:10])  # group by patient + cancellation calendar day
        existing = by_key.get(key)
        if existing is None or r["appointment_date"] < existing["appointment_date"]:
            by_key[key] = r
    return list(by_key.values()) + others


def enrich_phase2(rows):
    """Fill session_number and body_area on each row using the phase2 module.
    Idempotent — safe to call multiple times. Skips rows that already have values."""
    import phase2 as p2
    for r in rows:
        pid = r.get("_patient_id")
        if not pid:
            continue
        try:
            history = p2.fetch_patient_full_history(pid)
            anchor, episode, _ = p2.find_episode(history)
        except Exception as e:
            print(f"  WARN history fetch failed for {r['patient']}: {e}")
            continue

        # Session count
        if not r.get("session_number"):
            r["session_number"] = p2.session_number_for(r["appointment_id"], episode) or ""

        # Body area — IACNA / IADNA: patient never attended their IA, no notes to read.
        # Uses the wider IA set so cancellation/DNA of any IA-style appointment
        # (incl. Club Consultation) is labelled "n/a (pre-IA)", matching the
        # widened classify_dropoff rule.
        appt_type_id = r.get("_appointment_type_id")
        is_pre_ia = (r["dropoff_type"] in ("cancelled", "did_not_attend")
                     and appt_type_id in _IA_TYPES_FOR_CLASSIFY)
        if is_pre_ia:
            r["body_area"] = "n/a (pre-IA)"
            continue

        if not r.get("body_area") and anchor:
            anchor_dt = p2.parse_iso(anchor["starts_at"])
            notes_text, n_notes, fallback = p2.build_episode_notes_text(pid, anchor_dt)
            if n_notes > 0 and notes_text.strip():
                try:
                    body_area, _path, _usage = p2.categorise_episode_notes(notes_text)
                    if body_area:
                        r["body_area"] = (
                            f"{body_area} (legacy notes)" if fallback else body_area
                        )
                    else:
                        r["body_area"] = "(uncategorised)"
                except Exception as e:
                    print(f"  WARN AI call failed for {r['patient']}: {e}")
                    r["body_area"] = "(ai error)"
            else:
                r["body_area"] = "n/a (no notes)"


def print_preview(rows, excluded_reschedules):
    types = ("iacna", "iadna", "iadnr", "cancelled", "did_not_attend")
    counts = {k: sum(1 for r in rows if r["dropoff_type"] == k) for k in types}
    print("Drop-offs: " + "  ".join(f"{k}={counts[k]}" for k in types) + f"  total={len(rows)}")
    if excluded_reschedules:
        print("Excluded as reschedules (cancelled + has future booking):")
        for patient, when, type_name in excluded_reschedules:
            print(f"  - {patient} | {when} | {type_name}")
    if not rows:
        return
    visible = [c for c in SHEET_COLUMNS if c not in HIDDEN_COLUMNS]
    widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in visible}
    sep = "  "
    print()
    print("  " + sep.join(h.ljust(widths[h]) for h in visible))
    print("  " + sep.join("-" * widths[h] for h in visible))
    for r in sorted(rows, key=dropoff_event_dt):
        print("  " + sep.join(str(r[h]).ljust(widths[h]) for h in visible))


# ---------- Sheets writer ----------

def _sheets_credentials():
    """Google Sheets credentials. Prefers the SERVICE_ACCOUNT_JSON env var
    (used in the cloud / on Render); falls back to the local
    service_account.json file for running on the Mac."""
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=SHEETS_SCOPES)
    return Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SHEETS_SCOPES)


def open_spreadsheet():
    gc = gspread.authorize(_sheets_credentials())
    return gc.open_by_key(SPREADSHEET_ID)


def get_or_create_tab(sh, tab_name):
    try:
        ws = sh.worksheet(tab_name)
        # Self-heal: re-assert the dropdown + colour rules on the active tab.
        # If a previous run created the tab but died before formatting (leaving
        # column N with no dropdown / no colours), this repairs it next run.
        # apply_dropoff_tab_formatting is idempotent, so this never duplicates.
        try:
            apply_dropoff_tab_formatting(ws)
        except Exception as e:
            print(f"  WARN couldn't re-apply formatting to '{tab_name}': {e}")
        return ws, False
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=200, cols=len(SHEET_COLUMNS))
        ws.append_row([HEADER_LABELS[c] for c in SHEET_COLUMNS], value_input_option="RAW")
        first_hidden_idx = SHEET_COLUMNS.index(HIDDEN_COLUMNS[0])
        ws.hide_columns(first_hidden_idx, len(SHEET_COLUMNS))
        apply_dropoff_tab_formatting(ws)
        return ws, True


# ---------- Leads sheet (separate spreadsheet) ----------

LEADS_STATUS_COL_LETTER = "I"   # column we add to the Leads tab
LEADS_STATUS_VALUES = ["pending", "booked", "declined", "lost"]


def open_leads_spreadsheet():
    import config
    return gspread.authorize(_sheets_credentials()).open_by_key(config.LEADS_SPREADSHEET_ID)


def apply_leads_tab_formatting(ws):
    """Dropdown + colour rules for the Status column on the Leads tab.
      booked → green, lost → orange, declined → red, pending → no colour.
    """
    sh = ws.spreadsheet
    sheet_id = ws.id
    status_col_idx = ord(LEADS_STATUS_COL_LETTER) - ord("A")  # 8 (0-indexed)
    num_cols = status_col_idx + 1  # A..I

    full_row_range = {
        "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000,
        "startColumnIndex": 0, "endColumnIndex": num_cols,
    }
    status_col_range = {
        "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000,
        "startColumnIndex": status_col_idx, "endColumnIndex": status_col_idx + 1,
    }

    rules = [
        ("booked",   {"red": 0.74, "green": 0.93, "blue": 0.78}),  # green
        ("lost",     {"red": 1.00, "green": 0.87, "blue": 0.70}),  # orange
        ("declined", {"red": 0.96, "green": 0.78, "blue": 0.78}),  # red
    ]
    requests = []
    for i, (value, color) in enumerate(rules):
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [full_row_range],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue":
                                       f'=${LEADS_STATUS_COL_LETTER}2="{value}"'}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
                "index": i,
            }
        })
    requests.append({
        "setDataValidation": {
            "range": status_col_range,
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in LEADS_STATUS_VALUES],
                },
                "showCustomUi": True,
                "strict": False,
            }
        }
    })
    sh.batch_update({"requests": requests})


def leads_pipeline_summary():
    """Return per-status counts of leads dated within the CURRENT week.

    Week = Sunday→Saturday (Europe/London), matching the leads/bookings sheet
    convention. We filter by the lead's Date because the Leads tab is no longer
    wiped weekly (the bookings system now owns that tab) — so without this filter
    the counts would accumulate across all weeks.
    Returns {pending, booked, declined, lost, total, not_booked}."""
    sh = open_leads_spreadsheet()
    try:
        ws = sh.worksheet("Leads")
    except gspread.exceptions.WorksheetNotFound:
        return None
    records = ws.get_all_records()

    today = datetime.now(LONDON).date()
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)  # back to Sunday
    week_end = week_start + timedelta(days=7)

    def _lead_date(raw):
        raw = str(raw or "").strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d %b %Y", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt).date()
            except ValueError:
                continue
        return None

    counts = {"pending": 0, "booked": 0, "declined": 0, "lost": 0}
    for r in records:
        # Skip fully-empty rows
        if not any(str(v).strip() for v in r.values()):
            continue
        d = _lead_date(r.get("Date"))
        if not d or not (week_start <= d < week_end):
            continue  # only count THIS week's leads
        status = str(r.get("Status") or "").strip().lower() or "pending"
        if status in counts:
            counts[status] += 1
    total = sum(counts.values())
    counts["total"] = total
    counts["not_booked"] = total - counts["booked"]
    return counts


def weekly_leads_wipe():
    """Archive the current Leads tab content into a 'Leads Archive' tab, then
    clear the Leads tab so a fresh week starts. Runs every Sunday."""
    sh = open_leads_spreadsheet()
    try:
        leads_ws = sh.worksheet("Leads")
    except gspread.exceptions.WorksheetNotFound:
        print("  Leads tab not found — skipping wipe")
        return

    all_rows = leads_ws.get_all_values()
    if len(all_rows) <= 1:
        print("  Leads tab empty — nothing to archive")
        return

    header = all_rows[0]
    data_rows = [row for row in all_rows[1:] if any(c.strip() for c in row)]
    if not data_rows:
        print("  Leads tab empty — nothing to archive")
        return

    # Get or create archive tab
    try:
        archive_ws = sh.worksheet("Leads Archive")
        archive_existing_count = len(archive_ws.col_values(1)) - 1  # minus header
    except gspread.exceptions.WorksheetNotFound:
        # Create with header (original cols + Week Archived)
        archive_ws = sh.add_worksheet(title="Leads Archive",
                                      rows=2000, cols=len(header) + 1)
        archive_ws.update(values=[header + ["Week Archived"]],
                          range_name="A1", value_input_option="RAW")
        archive_existing_count = 0

    today_str = datetime.now(LONDON).strftime("%Y-%m-%d")
    # Pad rows to header length, then append Week Archived
    padded = [row + [""] * (len(header) - len(row)) + [today_str] for row in data_rows]
    archive_ws.append_rows(padded, value_input_option="RAW")

    # Clear the Leads tab (keep header + formatting)
    last_row = len(all_rows)
    leads_ws.batch_clear([f"A2:Z{last_row}"])
    print(f"  Archived {len(padded)} leads (total in archive: "
          f"{archive_existing_count + len(padded)}); cleared Leads tab")


def write_dashboard_lead_conversion(months_back=12):
    """Write a per-month lead conversion table to the Dashboard tab.

    For each month: Total IAs booked (from Cliniko NPs), Leads Not Booked
    (from archive: pending + declined + lost), Total Inquiries (sum),
    Conversion %. Direct bookings (most NPs) are correctly included since
    NPs come from Cliniko (all IAs booked), not from the leads tab.
    """
    import phase2 as p2
    sh = open_leads_spreadsheet()
    # Pull leads archive grouped by month (use Week Archived as the bucket)
    archive_by_month = {}  # 'YYYY-MM' -> list of status strings
    try:
        archive_ws = sh.worksheet("Leads Archive")
        for r in archive_ws.get_all_records():
            week_arch = str(r.get("Week Archived") or "")
            if len(week_arch) < 7:
                continue
            ym = week_arch[:7]
            status = str(r.get("Status") or "pending").strip().lower()
            archive_by_month.setdefault(ym, []).append(status)
    except gspread.exceptions.WorksheetNotFound:
        pass

    # Get or create Dashboard tab
    try:
        dash = sh.worksheet("Dashboard")
    except gspread.exceptions.WorksheetNotFound:
        dash = sh.add_worksheet(title="Dashboard", rows=200, cols=10)

    # Build last N months
    now = datetime.now(LONDON)
    months = []
    y, m = now.year, now.month
    for _ in range(months_back):
        months.append((y, m))
        m -= 1
        if m < 1:
            m = 12; y -= 1

    out = []
    out.append(["Lead Conversion (rolling)"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"])
    out.append([])
    out.append(["Month", "IAs Booked (clinic)", "Leads Not Booked",
                "Total Inquiries", "Conversion %"])
    for y, m in months:
        ym = f"{y:04d}-{m:02d}"
        # NPs (IAs booked) for this month from Cliniko
        start = datetime(y, m, 1, tzinfo=LONDON)
        end = (datetime(y + 1, 1, 1, tzinfo=LONDON) if m == 12
               else datetime(y, m + 1, 1, tzinfo=LONDON))
        try:
            physio_stats = p2.monthly_stats_per_physio(
                start.astimezone(timezone.utc), end.astimezone(timezone.utc)
            )
            ias_booked = sum(s["nps"] for s in physio_stats.values())
        except Exception as e:
            print(f"  WARN couldn't fetch NPs for {ym}: {e}")
            ias_booked = 0
        archived = archive_by_month.get(ym, [])
        not_booked = sum(1 for s in archived if s != "booked")
        total = ias_booked + not_booked
        rate = f"{(ias_booked / total * 100):.0f}%" if total else "—"
        out.append([f"{start.strftime('%b %Y')}", ias_booked, not_booked, total, rate])

    out.append([])
    out.append(["Definitions:"])
    out.append(["  IAs Booked = total New Patient appointments attended in the month (from Cliniko — includes both direct bookings and leads that converted)."])
    out.append(["  Leads Not Booked = leads logged in the Leads tab that week and ended the week as pending / declined / lost (from Leads Archive)."])
    out.append(["  Total Inquiries = IAs Booked + Leads Not Booked."])
    out.append(["  Conversion % = IAs Booked / Total Inquiries."])

    dash.batch_clear(["A1:Z200"])
    dash.update(values=out, range_name="A1", value_input_option="RAW")
    print(f"  Dashboard 'Lead Conversion' updated ({len(months)} months)")


def setup_leads_tab():
    """One-off setup of the Leads tab: ensure Status column exists, migrate any
    existing rows where Notes contains BOOKED → Status=booked, apply colours +
    dropdown. Idempotent — safe to re-run."""
    sh = open_leads_spreadsheet()
    try:
        ws = sh.worksheet("Leads")
    except gspread.exceptions.WorksheetNotFound:
        print("  Leads tab not found")
        return
    header = ws.row_values(1)
    status_col_1based = ord(LEADS_STATUS_COL_LETTER) - ord("A") + 1
    # Ensure the sheet is wide enough for the Status column
    if ws.col_count < status_col_1based:
        ws.add_cols(status_col_1based - ws.col_count)
    if len(header) < status_col_1based or header[status_col_1based - 1] != "Status":
        ws.update(values=[["Status"]],
                  range_name=f"{LEADS_STATUS_COL_LETTER}1",
                  value_input_option="RAW")
        print(f"  Added 'Status' header at column {LEADS_STATUS_COL_LETTER}")

    notes_col = ws.col_values(8)  # H = Notes
    status_col_vals = ws.col_values(status_col_1based)
    pending_set = 0
    booked_set = 0
    for row_idx, notes in enumerate(notes_col, start=1):
        if row_idx == 1:
            continue
        existing_status = (status_col_vals[row_idx - 1]
                           if row_idx - 1 < len(status_col_vals) else "")
        if existing_status:
            continue
        # Only set status on rows that have any data (skip blank rows)
        row_vals = ws.row_values(row_idx)
        if not any(v.strip() for v in row_vals):
            continue
        if "BOOKED" in (notes or "").upper():
            ws.update_cell(row_idx, status_col_1based, "booked")
            booked_set += 1
        else:
            ws.update_cell(row_idx, status_col_1based, "pending")
            pending_set += 1
    print(f"  Set Status on {booked_set + pending_set} rows "
          f"({booked_set} booked, {pending_set} pending)")
    apply_leads_tab_formatting(ws)
    print(f"  Applied colour rules + dropdown to Leads tab")


def apply_dropoff_tab_formatting(ws):
    """Apply Reactivation Status dropdown + row colour rules to a W/C drop-off tab.

    Colours (whole row, based on column N — Reactivation Status):
      • reactivated → green   (auto-set by detect_rebookings)
      • contact_attempted → orange  (reception sets manually)
      • leave → red  (reception sets manually OR physio via 'Not Appropriate' button)
      • pending → no colour (default)
    """
    sh = ws.spreadsheet
    sheet_id = ws.id
    num_cols = len(SHEET_COLUMNS)
    status_col_idx = SHEET_COLUMNS.index("reactivation_status")
    status_letter = chr(ord("A") + status_col_idx)

    full_row_range = {
        "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 500,
        "startColumnIndex": 0, "endColumnIndex": num_cols,
    }
    status_col_range = {
        "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 500,
        "startColumnIndex": status_col_idx, "endColumnIndex": status_col_idx + 1,
    }

    rules = [
        ("reactivated",       {"red": 0.74, "green": 0.93, "blue": 0.78}),  # green
        ("contact_attempted", {"red": 1.00, "green": 0.87, "blue": 0.70}),  # orange
        ("leave",             {"red": 0.96, "green": 0.78, "blue": 0.78}),  # red
    ]

    # Idempotent: drop any conditional-format rules already on this tab before
    # re-adding, so re-running (self-heal) never stacks duplicate rules. This
    # function is the sole owner of conditional formatting on W/C drop-off tabs.
    try:
        meta = sh.fetch_sheet_metadata(params={
            "fields": "sheets(properties(sheetId),conditionalFormats)"})
        existing_cf = 0
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") == sheet_id:
                existing_cf = len(s.get("conditionalFormats", []) or [])
                break
    except Exception as e:
        print(f"  WARN couldn't read existing conditional formats: {e}")
        existing_cf = 0

    requests = []
    for idx in range(existing_cf - 1, -1, -1):   # delete high→low so indices stay valid
        requests.append({"deleteConditionalFormatRule":
                         {"sheetId": sheet_id, "index": idx}})
    for i, (value, color) in enumerate(rules):
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [full_row_range],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": f'=${status_letter}2="{value}"'}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
                "index": i,
            }
        })
    # Dropdown for the status column — keeps reception's values typo-free
    requests.append({
        "setDataValidation": {
            "range": status_col_range,
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [
                        {"userEnteredValue": "pending"},
                        {"userEnteredValue": "contact_attempted"},
                        {"userEnteredValue": "leave"},
                        {"userEnteredValue": "reactivated"},
                    ],
                },
                "showCustomUi": True,
                "strict": False,
            }
        }
    })
    sh.batch_update({"requests": requests})


def cell_for(row, col):
    val = row.get(col, "") or ""
    if col == "patient":
        pid = row.get("_patient_id")
        name = str(val).replace('"', '""')
        if pid:
            return f'=HYPERLINK("https://{CLINIKO_WEB_DOMAIN}/patients/{pid}", "{name}")'
    return str(val)


def write_ia_rebook_rate_tab(months_back=3):
    """Refresh IA Rebook Rate tab — current MTD + previous N full months, stacked top-down."""
    import phase2 as p2
    now = datetime.now(LONDON)

    # Build periods: current MTD + previous N full months
    periods = []
    mtd_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    periods.append((mtd_start, now,
                    f"{mtd_start.strftime('%B %Y')} — month-to-date as of {now.strftime('%d %b')}"))
    y, m = now.year, now.month
    for _ in range(months_back):
        m -= 1
        if m < 1:
            m = 12; y -= 1
        start = datetime(y, m, 1, tzinfo=LONDON)
        if m == 12:
            end = datetime(y + 1, 1, 1, tzinfo=LONDON)
        else:
            end = datetime(y, m + 1, 1, tzinfo=LONDON)
        periods.append((start, end, f"{start.strftime('%B %Y')} (settled)"))

    out = []
    out.append(["IA Rebook Rate"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"])
    out.append([])

    for start, end, label in periods:
        try:
            result = p2.ia_rebook_rate_for_window(
                start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        except Exception as e:
            print(f"  WARN rebook rate calc failed for {label}: {e}")
            continue

        out.append([f"=== {label} ==="])
        out.append(["Physio", "IAs", "Rebooked", "Rate"])
        if not result["per_physio"]:
            out.append(["(no IAs in this window)", "", "", ""])
        else:
            for _, s in sorted(result["per_physio"].items(), key=lambda x: -x[1]["ias"]):
                rate_pct = f"{s['rate'] * 100:.0f}%" if s["rate"] is not None else "—"
                out.append([s["name"], s["ias"], s["rebooked"], rate_pct])
            c = result["clinic"]
            crate = f"{c['rate'] * 100:.0f}%" if c["rate"] is not None else "—"
            out.append(["CLINIC TOTAL", c["ias"], c["rebooked"], crate])
        out.append([f"Pre-IA drop-offs (excluded): "
                    f"{result['iacna_count']} IACNA, {result['iadna_count']} IADNA"])
        out.append([])

    out.append(["Definitions:"])
    out.append(["  Rebooked = patient has ATTENDED a follow-up OR has an active (non-cancelled) future booking after the IA. A booked 2nd visit that was later DNA'd or cancelled does NOT count."])
    out.append(["  IACNA = IA cancelled before attending.  IADNA = IA did-not-attend."])
    out.append(["  IACNAs and IADNAs are excluded from numerator AND denominator (physio never saw patient)."])
    out.append(["  ⏳ PROVISIONAL: the current/most-recent period looks artificially HIGH — many patients book a 2nd visit but then CNA/DNA it before attending. This figure is recomputed every day, so it SETTLES (drops) over the following 1-2 weeks. Review the settled figure after ~2 weeks."])

    # All compute succeeded — now touch the sheet atomically.
    sh = open_spreadsheet()
    try:
        ws = sh.worksheet("IA Rebook Rate")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="IA Rebook Rate", rows=300, cols=6)

    ws.update(values=out, range_name="A1", value_input_option="RAW")
    return ws


GREEN_RGB = {"red": 0.74, "green": 0.93, "blue": 0.78}
YELLOW_RGB = {"red": 1.0, "green": 0.95, "blue": 0.7}
RED_RGB = {"red": 0.96, "green": 0.78, "blue": 0.78}


def _colour_for(metric, value):
    """Return GREEN / YELLOW / RED RGB dict (or None) for a metric value vs. standard."""
    if value is None or value == "":
        return None
    if metric == "utilization_pct":
        if 75 <= value <= 85:
            return GREEN_RGB
        if 67.5 <= value <= 93.5:
            return YELLOW_RGB
        return RED_RGB
    if metric in ("pva", "gen_pop_pva"):
        if value >= 6:
            return GREEN_RGB
        if value >= 5.4:
            return YELLOW_RGB
        return RED_RGB
    if metric == "nps_clinic":
        if value >= 192:
            return GREEN_RGB
        if value >= 172.8:
            return YELLOW_RGB
        return RED_RGB
    if metric == "dna_pct":
        if value < 2:
            return GREEN_RGB
        if value <= 2.2:
            return YELLOW_RGB
        return RED_RGB
    if metric == "cna_pct":
        if value < 8:
            return GREEN_RGB
        if value <= 8.8:
            return YELLOW_RGB
        return RED_RGB
    if metric == "combined_pct":
        if value < 10:
            return GREEN_RGB
        if value <= 11:
            return YELLOW_RGB
        return RED_RGB
    if metric == "cna_dna_1st_pct":
        if value < 2:
            return GREEN_RGB
        if value <= 2.2:
            return YELLOW_RGB
        return RED_RGB
    if metric == "ia_rebook_pct":
        if value >= 85:
            return GREEN_RGB
        if value >= 76.5:
            return YELLOW_RGB
        return RED_RGB
    if metric == "clinic_rebook_pct":
        # Same gold standard as the Weekly Snapshot's Clinic Rebook % (≥85%).
        if value >= 85:
            return GREEN_RGB
        if value >= 76.5:
            return YELLOW_RGB
        return RED_RGB
    if metric == "dropoff_pct":
        if value < 10:
            return GREEN_RGB
        if value <= 11:
            return YELLOW_RGB
        return RED_RGB
    if metric == "net_promoter":
        # Standard ≥85 (per the dashboard's "Net Promoter ≥85%" gold standard).
        if value >= 85:
            return GREEN_RGB
        if value >= 76.5:
            return YELLOW_RGB
        return RED_RGB
    return None


def compute_nps_by_physio(start_dt, end_dt):
    """Per-physio Net Promoter Score from the marketing sheet's
    NPS - Raw Data tab. NPS = % promoters (9-10) − % detractors (0-6).

    Date filter uses Date Responded (col M), falling back to Date Sent (col A).
    Physio column on the NPS sheet is the DISPLAY name (e.g. "Molaí"), so it
    joins directly with the Performance Dashboard's display rows.

    Returns: {display_name: {"nps": int | None, "responses": int,
                              "promoters": int, "passives": int,
                              "detractors": int}}.  Empty dict on failure
    (marketing sheet not configured, no responses yet, etc.) — the dashboard
    falls back to '—' for the Net Promoter cell when a physio has no data.
    """
    try:
        from marketing.sheets import tab
        ws = tab("NPS - Raw Data")
        rows = ws.get_all_values()[1:]
    except Exception as e:
        print(f"  WARN: NPS data unavailable ({e})")
        return {}

    from datetime import datetime as _dt

    def _parse(s):
        s = (s or "").strip()
        if not s:
            return None
        for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
            try:
                return _dt.strptime(s, fmt)
            except ValueError:
                continue
        return None

    start_d = start_dt.date() if hasattr(start_dt, "date") else start_dt
    end_d = end_dt.date() if hasattr(end_dt, "date") else end_dt

    raw = {}
    for r in rows:
        if len(r) < 13:
            continue
        physio = (r[3] or "").strip()
        score_str = (r[6] or "").strip()
        if not physio or not score_str:
            continue
        d = _parse(r[12]) or _parse(r[0])
        if d and not (start_d <= d.date() < end_d):
            continue
        try:
            score = int(float(score_str))
        except ValueError:
            continue
        bucket = raw.setdefault(physio, {"promoters": 0, "passives": 0, "detractors": 0, "responses": 0})
        bucket["responses"] += 1
        if score >= 9:
            bucket["promoters"] += 1
        elif score >= 7:
            bucket["passives"] += 1
        else:
            bucket["detractors"] += 1

    out = {}
    for phys, c in raw.items():
        total = c["responses"]
        if total == 0:
            out[phys] = {"nps": None, "responses": 0, **c}
            continue
        nps = round((c["promoters"] - c["detractors"]) / total * 100)
        out[phys] = {"nps": nps, "responses": total, **c}
    return out


def write_performance_dashboard_tab():
    """Build the Performance Dashboard tab: per-physio monthly KPIs stacked top-down.

    Replicates Martin's existing manual tracker layout. Rolls April + May 2026
    (only data available currently) with most recent on top.
    """
    import phase2 as p2
    import config
    now = datetime.now(LONDON)

    # Build month list: April + May 2026
    # (more months will accrue as time passes; defaults to current + last month for now)
    def month_window(y, m):
        s = datetime(y, m, 1, tzinfo=LONDON)
        if m == 12:
            e = datetime(y + 1, 1, 1, tzinfo=LONDON)
        else:
            e = datetime(y, m + 1, 1, tzinfo=LONDON)
        return s, e

    # Rolling 3-month view: month-before-previous, previous, current.
    # E.g. in June we show April + May + June; from 1 July it rolls to
    # May + June + July automatically.
    months = []
    for offset in (2, 1, 0):
        y, m = now.year, now.month - offset
        while m < 1:
            m += 12
            y -= 1
        months.append(month_window(y, m))
    months.reverse()  # most recent month on top

    # NOTE: we compute the full `out`/`format_cells` payload BELOW before
    # touching the sheet. The tab is only cleared+rewritten once all Cliniko
    # queries have succeeded, so a transient network/DNS failure can never
    # blank the tab (it just keeps yesterday's data and retries next run).

    # Standards row values (display strings)
    headers = ["Practitioner", "Utilization", "NPs", "DNA %", "CNA %", "DNA+CNA %",
               "IADNRs", "IA Rebook %", "Drop off %",
               "PVA", "CNA/DNA 1st %", "Net Promoter", "Gen Pop PVA", "Total Apts"]
    standard_vals = ["Standard", "75–85%", "≥192", "<2%", "<8%", "<10%",
                     "—", "≥85%", "<10%",
                     "≥6", "<2%", "≥85%", "≥6", "—"]

    out = []
    out.append(["Performance Dashboard"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"])
    out.append([])

    # We'll collect formatting requests as (row_index, col_index, rgb) and apply once
    format_cells = []

    def fmt_pct(v):
        return f"{v:.1f}%" if v is not None else "—"
    def fmt_num(v, places=1):
        return f"{v:.{places}f}" if v is not None else "—"
    def fmt_int(v):
        return v if v is not None else "—"

    def row_with_colours(row_idx, kpi_values_by_metric, label):
        """Append a row and stage cell colours."""
        cols = {
            "utilization_pct": 2,    # col B
            "nps_clinic": 3,         # col C — only coloured on clinic rows
            "dna_pct": 4,
            "cna_pct": 5,
            "combined_pct": 6,
            # IADNRs col 7 — count, no colour
            "ia_rebook_pct": 8,
            "dropoff_pct": 9,
            "pva": 10,
            "cna_dna_1st_pct": 11,
            "net_promoter": 12,
            "gen_pop_pva": 13,
        }
        for metric, col in cols.items():
            v = kpi_values_by_metric.get(metric)
            if metric == "nps_clinic":
                # NPs column is coloured only on the clinic-wide rows (Clinic Avg / w/o M&J)
                if label not in ("Clinic Average", "w/o M&J"):
                    continue
            rgb = _colour_for(metric, v)
            if rgb is not None:
                format_cells.append((row_idx, col, rgb))

    for start_local, end_local in months:
        month_label = start_local.strftime("%b-%y")
        out.append([month_label])

        # Header + Standards
        out.append(headers)
        out.append(standard_vals)

        # Compute per-physio stats
        stats_by_display = p2.monthly_stats_per_physio(
            start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc),
        )

        # Per-physio Net Promoter Score from the marketing NPS sheet (lives
        # in the separate "Elite Physio — NPS & Marketing" workbook).
        nps_by_physio = compute_nps_by_physio(start_local, end_local)

        # Compute Clinic Average and w/o M&J aggregates
        def aggregate(displays):
            agg = {"total_apts": 0, "nps": 0, "cnas_review": 0, "dnas_review": 0,
                   "iacnas": 0, "iadnas": 0, "iadnrs": 0, "used_minutes_total": 0,
                   "available_hours": 0,
                   "gen_pop_initial": 0, "gen_pop_review": 0,
                   "nps_promoters": 0, "nps_passives": 0, "nps_detractors": 0,
                   "nps_responses": 0}
            for d in displays:
                s = stats_by_display.get(d)
                if not s:
                    continue
                agg["total_apts"] += s["total_apts"]
                agg["nps"] += s["nps"]
                agg["cnas_review"] += s["cnas_review"]
                agg["dnas_review"] += s["dnas_review"]
                agg["iacnas"] += s["iacnas"]
                agg["iadnas"] += s["iadnas"]
                agg["iadnrs"] += s.get("iadnrs", 0)
                agg["used_minutes_total"] += s["used_minutes"]
                if s.get("available_hours"):
                    agg["available_hours"] += s["available_hours"]
                agg["gen_pop_initial"] += s["gen_pop_initial"]
                agg["gen_pop_review"] += s["gen_pop_review"]
                # Net Promoter — aggregate the underlying counts so we can
                # compute a properly-weighted NPS for clinic-average rows.
                n = nps_by_physio.get(d)
                if n:
                    agg["nps_promoters"] += n.get("promoters", 0)
                    agg["nps_passives"] += n.get("passives", 0)
                    agg["nps_detractors"] += n.get("detractors", 0)
                    agg["nps_responses"] += n.get("responses", 0)
            # Derived
            review = agg["total_apts"] - agg["nps"]
            agg["review_appts"] = review
            agg["dna_pct"] = (agg["dnas_review"] / review * 100) if review else None
            agg["cna_pct"] = (agg["cnas_review"] / review * 100) if review else None
            agg["combined_pct"] = ((agg["dnas_review"] + agg["cnas_review"]) / review * 100) if review else None
            agg["cna_dna_1st_pct"] = ((agg["iacnas"] + agg["iadnas"]) / agg["nps"] * 100) if agg["nps"] else None
            agg["pva"] = (agg["total_apts"] / agg["nps"]) if agg["nps"] else None
            agg["gen_pop_pva"] = ((agg["gen_pop_initial"] + agg["gen_pop_review"]) / agg["gen_pop_initial"]) if agg["gen_pop_initial"] else None
            # IA Rebook % — Martin's formula: (NPs − IADNRs) / NPs, computed
            # for the calendar month. Acknowledges that some IADNRs in the
            # month relate to IAs from the previous month; over a quarter or
            # year that timing noise averages out.
            agg["ia_rebook_pct"] = ((agg["nps"] - agg["iadnrs"]) / agg["nps"] * 100) if agg["nps"] else None
            # Drop off % — Martin's formula: Total Drop offs / (Total Drop offs
            # + Review Appts). "Total Drop offs" here = CNAs + DNAs + IADNRs
            # (matches the per-physio sheet totals, excludes IACNAs/IADNAs).
            total_drops = agg["cnas_review"] + agg["dnas_review"] + agg["iadnrs"]
            agg["total_dropoffs"] = total_drops
            denom = total_drops + review
            agg["dropoff_pct"] = (total_drops / denom * 100) if denom else None
            # Net Promoter — properly weighted across all physios in the agg.
            if agg["nps_responses"]:
                agg["net_promoter"] = round(
                    (agg["nps_promoters"] - agg["nps_detractors"])
                    / agg["nps_responses"] * 100
                )
            else:
                agg["net_promoter"] = None
            used_hrs = agg["used_minutes_total"] / 60
            agg["used_hours"] = round(used_hrs, 2)
            agg["util_pct"] = (used_hrs / agg["available_hours"] * 100) if agg["available_hours"] else None
            return agg

        clinic_avg = aggregate(config.PRACTITIONER_DISPLAY_ORDER)
        main_team = aggregate([d for d in config.PRACTITIONER_DISPLAY_ORDER
                              if d not in config.EXCLUDE_FROM_MAIN_TEAM])

        # Write clinic-wide rows
        def stat_row(label, s):
            row_idx = len(out) + 1  # 1-indexed sheet row
            # Net Promoter — clinic rows come from agg["net_promoter"]; per-physio
            # rows look up the physio's NPS in nps_by_physio.
            net_promoter_val = s.get("net_promoter")
            if net_promoter_val is None:
                net_promoter_val = (nps_by_physio.get(label) or {}).get("nps")
            net_promoter_str = (f"{net_promoter_val}" if net_promoter_val is not None else "—")
            out.append([
                label,
                fmt_pct(s["util_pct"]),
                fmt_int(s["nps"]),
                fmt_pct(s["dna_pct"]),
                fmt_pct(s["cna_pct"]),
                fmt_pct(s["combined_pct"]),
                fmt_int(s.get("iadnrs", 0)),
                fmt_pct(s.get("ia_rebook_pct")),
                fmt_pct(s.get("dropoff_pct")),
                fmt_num(s["pva"], 1),
                fmt_pct(s["cna_dna_1st_pct"]),
                net_promoter_str,
                fmt_num(s["gen_pop_pva"], 1),
                fmt_int(s["total_apts"]),
            ])
            colour_vals = {
                "utilization_pct": s["util_pct"],
                "nps_clinic": s["nps"],
                "dna_pct": s["dna_pct"],
                "cna_pct": s["cna_pct"],
                "combined_pct": s["combined_pct"],
                "ia_rebook_pct": s.get("ia_rebook_pct"),
                "dropoff_pct": s.get("dropoff_pct"),
                "pva": s["pva"],
                "cna_dna_1st_pct": s["cna_dna_1st_pct"],
                "net_promoter": net_promoter_val,
                "gen_pop_pva": s["gen_pop_pva"],
            }
            row_with_colours(row_idx, colour_vals, label)

        stat_row("Clinic Average", clinic_avg)
        stat_row("w/o M&J", main_team)

        # Per-physio rows in configured display order
        for display_name in config.PRACTITIONER_DISPLAY_ORDER:
            s = stats_by_display.get(display_name)
            if not s:
                # No data this month — still show row with zeros for clarity
                s = {
                    "util_pct": None, "nps": 0, "dna_pct": None, "cna_pct": None,
                    "combined_pct": None, "pva": None, "cna_dna_1st_pct": None,
                    "gen_pop_pva": None, "total_apts": 0,
                    "iadnrs": 0, "ia_rebook_pct": None, "dropoff_pct": None,
                }
            stat_row(display_name, s)

        out.append([])  # spacer between months

    out.append([])
    out.append(["Definitions:"])
    out.append(["  NPs = New Patients (broader 13 IA types incl Sports & MSK Consult, Mummy MOT, Pelvic Health, Ultrasound Assessment, Injury Update Testing, etc.)"])
    out.append(["  PVA = Total Appts ÷ NPs (per physio). Gold standard ≥ 6."])
    out.append(["  Gen Pop PVA = (1. Initial Appt attended + 2. Review Appt attended) ÷ 1. Initial Appt attended. Gold standard ≥ 6."])
    out.append(["  DNA % / CNA % = non-IA no-shows / cancellations ÷ Review Appointments (= Total Appts − NPs)."])
    out.append(["  CNA/DNA 1st % = (IACNA + IADNA) ÷ NPs = pre-IA drop-off rate. Gold standard < 2%."])
    out.append(["  Utilization % = (attended + DNA + group session hours) ÷ available hours per physio."])
    out.append([])
    out.append(["Colour key: green = meets standard, yellow = within 10% of standard, red = below."])

    # All compute succeeded — now (and only now) touch the sheet atomically.
    sh = open_spreadsheet()
    try:
        ws = sh.worksheet("Performance Dashboard")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Performance Dashboard", rows=400, cols=15)

    ws.update(values=out, range_name="A1", value_input_option="RAW")

    # Apply colour formatting in one batch_format call
    if format_cells:
        def col_letter(idx):
            return chr(ord("A") + idx - 1)
        batch = []
        for row_idx, col_idx, rgb in format_cells:
            cell_range = f"{col_letter(col_idx)}{row_idx}"
            batch.append({"range": cell_range, "format": {"backgroundColor": rgb}})
        ws.batch_format(batch)

    return ws


def write_weekly_snapshot_tab(weeks_back=1):
    """Refresh Weekly Snapshot tab — last N completed weeks, most recent first."""
    import phase2 as p2
    import config
    now = datetime.now(LONDON)

    # Most recent completed week = the Monday-to-Sunday block ending most recently
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Find last Sunday (or today if today is Sunday)
    days_since_sunday = (today.weekday() + 1) % 7  # Sunday=0 in this scheme
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    # Build list of completed weeks, most recent first
    periods = []
    monday = last_monday
    for _ in range(weeks_back):
        sunday = monday + timedelta(days=6)
        week_end_exclusive = sunday + timedelta(days=1)  # Mon 00:00 next week
        label = f"W/C {monday.strftime('%d %b %Y')}"
        periods.append((monday, week_end_exclusive, label))
        monday = monday - timedelta(days=7)

    out = []
    out.append(["Weekly Snapshot — clinic-wide"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}  |  "
                f"Clinic capacity: {config.CLINIC_WEEKLY_HOURS} hrs/wk"])
    out.append([])
    headers = [
        "Week", "IAs Performed", "IA Rebook %", "Total Appts", "Review Appts",
        "DNAs", "DNA %", "CNAs", "CNA %", "DNA+CNA %",
        "CNA/DNA 1st %", "Clinic Rebook %",
        "Used Hours", "Capacity", "Utilization %",
    ]
    out.append(headers)
    out.append([
        "Gold Standard", "—", "≥80%", "—", "—",
        "—", "—", "—", "—", "<10%",
        "<2%", "≥85%",
        "—", "—", "75-85%",
    ])

    def pct(v):
        return f"{v:.1f}%" if v is not None else "—"

    for week_start_local, week_end_local, label in periods:
        start_utc = week_start_local.astimezone(timezone.utc)
        end_utc = week_end_local.astimezone(timezone.utc)
        s = p2.weekly_clinic_stats(start_utc, end_utc)
        out.append([
            label,
            s["ias_performed"],
            pct(s["ia_rebook_pct"]),
            s["total_appts_seen"],
            s["review_appts"],
            s["dnas_review"],
            pct(s["dna_pct"]),
            s["cnas_review"],
            pct(s["cna_pct"]),
            pct(s["combined_pct"]),
            pct(s["cna_dna_1st_pct"]),
            pct(s["clinic_rebook_pct"]),
            s["used_hours"],
            s["capacity_hours"],
            pct(s["utilization_pct"]),
        ])

    out.append([])
    out.append(["Definitions:"])
    out.append(["  IAs Performed = all attended IA-type appointments (broader 8: Initial Appt, Club IA, PHI IA, ACL IA, Sports & MSK Consult, Mummy MOT, Pelvic Health, Club Consultation)."])
    out.append(["  IA Rebook % = of strict-4 IAs performed, how many ATTENDED a follow-up or have an active future booking. A booked 2nd visit later DNA'd or cancelled does NOT count."])
    out.append(["  ⏳ IA Rebook % is PROVISIONAL for recent weeks — it looks high at first because many 2nd visits are booked but then CNA'd/DNA'd before attending. It's recomputed daily and SETTLES (drops) over ~1-2 weeks, so review the settled figure for a week once it's ~2 weeks old."])
    out.append(["  Review Appts = Total Appts Seen − IAs Performed (the denominator for DNA/CNA rates)."])
    out.append(["  DNA % / CNA % = non-IA no-shows / cancellations, as % of Review Appts."])
    out.append(["  DNA+CNA % = combined rate (target <10%)."])
    out.append(["  CNA/DNA 1st % = (IACNA + IADNA) / IAs Performed = pre-IA drop-off rate (target <2%)."])
    out.append(["  Clinic Rebook % = of unique patients seen this week, % with any future booking in the diary."])
    out.append(["  Utilization % = (attended + DNA appointment hours, excl. cancelled & classes) / weekly capacity."])

    # All compute succeeded — now touch the sheet atomically.
    sh = open_spreadsheet()
    try:
        ws = sh.worksheet("Weekly Snapshot")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Weekly Snapshot", rows=200, cols=12)

    ws.update(values=out, range_name="A1", value_input_option="RAW")
    return ws


def write_weekly_team_stats_tab(weeks_back=4):
    """Build the 'Weekly Team Stats' tab: per-physio Utilisation % and Clinic
    Rebook % for each completed week, stacked most-recent-first.

    Complements the Weekly Snapshot (clinic-wide only) and the Performance
    Dashboard (per-physio but monthly): this gives Sinead the per-physio AND team
    utilisation + rebook for every week going forward. One block per week:

        Practitioner    | Utilisation % | Clinic Rebook %
        Clinic Average  |      …        |       …
        w/o M&J         |      …        |       …
        Marty / Julie / … (per physio)
    """
    import phase2 as p2
    import config
    now = datetime.now(LONDON)

    # Same completed-week derivation as write_weekly_snapshot_tab.
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_sunday = (today.weekday() + 1) % 7  # Sunday=0 in this scheme
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    periods = []
    monday = last_monday
    for _ in range(weeks_back):
        sunday = monday + timedelta(days=6)
        week_end_exclusive = sunday + timedelta(days=1)
        label = f"W/C {monday.strftime('%d %b %Y')}"
        periods.append((monday, week_end_exclusive, label))
        monday = monday - timedelta(days=7)

    headers = ["Practitioner", "Utilisation %", "Clinic Rebook %"]
    standard_vals = ["Gold Standard", "75–85%", "≥85%"]

    out = []
    out.append(["Weekly Team Stats — per-physio utilisation & rebook"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"])
    out.append([])

    format_cells = []  # (row_idx, col_idx, rgb)

    def pct(v):
        return f"{v:.1f}%" if v is not None else "—"

    def aggregate(displays, stats_by_display):
        used = avail = 0.0
        seen = future = 0
        for d in displays:
            s = stats_by_display.get(d)
            if not s:
                continue
            if s.get("available_hours"):
                used += s.get("used_hours", 0) or 0
                avail += s["available_hours"]
            seen += s.get("unique_patients_seen", 0)
            future += s.get("patients_with_future", 0)
        util = (used / avail * 100) if avail else None
        rebook = (future / seen * 100) if seen else None
        return util, rebook

    for week_start_local, week_end_local, label in periods:
        stats_by_display = p2.weekly_stats_per_physio(
            week_start_local.astimezone(timezone.utc),
            week_end_local.astimezone(timezone.utc),
        )

        out.append([label])
        out.append(headers)
        out.append(standard_vals)

        def stat_row(row_label, util, rebook):
            row_idx = len(out) + 1  # 1-indexed sheet row
            out.append([row_label, pct(util), pct(rebook)])
            for metric, col, v in (("utilization_pct", 2, util),
                                   ("clinic_rebook_pct", 3, rebook)):
                rgb = _colour_for(metric, v)
                if rgb is not None:
                    format_cells.append((row_idx, col, rgb))

        clinic_util, clinic_rebook = aggregate(config.PRACTITIONER_DISPLAY_ORDER, stats_by_display)
        main_util, main_rebook = aggregate(
            [d for d in config.PRACTITIONER_DISPLAY_ORDER
             if d not in config.EXCLUDE_FROM_MAIN_TEAM], stats_by_display)
        stat_row("Clinic Average", clinic_util, clinic_rebook)
        stat_row("w/o M&J", main_util, main_rebook)

        for display_name in config.PRACTITIONER_DISPLAY_ORDER:
            s = stats_by_display.get(display_name) or {}
            stat_row(display_name, s.get("util_pct"), s.get("clinic_rebook_pct"))

        out.append([])  # spacer between weeks

    out.append([])
    out.append(["Definitions:"])
    out.append(["  Utilisation % = non-cancelled appointment hours (incl. group sessions) ÷ that physio's available WEEKLY hours."])
    out.append(["  Weekly hours are derived from monthly contracted hours (÷ 4.345). Override config.PHYSIO_WEEKLY_HOURS for exact figures."])
    out.append(["  Clinic Rebook % = of the unique patients a physio ATTENDED that week, the share with any future booking in the diary."])
    out.append(["  Clinic Average / w/o M&J = team roll-up of the per-physio figures (w/o M&J excludes Marty + Julie)."])
    out.append(["Colour key: green = meets standard, yellow = within 10%, red = below."])

    sh = open_spreadsheet()
    try:
        ws = sh.worksheet("Weekly Team Stats")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Weekly Team Stats", rows=400, cols=4)

    ws.update(values=out, range_name="A1", value_input_option="RAW")

    if format_cells:
        def col_letter(idx):
            return chr(ord("A") + idx - 1)
        batch = [{"range": f"{col_letter(c)}{r}", "format": {"backgroundColor": rgb}}
                 for r, c, rgb in format_cells]
        ws.batch_format(batch)

    return ws


# ===========================================================================
# Monthly funnel — lead → IA → completed appointment counts for the
# top section of the Monthly Summary tab.  Each metric pulled live from
# Cliniko; unbooked leads pulled from the Bookings Archive sheet.
# ===========================================================================

# All "Initial Assessment"-style appointment types (the broad new-patient set).
_FUNNEL_IA_IDS = set(config.NEW_PATIENT_TYPE_IDS) if hasattr(
    config, "NEW_PATIENT_TYPE_IDS") else set()

# Club appointment types: Club IA, Club Follow Up, Club Consultation, ACL
# (initial + profiling), Sports Massage (all variants), Injury Update Testing
# (30/60), Hip & Groin / Back & Hamstring Profiling, Lab 60 Screening, all
# Reset/Rebuild Programme Initial Testing sessions.
_FUNNEL_CLUB_IDS = {
    "392015278608749674",   # 3. Club Initial Assessment
    "382589431795684515",   # 4. Club Follow Up Appointment
    "1396206071189608060",  # Club Consultation
    "945551547020874765",   # 7. ACL Initial Assessment
    "1031259844406941435",  # 1. ACL Profiling
    "1882529999999735591",  # Sports Massage
    "752219543803270402",   # Sports Massage Offer (30 Mins)
    "1820239945827096402",  # Sports Massage Offer (60 mins)
    "765760828145145406",   # Injury Update Testing (ACL/Hamstring/Groin) 30
    "765761537334842944",   # Injury Update Testing 60
    "998334021563847947",   # 2. Back & Hamstring Profiling
    "998332399567770890",   # 3. Hip & Groin Profiling
    "1810765504990680283",  # 4. Lab 60 Screening
    "1796352479944775353",  # Complete Groin Rebuild Package & Initial Testing
    "1796356817727526589",  # Complete Hamstring Rebuild Package & Initial Testing
    "1796354967016052411",  # Groin Reset Programme Initial Testing
    "1796358853206480576",  # Hamstring Reset Programme Initial Testing
}

# General population: Initial Appointment, Review, PHI Initial + Review,
# Mummy MOT Initial + Review, Pelvic Health Assessment, Sports & MSK
# Clinical Consultation, Ultrasound Assessment, Injection Therapy,
# Shockwave Therapy. Includes Injection + Shockwave per Martin 2026-06-07.
_FUNNEL_GEN_IDS = {
    "382563815654429852",   # 1. Initial Appointment
    "382563815511823515",   # 2. Review Appointment
    "1558530673046721630",  # 5. PHI Initial Assessment
    "1558531409491006559",  # 6. PHI Review
    "1118674052857206233",  # Mummy MOT Initial Assessment
    "1118674366867969498",  # Mummy MOT Review
    "1194028405859816854",  # Pelvic Health Assessment
    "1521627460095973060",  # 2. Sports & MSK Clinical Consultation
    "1206575759565526893",  # 3. Ultrasound Assessment
    "1192928323588592985",  # 1. Injection Therapy
    "980228540505003527",   # 2. Shockwave Therapy
}


def _funnel_appt_type_id(a):
    """Pull the appointment_type id from a Cliniko individual_appointment dict."""
    self_url = ((a.get("appointment_type") or {}).get("links") or {}).get("self") or ""
    m = re.search(r"/appointment_types/(\d+)", self_url)
    return m.group(1) if m else None


def _date_to_ym(raw):
    """Robust date-string → 'YYYY-MM' converter. Handles UK dd/mm/yyyy
    (the format the Bookings Leads tab uses), ISO yyyy-mm-dd, and dd/mm/yy.
    Returns None if the string can't be parsed.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    # dd/mm/yyyy or dd/mm/yy
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}"
    # ISO yyyy-mm-dd
    m = re.match(r"^(\d{4})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _unbooked_leads_by_month():
    """Read unbooked leads from the Bookings spreadsheet → dict 'YYYY-MM' → int.

    A lead counts as unbooked if its Status is anything except 'booked'
    (pending / declined / lost / etc.). Reads BOTH the live "Leads" tab
    (current pending leads, dated by the lead's intake Date) AND the
    "Leads Archive" tab if it exists (historical leads, dated by Week
    Archived).
    """
    out = {}
    try:
        sh = open_leads_spreadsheet()
    except Exception as e:  # noqa: BLE001
        print(f"  WARN funnel: couldn't open bookings spreadsheet ({e})")
        return out

    # Live Leads tab (pending leads not yet archived). Key by intake Date.
    try:
        leads_ws = sh.worksheet("Leads")
        for r in leads_ws.get_all_records():
            status = str(r.get("Status") or "pending").strip().lower()
            if status == "booked":
                continue
            ym = _date_to_ym(r.get("Date"))
            if not ym:
                continue
            out[ym] = out.get(ym, 0) + 1
    except Exception as e:  # noqa: BLE001
        print(f"  WARN funnel: couldn't read Leads tab ({e})")

    # Leads Archive (historical) — may not exist yet, that's OK
    try:
        archive_ws = sh.worksheet("Leads Archive")
        for r in archive_ws.get_all_records():
            status = str(r.get("Status") or "pending").strip().lower()
            if status == "booked":
                continue
            ym = _date_to_ym(r.get("Week Archived") or r.get("Date"))
            if not ym:
                continue
            out[ym] = out.get(ym, 0) + 1
    except Exception:  # noqa: BLE001
        pass  # archive doesn't exist yet — non-fatal

    return out


def _funnel_for_month(year, month, unbooked_by_month):
    """Compute the funnel metrics for a single calendar month.

    Returns dict with: total_leads, ias_booked, unbooked_leads, ias_completed,
    show_up_pct, club_completed, gen_completed, total_completed.
    """
    import phase2 as p2
    start = datetime(year, month, 1, tzinfo=LONDON)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=LONDON)
    else:
        end = datetime(year, month + 1, 1, tzinfo=LONDON)
    iso_start = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso_end = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Active (non-cancelled) individual appointments in the month
    try:
        active = list(p2.fetch_all("/individual_appointments", [
            ("q[]", f"starts_at:>={iso_start}"),
            ("q[]", f"starts_at:<{iso_end}"),
        ]))
    except Exception as e:  # noqa: BLE001
        print(f"  WARN funnel {year}-{month}: active fetch failed ({e})")
        active = []
    # Cancelled (Ransack ?` operator = is-present)
    try:
        cancelled = list(p2.fetch_all("/individual_appointments", [
            ("q[]", f"starts_at:>={iso_start}"),
            ("q[]", f"starts_at:<{iso_end}"),
            ("q[]", "cancelled_at:?"),
        ]))
    except Exception as e:  # noqa: BLE001
        print(f"  WARN funnel {year}-{month}: cancelled fetch failed ({e})")
        cancelled = []

    completed = [a for a in active if not a.get("did_not_arrive")]

    def in_bucket(apps, ids):
        return sum(1 for a in apps if _funnel_appt_type_id(a) in ids)

    ia_booked = in_bucket(active, _FUNNEL_IA_IDS) + in_bucket(cancelled, _FUNNEL_IA_IDS)
    ia_completed = in_bucket(completed, _FUNNEL_IA_IDS)
    club_completed = in_bucket(completed, _FUNNEL_CLUB_IDS)
    gen_completed = in_bucket(completed, _FUNNEL_GEN_IDS)
    unbooked = unbooked_by_month.get(f"{year:04d}-{month:02d}", 0)
    return {
        "total_leads":    ia_booked + unbooked,
        "ias_booked":     ia_booked,
        "unbooked_leads": unbooked,
        "ias_completed":  ia_completed,
        "show_up_pct":    (ia_completed / ia_booked) if ia_booked else 0.0,
        "club_completed": club_completed,
        "gen_completed":  gen_completed,
        "total_completed": club_completed + gen_completed,
    }


def write_monthly_summary_tab():
    """Refresh Monthly Summary tab — multi-month stacked, current month first,
    aggregated from all W/C tabs in the sheet."""
    now = datetime.now(LONDON)
    sh = open_spreadsheet()
    types = ("iacna", "iadna", "iadnr", "cancelled", "did_not_attend")

    # Collect rows grouped by YYYY-MM
    rows_by_month = {}
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            records = ws.get_all_records()
        except Exception as e:
            print(f"  WARN couldn't read {ws.title}: {e}")
            continue
        for row in records:
            appt_date = str(row.get("Appointment Date") or row.get("appointment_date") or "")
            canc_date = str(row.get("Cancellation Date") or row.get("cancellation_date") or "")
            # Event date: cancellation_date if set (cancellations), else appointment_date.
            # Matches the W/C tab grouping convention.
            event_date = canc_date if canc_date else appt_date
            if len(event_date) < 7:
                continue
            month_key = event_date[:7]
            rows_by_month.setdefault(month_key, []).append(row)

    try:
        ws = sh.worksheet("Monthly Summary")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Monthly Summary", rows=400, cols=8)

    out = []
    out.append(["Monthly Summary"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"])
    out.append([])

    # === MONTHLY FUNNEL — leads / IAs / completed appointments by month ===
    # Sits at the very top of the tab so it's the first thing Martin sees
    # heading into the monthly team meeting.
    print("  Computing Monthly Funnel (12 months)…", flush=True)
    try:
        unbooked_by_month = _unbooked_leads_by_month()
        out.append(["=== MONTHLY FUNNEL (last 12 months) ==="])
        out.append(["Month", "Total Leads", "IAs Booked", "Unbooked Leads",
                    "IAs Completed", "IA Show-up %",
                    "Club Completed", "General Pop Completed",
                    "Total Completed (Club + Gen)"])
        # Walk back 12 months from current
        y, m = now.year, now.month
        funnel_months = []
        for _ in range(12):
            funnel_months.append((y, m))
            m -= 1
            if m < 1:
                m = 12
                y -= 1
        for fy, fm in funnel_months:
            label = datetime(fy, fm, 1).strftime("%B %Y")
            try:
                f = _funnel_for_month(fy, fm, unbooked_by_month)
            except Exception as e:  # noqa: BLE001
                print(f"  WARN funnel skipped {fy}-{fm:02d}: {e}")
                out.append([label, "—", "—", "—", "—", "—", "—", "—", "—"])
                continue
            out.append([label, f["total_leads"], f["ias_booked"],
                        f["unbooked_leads"], f["ias_completed"],
                        f"{f['show_up_pct']*100:.0f}%" if f["ias_booked"] else "—",
                        f["club_completed"], f["gen_completed"],
                        f["total_completed"]])
        out.append([])
        out.append(["Definitions:"])
        out.append(["  Total Leads = IAs Booked + Unbooked Leads (booked-and-unbooked-on-the-Bookings-sheet)"])
        out.append(["  IAs Booked = ALL new-patient appointments scheduled in the month (any of 13 IA types, includes cancellations)"])
        out.append(["  IAs Completed = attended — excludes cancellations and did-not-arrives"])
        out.append(["  Club Completed = Club IA, Club Follow Up, Club Consultation, ACL Initial + Profiling, Sports Massage, Injury Update Testing, Profiling, Lab 60, Programme Initial Testing — completed only"])
        out.append(["  General Pop Completed = Initial Appointment, Review, PHI Initial/Review, Mummy MOT Initial/Review, Pelvic Health, Sports & MSK, Ultrasound, Injection, Shockwave — completed only"])
        out.append(["  Pilates classes excluded by design."])
        out.append([])
    except Exception as e:  # noqa: BLE001 (don't let funnel errors break the rest)
        print(f"  WARN funnel section failed: {e}")
        out.append(["=== MONTHLY FUNNEL — failed to compute, see logs ==="])
        out.append([])

    # === Existing per-physio drop-off sections continue below ===
    current_month_key = now.strftime("%Y-%m")
    for month_key in sorted(rows_by_month.keys(), reverse=True):
        month_label = datetime.strptime(month_key + "-01", "%Y-%m-%d").strftime("%B %Y")
        suffix = " (month-to-date)" if month_key == current_month_key else " (settled)"
        out.append([f"=== {month_label}{suffix} ==="])
        out.append(["Physio", "IACNA", "IADNA", "IADNR", "Cancelled", "DNA", "Total"])

        counts = {}
        for row in rows_by_month[month_key]:
            physio = row.get("Physio") or row.get("physio") or "?"
            kind = row.get("Drop-off Type") or row.get("dropoff_type") or ""
            counts.setdefault(physio, {k: 0 for k in types})
            if kind in counts[physio]:
                counts[physio][kind] += 1

        grand = {k: 0 for k in types}
        for physio in sorted(counts.keys()):
            c = counts[physio]
            total = sum(c.values())
            out.append([physio, c["iacna"], c["iadna"], c["iadnr"], c["cancelled"],
                        c["did_not_attend"], total])
            for k in types:
                grand[k] += c[k]
        out.append(["CLINIC TOTAL", grand["iacna"], grand["iadna"], grand["iadnr"],
                    grand["cancelled"], grand["did_not_attend"], sum(grand.values())])
        out.append([])

    out.append(["Definitions:"])
    out.append(["  IACNA = IA cancelled before attending. Physio not responsible."])
    out.append(["  IADNA = IA did-not-attend. Physio not responsible."])
    out.append(["  IADNR = Attended IA but never returned (no-rebook + cancelled/DNA'd first follow-up)."])
    out.append(["  Cancelled / DNA = established patient drop-offs (≥2 attended sessions in current episode)."])
    ws.update(values=out, range_name="A1", value_input_option="RAW")
    return ws


def write_physio_trends_tab(months_back=12):
    """Rolling N-month physio trends with a per-physio drop-down and four
    line charts (Utilization %, Total Drop off %, IA Rebook %, PVA).

    Layout:
      A1: title
      A2: last updated stamp
      A4: "Select physio:"  B4: drop-down (data validation: physio names)
      B7:M7: month labels (oldest → newest)
      A8:A11: metric labels
      B8:M11: formulas pulling the selected physio's values via INDEX/MATCH
      Row 13–46: four line-chart overlays (one per metric)
      Row 50+: hidden raw data blocks (one per metric, one row per physio)

    Uses the dashboard's rules end-to-end (per-patient dedup, responsible-
    physio attribution, strict-4 IADNR, wider-8 IA for NPs). Heavy — pulls
    12 months of Cliniko data, so suitable for weekly or monthly refresh.
    """
    import phase2 as p2

    now = datetime.now(LONDON)
    physios = list(config.PRACTITIONER_DISPLAY_ORDER)

    # Build 12 COMPLETED months ending with last month, oldest first. The
    # current (in-progress) month is excluded — drop-off rates look
    # artificially low early in a month before review-appt cancellations
    # have had time to land (Martin 2026-06-01).
    months = []
    y, m = now.year, now.month - 1   # start from previous month
    if m < 1:
        m = 12
        y -= 1
    for _ in range(months_back):
        months.insert(0, (y, m))
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    # Force month labels to TEXT (leading apostrophe). Without this Sheets
    # parses "Jul-25" as the date "July 25 of current year" → the chart's
    # x-axis becomes a date axis ("Mar-1, May-1, Jul-1…") AND the numeric
    # date values get plotted as a second series (the spurious diagonal
    # line we hit on first build).
    month_labels = ["'" + datetime(y, m, 1).strftime("%b-%y") for y, m in months]

    print(f"  Pulling {months_back} months of stats for Physio Trends tab…", flush=True)
    data = {p: {"util": [None] * months_back, "dropoff": [None] * months_back,
                "rebook": [None] * months_back, "pva": [None] * months_back}
            for p in physios}
    for i, (y, m) in enumerate(months):
        s_dt = datetime(y, m, 1, tzinfo=LONDON)
        e_dt = (datetime(y, m + 1, 1, tzinfo=LONDON) if m < 12
                else datetime(y + 1, 1, 1, tzinfo=LONDON))
        stats = p2.monthly_stats_per_physio(s_dt.astimezone(timezone.utc),
                                            e_dt.astimezone(timezone.utc))
        for phys in physios:
            sd = stats.get(phys)
            if not sd:
                continue
            review = sd["total_apts"] - sd["nps"]
            total_drops = sd["cnas_review"] + sd["dnas_review"] + sd.get("iadnrs", 0)
            data[phys]["util"][i] = sd.get("util_pct")
            data[phys]["dropoff"][i] = (
                total_drops / (total_drops + review) * 100
                if (total_drops + review) else None
            )
            data[phys]["rebook"][i] = (
                (sd["nps"] - sd.get("iadnrs", 0)) / sd["nps"] * 100
                if sd["nps"] else None
            )
            data[phys]["pva"][i] = (sd["total_apts"] / sd["nps"]) if sd["nps"] else None

    METRIC_DEF = [
        ("Utilization %", "util", 1),
        ("Total Drop off %", "dropoff", 1),
        ("IA Rebook %", "rebook", 1),
        ("PVA", "pva", 2),
    ]

    sh = open_spreadsheet()
    try:
        ws = sh.worksheet("Physio Trends")
        sheet_id = ws.id
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Physio Trends", rows=200, cols=20)
        sheet_id = ws.id

    # Delete any existing charts on this tab (so re-runs don't duplicate them).
    try:
        meta = sh.fetch_sheet_metadata(params={
            "fields": "sheets(properties(sheetId),charts(chartId))"
        })
        delete_reqs = []
        for s_obj in meta.get("sheets", []):
            if s_obj.get("properties", {}).get("sheetId") == sheet_id:
                for c in s_obj.get("charts", []) or []:
                    delete_reqs.append({"deleteEmbeddedObject": {"objectId": c["chartId"]}})
        if delete_reqs:
            sh.batch_update({"requests": delete_reqs})
    except Exception as e:
        print(f"  WARN chart cleanup failed: {e}")

    # Reserve raw-data block rows up front so display-row formulas can point at them.
    RAW_START = 50
    raw_blocks = {}
    cur = RAW_START
    for label, key, _ in METRIC_DEF:
        # Block: header row (RAW: <label>), month-label row, then N physio rows, then blank
        raw_blocks[key] = (cur + 2, cur + 1 + len(physios))   # 1-indexed inclusive
        cur = raw_blocks[key][1] + 2

    out = []
    out.append(["Physio Trends — Rolling 12 Completed Months (excludes current month)"])
    out.append([f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"])
    out.append([])
    out.append(["Select physio:", physios[0]])
    out.append([])
    out.append([])
    out.append(["Month →"] + month_labels)

    for label, key, _ in METRIC_DEF:
        start, end = raw_blocks[key]
        formulas = [
            f'=IFERROR(INDEX($B${start}:$M${end}, MATCH($B$4, $A${start}:$A${end}, 0), {ci}), "")'
            for ci in range(1, len(month_labels) + 1)
        ]
        out.append([label] + formulas)

    while len(out) < RAW_START - 1:
        out.append([])

    for label, key, _ in METRIC_DEF:
        out.append([f"RAW · {label}  (auto-generated, do not edit)"])
        out.append(["Physio"] + month_labels)
        for phys in physios:
            row = [phys]
            for v in data[phys][key]:
                if v is None:
                    row.append("")
                elif key == "pva":
                    row.append(f"{v:.2f}")
                else:
                    row.append(f"{v:.1f}")
            out.append(row)
        out.append([])

    ws.update(values=out, range_name="A1", value_input_option="USER_ENTERED")

    # Drop-down + four chart overlays, in a single batch.
    requests = [{
        "setDataValidation": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": 3, "endRowIndex": 4,
                      "startColumnIndex": 1, "endColumnIndex": 2},
            "rule": {
                "condition": {"type": "ONE_OF_LIST",
                              "values": [{"userEnteredValue": p} for p in physios]},
                "showCustomUi": True,
                "strict": True,
            },
        }
    }]

    # 2x2 grid of charts under row 12. Anchor rows/cols are 0-indexed.
    chart_anchors = [(12, 0), (12, 7), (29, 0), (29, 7)]
    for idx, ((label, key, _), (a_row, a_col)) in enumerate(zip(METRIC_DEF, chart_anchors)):
        # Domain = month labels in row 7 (0-indexed row 6), B–M.
        domain_range = {
            "sheetId": sheet_id,
            "startRowIndex": 6, "endRowIndex": 7,
            "startColumnIndex": 1, "endColumnIndex": 1 + len(month_labels),
        }
        # Series = the metric's formula row (row 8/9/10/11 → 0-indexed 7/8/9/10).
        s_row_0 = 7 + idx
        series_range = {
            "sheetId": sheet_id,
            "startRowIndex": s_row_0, "endRowIndex": s_row_0 + 1,
            "startColumnIndex": 1, "endColumnIndex": 1 + len(month_labels),
        }
        requests.append({
            "addChart": {
                "chart": {
                    "spec": {
                        "title": label,
                        "basicChart": {
                            "chartType": "LINE",
                            "legendPosition": "NO_LEGEND",
                            "axis": [
                                {"position": "BOTTOM_AXIS", "title": "Month"},
                                {"position": "LEFT_AXIS", "title": label},
                            ],
                            "domains": [{"domain": {"sourceRange": {"sources": [domain_range]}}}],
                            "series": [{
                                "series": {"sourceRange": {"sources": [series_range]}},
                                "targetAxis": "LEFT_AXIS",
                            }],
                            "headerCount": 0,
                        }
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {"sheetId": sheet_id,
                                           "rowIndex": a_row, "columnIndex": a_col},
                            "widthPixels": 500,
                            "heightPixels": 320,
                        }
                    }
                }
            }
        })

    sh.batch_update({"requests": requests})
    return ws


def _iadnr_reactivation_lookup(sh, window_days=30):
    """Map {appointment_id: True/False} for every IADNR row across the W/C tabs.
    True = the patient rebooked a follow-up within `window_days` of the drop-off
    (reactivated). A reactivation NEVER un-does the drop-off — the row stays an
    IADNR — but a within-30-day rebooking means it's not a *net loss*, so the
    Weekly Drop-off Analysis can show net-loss vs reactivated separately.

    Resolves each patient by the ID embedded in their name-hyperlink, with a
    name-search fallback, so stale appointment IDs / duplicate patient records
    don't corrupt the result. (Martin 2026-07: 30-day line.)"""
    import phase2 as p2
    import re
    PID_RE = re.compile(r"/patients/(\d+)")

    def _pdt(s):
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except ValueError:
            return None

    def _sheet_dt(s):
        s = (s or "").strip()
        for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, f)
            except ValueError:
                continue
        return None

    hist_cache = {}
    def hist(pid):
        if pid not in hist_cache:
            try:
                hist_cache[pid] = p2.fetch_patient_full_history(pid)
            except Exception:
                hist_cache[pid] = []
        return hist_cache[pid]

    name_cache = {}
    def by_name(first, last):
        k = (first, last)
        if k not in name_cache:
            try:
                name_cache[k] = list(fetch_all(
                    "/patients", [("q[]", f"first_name:={first}"), ("q[]", f"last_name:={last}")]))
            except Exception:
                name_cache[k] = []
        return name_cache[k]

    def resolve(pid, disp, ev_date):
        h = hist(pid)
        if any((a.get("starts_at") or "")[:10] == ev_date for a in h):
            return h
        parts = disp.split()
        if len(parts) >= 2:
            for pt in by_name(parts[0], " ".join(parts[1:])):
                h2 = hist(pt["id"])
                if any((a.get("starts_at") or "")[:10] == ev_date for a in h2):
                    return h2
        return h

    lookup = {}
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            form = ws.get_values(value_render_option="FORMULA")
            recs = ws.get_all_records()
        except Exception:
            continue
        nci = form[0].index("Patient Name") if form and "Patient Name" in form[0] else None
        for k, rec in enumerate(recs):
            if str(rec.get("Drop-off Type") or "").strip().lower() != "iadnr":
                continue
            aid = str(rec.get("appointment_id") or "")
            if not aid:
                continue
            frow = form[k + 1] if (k + 1) < len(form) else []
            m = PID_RE.search(str(frow[nci]) if (nci is not None and nci < len(frow)) else "")
            ev = _sheet_dt(str(rec.get("Appointment Date") or ""))
            if not m or not ev:
                lookup[aid] = False
                continue
            ev_date = ev.strftime("%Y-%m-%d")
            h = resolve(m.group(1), str(rec.get("Patient Name") or ""), ev_date)
            evt = [a for a in h if (a.get("starts_at") or "")[:10] == ev_date]
            if not evt:
                lookup[aid] = False
                continue
            es = _pdt(evt[0].get("starts_at"))
            reactivated = False
            for a in h:
                s = _pdt(a.get("starts_at"))
                c = _pdt(a.get("created_at"))
                if not s or a.get("cancelled_at"):
                    continue
                if es and s > es and c and 0 <= (c - es).days <= window_days:
                    reactivated = True
                    break
            lookup[aid] = reactivated
    return lookup


def write_weekly_dropoff_analysis_tab():
    """Weekly Drop-off Analysis tab — per-practitioner tally + stage-of-rehab
    breakdown, one section per week, most recent at the top.

    Reproduces Martin's manual weekly analysis. The current week always sits at
    fixed rows (practitioner tally A5:E14, stage breakdown G5:H10) so any charts
    pointed there redraw themselves on every daily run.
    """
    import config
    import phase2 as p2
    now = datetime.now(LONDON)
    sh = open_spreadsheet()

    def _performed_counts(su, eu):
        """Attended IAs (broader 13 NP types) + review appts performed in the
        UTC window [su, eu). Matches the Weekly Snapshot definitions. One light
        Cliniko fetch (no per-patient history), so cheap to run per week."""
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
        appts = list(p2.fetch_all("/individual_appointments", [
            ("q[]", f"starts_at:>={iso(su)}"), ("q[]", f"starts_at:<{iso(eu)}")]))
        ias = reviews = 0
        for a in appts:
            if a.get("cancelled_at") or a.get("did_not_arrive"):
                continue  # attended only
            tid = p2.id_from_link(a.get("appointment_type"))
            if tid in config.EXCLUDED_FROM_TOTAL_APPTS:
                continue  # classes / group sessions
            if tid in config.NEW_PATIENT_TYPE_IDS:
                ias += 1
            else:
                reviews += 1
        return ias, reviews

    weeks = {}
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            weeks[ws.title] = ws.get_all_records()
        except Exception as e:
            print(f"  WARN couldn't read {ws.title}: {e}")

    def week_date(title):
        try:
            return datetime.strptime(title.replace("W/C ", ""), "%d %b %Y")
        except ValueError:
            return datetime.min
    ordered = sorted(weeks.keys(), key=week_date, reverse=True)

    # Which IADNRs rebooked within 30 days (reactivations, not net losses).
    try:
        react_lookup = _iadnr_reactivation_lookup(sh)
    except Exception as e:
        print(f"  WARN reactivation lookup failed, treating all as net loss: {e}")
        react_lookup = {}

    # Physio-responsible drop-off types (IACNA / IADNA are pre-IA — excluded
    # from the practitioner tally; the physio never saw the patient).
    PHYS_RESP = ("iadnr", "cancelled", "did_not_attend")
    # Stage breakdown EXCLUDES pre-IA drop-offs (IACNA / IADNA) — the patient
    # never attended, so there's no stage of rehab to place them in.
    STAGE_ROWS = ["IADNR", "Before Session 3",
                  "Before Session 6", "After Session 6"]
    BODY_MAX = 14   # fixed body-area block height so the pie-chart range is stable

    def analyse(rows, react_lookup):
        per = {}
        per_react = {}   # display -> IADNRs reactivated within 30d (not net losses)
        stage = {k: 0 for k in STAGE_ROWS}
        body = {}
        clinical = {"Clinical": 0, "Non-Clinical": 0}
        for r in rows:
            kind = str(r.get("Drop-off Type") or r.get("dropoff_type")
                       or "").strip().lower()
            physio_full = str(r.get("Physio") or r.get("physio") or "?").strip()
            display = config.PRACTITIONER_DISPLAY_NAME.get(physio_full, physio_full)
            # IADNRs where the patient rebooked within 30 days are reactivations,
            # not net losses: still logged as IADNR, but counted separately here
            # so the tally/stage/body reflect genuine losses only. (Martin 2026-07)
            if kind == "iadnr" and react_lookup.get(str(r.get("appointment_id") or ""), False):
                per_react[display] = per_react.get(display, 0) + 1
                continue
            # Pre-IA drop-offs (iacna/iadna) are excluded from the stage breakdown.
            if kind in PHYS_RESP:
                if kind == "iadnr":
                    stage["IADNR"] += 1
                sn_raw = str(r.get("Session #") or r.get("session_number") or "").strip()
                try:
                    sn = int(float(sn_raw))
                except ValueError:
                    sn = 1
                if sn <= 3:
                    stage["Before Session 3"] += 1
                elif sn <= 6:
                    stage["Before Session 6"] += 1
                else:
                    stage["After Session 6"] += 1
            if kind in PHYS_RESP:
                d = per.setdefault(display, {"iadnr": 0, "cancelled": 0,
                                             "did_not_attend": 0})
                d[kind] += 1
            # Body area — pre-IA drop-offs (IACNA/IADNA) excluded: the patient
            # never attended, so there is no assessed body area.
            if kind not in ("iacna", "iadna"):
                area = str(r.get("Body Area") or "").strip()
                if area:
                    body[area] = body.get(area, 0) + 1
            # Clinical vs non-clinical reason. Pre-IA drop-offs (IACNA/IADNA)
            # are always counted Non-Clinical — the patient never attended, so
            # there is no clinical reason behind them.
            if kind in ("iacna", "iadna"):
                clinical["Non-Clinical"] += 1
            else:
                cv = str(r.get("Clinical / Non-Clinical") or "").strip().lower()
                if cv.startswith("non"):
                    clinical["Non-Clinical"] += 1
                elif cv.startswith("clin"):
                    clinical["Clinical"] += 1
        return per, per_react, stage, body, clinical

    out = [["Weekly Drop-off Analysis"],
           [f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"],
           []]

    for idx, title in enumerate(ordered):
        per, per_react, stage, body_counts, clinical = analyse(weeks[title], react_lookup)
        # IAs + review appointments actually performed that week (from Cliniko),
        # so the IADNR / CNA / DNA counts can be read against the volume they
        # came from.
        wd = week_date(title)
        ias_perf = review_perf = None
        if wd != datetime.min:
            try:
                su = wd.replace(tzinfo=LONDON).astimezone(timezone.utc)
                eu = (wd.replace(tzinfo=LONDON) + timedelta(days=7)).astimezone(timezone.utc)
                ias_perf, review_perf = _performed_counts(su, eu)
            except Exception as e:
                print(f"  WARN performed-counts failed for {title}: {e}")
        out.append(["CURRENT WEEK — " + title if idx == 0 else title])
        out.append(["Practitioner", "IADNR", "CNA", "DNA", "Total", "",
                    "Stage Drop-off", "Count", "",
                    "Body Area", "Count", "",
                    "Clinical Split", "Count"])

        prac_rows, tot = [], {"iadnr": 0, "cancelled": 0, "did_not_attend": 0}
        for d in config.PRACTITIONER_DISPLAY_ORDER:
            c = per.get(d, {"iadnr": 0, "cancelled": 0, "did_not_attend": 0})
            for k in tot:
                tot[k] += c[k]
            prac_rows.append([d, c["iadnr"], c["cancelled"], c["did_not_attend"],
                              c["iadnr"] + c["cancelled"] + c["did_not_attend"]])
        prac_rows.append(["Total", tot["iadnr"], tot["cancelled"],
                          tot["did_not_attend"], sum(tot.values())])

        stage_rows = [[s, stage[s]] for s in STAGE_ROWS]
        body_rows = [[a, n] for a, n in
                     sorted(body_counts.items(), key=lambda kv: -kv[1])]
        clin_rows = [["Clinical", clinical["Clinical"]],
                     ["Non-Clinical", clinical["Non-Clinical"]]]

        height = max(len(prac_rows), len(stage_rows), BODY_MAX, len(clin_rows))
        for i in range(height):
            p = prac_rows[i] if i < len(prac_rows) else ["", "", "", "", ""]
            s = stage_rows[i] if i < len(stage_rows) else ["", ""]
            b = body_rows[i] if i < len(body_rows) else ["", ""]
            cl = clin_rows[i] if i < len(clin_rows) else ["", ""]
            out.append(p + [""] + s + [""] + b + [""] + cl)
        # Performed-volume context: pair IADNRs against IAs performed, and
        # CNAs+DNAs against review appointments completed, for the same week.
        ia_disp = ias_perf if ias_perf is not None else "?"
        rev_disp = review_perf if review_perf is not None else "?"
        react_tot = sum(per_react.values())
        out.append(["IAs performed this week:", ia_disp, "", "IADNRs (net loss):", tot["iadnr"]])
        out.append(["Review appts completed:", rev_disp, "",
                    "CNAs + DNAs:", tot["cancelled"] + tot["did_not_attend"]])
        out.append(["", "", "", "IADNRs reactivated ≤30d:", react_tot,
                    "", "(gross IADNRs:", tot["iadnr"] + react_tot, ")"])
        out.append([])

    out.append(["Definitions:"])
    out.append(["  Practitioner tally = physio-responsible drop-offs only: "
                "IADNR, CNA (established-patient cancellation), DNA."])
    out.append(["  IADNR (net loss) = attended IA then lost, NOT rebooked within 30 days. "
                "IADNRs reactivated ≤30d (patient rebooked a follow-up within 30 days) "
                "stay logged as IADNR but are shown separately, not counted as losses."])
    out.append(["  IACNA / IADNA (pre-IA drop-offs — patient never attended the "
                "assessment) are EXCLUDED from the Stage breakdown and the Body-Area "
                "chart; they are not attributed to a physio and have no rehab stage."])
    out.append(["  Before Session 3 / Before Session 6 = dropped off at session "
                "1-3 / 4-6 (attended a max of 2 / a max of 5). "
                "After Session 6 = session 7+."])
    out.append(["  IAs performed / Review appts completed = attended appointments "
                "that week (from Cliniko) — the denominators for the drop-offs: "
                "read IADNRs against IAs performed, and CNAs+DNAs against Review appts."])

    try:
        ws = sh.worksheet("Weekly Drop-off Analysis")
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Weekly Drop-off Analysis", rows=600, cols=26)
    ws.update(values=out, range_name="A1", value_input_option="RAW")
    return ws


def write_to_sheet(rows):
    if not rows:
        print("No rows to write.")
        return
    sh = open_spreadsheet()
    by_tab = {}
    for r in rows:
        # W/C tab keyed to the drop-off event date (cancellation date for cancellations,
        # appointment date for DNAs / IADNRs), so reception sees the cancellation in the
        # same week it actually occurred — not the week of the original appointment.
        event_dt = dropoff_event_dt(r)
        monday = event_dt - timedelta(days=event_dt.weekday())
        tab_name = f"W/C {monday.strftime('%d %b %Y')}"
        by_tab.setdefault(tab_name, []).append(r)

    appt_id_col_index = SHEET_COLUMNS.index("appointment_id") + 1  # 1-indexed for gspread
    for tab_name, tab_rows in by_tab.items():
        ws, created = get_or_create_tab(sh, tab_name)
        existing_ids = set(ws.col_values(appt_id_col_index)) if not created else set()
        new_rows = [r for r in tab_rows if r["appointment_id"] not in existing_ids]
        new_rows.sort(key=dropoff_event_dt)
        if new_rows:
            payload = [[cell_for(r, c) for c in SHEET_COLUMNS] for r in new_rows]
            ws.append_rows(payload, value_input_option="USER_ENTERED")
        flag = "(NEW TAB)" if created else "(existing)"
        skipped = len(tab_rows) - len(new_rows)
        print(f"  Tab '{tab_name}' {flag}: appended {len(new_rows)} of {len(tab_rows)} rows"
              + (f" ({skipped} skipped as duplicates)" if skipped else ""))


def existing_appointment_ids():
    """Set of all appointment_ids already written to any W/C tab.
    Lets the daily re-scan skip already-captured drop-offs cheaply."""
    sh = open_spreadsheet()
    appt_id_col = SHEET_COLUMNS.index("appointment_id") + 1
    ids = set()
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            ids.update(v for v in ws.col_values(appt_id_col) if v and v != "appointment_id")
        except Exception as e:
            print(f"  WARN couldn't read appointment_ids from {ws.title}: {e}")
    return ids


# How many days back the daily cron re-scans, to catch late-marked DNAs and
# late-logged cancellations that weren't visible when first processed.
DAILY_LOOKBACK_DAYS = 10

# How far back to re-check pending drop-offs for auto-detected rebookings.
REBOOKING_RECHECK_WEEKS = 8


def detect_rebookings():
    """Auto-promote drop-off rows to 'rebooked' once the patient is back in the diary.

    Scans W/C tabs from the last REBOOKING_RECHECK_WEEKS weeks. For each row still
    marked 'pending' or 'contact_attempted', checks Cliniko: does the patient now
    have a non-cancelled, non-DNA appointment AFTER the drop-off date? If so, the
    row's Reactivation Status flips to 'rebooked' — no manual sheet editing needed.
    """
    import phase2 as p2
    sh = open_spreadsheet()
    now = datetime.now(LONDON)
    cutoff_week = (now - timedelta(weeks=REBOOKING_RECHECK_WEEKS)).replace(tzinfo=None)
    status_col = SHEET_COLUMNS.index("reactivation_status") + 1  # 1-indexed

    checked = 0
    promoted = 0
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            week_start = datetime.strptime(ws.title.replace("W/C ", ""), "%d %b %Y")
        except ValueError:
            continue
        if week_start < cutoff_week:
            continue  # too old to keep re-checking

        try:
            records = ws.get_all_records()
        except Exception as e:
            print(f"  WARN couldn't read {ws.title} for rebooking check: {e}")
            continue

        for sheet_row, row in enumerate(records, start=2):  # row 1 = header
            status = str(row.get("Reactivation Status") or "").strip().lower()
            # Include BLANK status — newly-written IADNR rows start blank and
            # used to be skipped here, so reactivated patients with untouched
            # rows never got auto-flagged green. (Johnnie Wright / Peter
            # McCormack case, 2026-05-28.)
            if status not in ("", "pending", "contact_attempted"):
                continue
            appt_id = str(row.get("appointment_id") or "")
            appt_date = str(row.get("Appointment Date") or "")
            if not appt_id or not appt_date:
                continue
            checked += 1

            # appointment_id → patient_id
            try:
                r = p2.SESSION.get(f"{p2.BASE}/individual_appointments/{appt_id}", timeout=30)
                if r.status_code != 200:
                    continue
                patient_id = id_from_link(r.json().get("patient"))
            except Exception:
                continue
            if not patient_id:
                continue

            # Reference point = the drop-off appointment's start
            try:
                ref = datetime.strptime(appt_date, "%Y-%m-%d %H:%M").replace(tzinfo=LONDON)
            except ValueError:
                continue
            ref_iso = ref.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Any non-cancelled, non-DNA appointment after the drop-off = re-engaged
            rebooked = False
            try:
                for appt in p2.fetch_all("/individual_appointments", [
                    ("q[]", f"patient_id:={patient_id}"),
                    ("q[]", f"starts_at:>{ref_iso}"),
                ]):
                    if not appt.get("did_not_arrive"):
                        rebooked = True
                        break
            except Exception as e:
                print(f"  WARN rebooking check failed for appt {appt_id}: {e}")
                continue

            if rebooked:
                ws.update_cell(sheet_row, status_col, "reactivated")
                promoted += 1

    print(f"  Reactivation check: {checked} pending rows checked, "
          f"{promoted} auto-promoted to 'reactivated'")


def main():
    write_mode = "--write" in sys.argv
    skip_phase2 = "--no-phase2" in sys.argv
    date_override = None
    for i, a in enumerate(sys.argv):
        if a == "--date" and i + 1 < len(sys.argv):
            date_override = sys.argv[i + 1]
            break

    if date_override:
        rows, excluded = collect_dropoffs(date_override=date_override)
    else:
        # Daily cron: rolling re-scan, skipping anything already in the sheet
        already = existing_appointment_ids()
        rows, excluded = collect_dropoffs(lookback_days=DAILY_LOOKBACK_DAYS,
                                          skip_appointment_ids=already)

    if rows and not skip_phase2:
        print()
        print(f"Enriching {len(rows)} NEW row(s) with Phase 2 (session # + body area)…")
        enrich_phase2(rows)
    print()
    print_preview(rows, excluded)
    print()
    if write_mode:
        # NOTE: the Sunday Leads-tab wipe is DISABLED. The bookings system
        # (bookings_fetch.py) now owns the Leads tab — reception manages a live,
        # rolling lead list there and the bookings Dashboard reads it by date.
        # Wiping it weekly would erase that list, so the drop-off system no
        # longer touches the Leads tab. (weekly_leads_wipe() is kept defined for
        # reference but is intentionally not called.)

        print("Writing drop-off rows to Google Sheet…")
        write_to_sheet(rows)
        print("Checking for auto-detected rebookings…")
        try:
            detect_rebookings()
        except Exception as e:
            print(f"  WARN rebooking detection failed: {e}")
        # Each refresh wrapped independently — a network blip on one tab must not
        # abort the rest of the run (or the Slack notifications at the end).
        # NOTE: "Lead Conversion (Dashboard)" write is DISABLED — it wrote to a
        # tab named "Dashboard" in the leads/bookings sheet, which collided with
        # the bookings system's own Dashboard. The bookings system now owns that
        # sheet's Leads tab + Dashboard. (write_dashboard_lead_conversion() is
        # kept defined for reference but is intentionally not called.)
        refreshes = [
            ("IA Rebook Rate", write_ia_rebook_rate_tab),
            ("Bookings Funnel", lambda: __import__("funnel").write_funnel_tab()),
            ("Monthly Summary", write_monthly_summary_tab),
            ("Weekly Snapshot", lambda: write_weekly_snapshot_tab(weeks_back=4)),
            ("Weekly Team Stats", lambda: write_weekly_team_stats_tab(weeks_back=4)),
            ("Performance Dashboard", write_performance_dashboard_tab),
            ("Weekly Drop-off Analysis", write_weekly_dropoff_analysis_tab),
        ]
        # Physio Trends rebuilds 12 months of stats — heavy (~5–8 min). Only
        # refresh on Mondays + on the 1st of each month so the data stays
        # current without slowing every daily cron.
        now_local = datetime.now(LONDON)
        if now_local.weekday() == 0 or now_local.day == 1:
            refreshes.append(("Physio Trends (rolling 12 months)", write_physio_trends_tab))
        for label, fn in refreshes:
            print(f"Refreshing {label} tab…")
            try:
                fn()
            except Exception as e:
                print(f"  WARN {label} refresh failed: {e}")
        print("Sending Slack notifications…")
        try:
            import slack_notifier
            mtd_pct = read_mtd_rebook_pct_from_sheet()
            leads = None
            try:
                leads = leads_pipeline_summary()
            except Exception as e:
                print(f"  WARN leads pipeline summary failed: {e}")
            # The Ops Manager summary reports YESTERDAY's actual drop-offs, not
            # just the rows this run added. The daily run's `rows` only contains
            # drop-offs newly written this run (the rolling re-scan skips ones
            # already in the sheet), so on a day where yesterday's drop-offs were
            # already captured it would otherwise report Total: 0. Re-collect
            # yesterday specifically (full set, no skip) for an accurate count.
            summary_rows = None
            try:
                yday = (datetime.now(LONDON) - timedelta(days=1)).strftime("%Y-%m-%d")
                summary_rows, _ = collect_dropoffs(date_override=yday)
            except Exception as e:
                print(f"  WARN yesterday summary recount failed, "
                      f"falling back to new rows: {e}")
            slack_notifier.send_all(rows, ia_rebook_mtd_pct=mtd_pct,
                                    leads_summary=leads, summary_rows=summary_rows)
        except Exception as e:
            print(f"  WARN Slack notifications failed: {e}")
            # Don't let Slack failure abort the daily run — the sheet is already updated.
        print("Done.")
    else:
        print("(Preview only. Re-run with --write to append to the Sheet.)")


def read_mtd_rebook_pct_from_sheet():
    """Read the current-month clinic IA Rebook % from the freshly-refreshed
    IA Rebook Rate tab. Returns None if not parseable."""
    sh = open_spreadsheet()
    try:
        ws = sh.worksheet("IA Rebook Rate")
    except gspread.exceptions.WorksheetNotFound:
        return None
    in_mtd_section = False
    for row in ws.get_all_values():
        head = row[0] if row else ""
        if "month-to-date" in head:
            in_mtd_section = True
            continue
        if head.startswith("===") and "settled" in head:
            in_mtd_section = False
        if in_mtd_section and row and row[0] == "CLINIC TOTAL" and len(row) >= 4:
            rate = (row[3] or "").rstrip("%").strip()
            if rate and rate != "—":
                try:
                    return float(rate)
                except ValueError:
                    pass
            return None
    return None


if __name__ == "__main__":
    main()
