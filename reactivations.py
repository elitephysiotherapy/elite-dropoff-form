"""Canonical reactivation engine — the single source of truth used by the weekly
DM, the EOD number, and the bookings sheet / funnel (Martin, 2026-06-30).

DEFINITION
==========
Drop-off: a patient becomes a drop-off when they CNA (cancel) or DNA (no-show)
any follow-up or IA, OR are IADNR (attend an IA and book no follow-up) — AND by
the END OF THAT DAY they have no future appointment in the diary. A same-day
rebook (or still holding another future appointment) means it's a reschedule, not
a drop — this applies to CNAs, DNAs and IADNRs alike.

Reactivation: a drop-off who then books, regaining a future appointment. Counted
ONCE, in the week the rebooking was created.
  • Resets only on attendance: drop→rebook→drop→rebook (no attend) = 1;
    drop→rebook→attend→drop→rebook = 2.
  • A rescheduled / therapist-changed appointment that was never cancelled is not
    a drop, so it is never a reactivation.
  • >60 days: if the first rebook is a NEW IA whose appointment date is more than
    60 days after the drop-off event, it is a NEW BOOKING, not a reactivation.
    A follow-up after 60 days is still a reactivation.
  • Never double-counted: a ≤60-day return is a reactivation only (kept out of
    Total IAs); a >60-day new IA is a new booking only.

`reactivation_records(history, now)` returns one record per lapse that produced a
rebooking, each: {drop_date, rebook (appt), rebook_created, is_new_booking}.
is_new_booking=True means the >60-day-new-IA carve-out fired, so it counts as a
new booking and NOT as a reactivation.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

import phase2
import config

LONDON = ZoneInfo("Europe/London")
NEW_IA_AFTER_DAYS = 60


def _dt(iso):
    return phase2.parse_iso(iso) if iso else None


def _is_ia(appt):
    # "IA" = a real initial assessment (Initial Appointment / Club IA / PHI IA /
    # ACL IA), NOT consultations or scans — used for the IADNR drop trigger and
    # the >60-day-new-IA carve-out. (config.BOOKINGS_IA_TYPE_IDS is broader and
    # would wrongly treat a Club Consultation as an IA.)
    return phase2.id_from_link(appt.get("appointment_type")) in phase2.STRICT_IA_TYPE_IDS


def _end_of_day(dt):
    """End of the calendar day (Europe/London) containing dt."""
    loc = dt.astimezone(LONDON)
    return loc.replace(hour=23, minute=59, second=59, microsecond=0)


def _has_future_appt(appts, at_dt, exclude_id):
    """True if, as of at_dt, the patient holds a future appointment in the diary:
    booked on/before at_dt, starts after at_dt, not cancelled as of at_dt."""
    for a in appts:
        if str(a.get("id")) == exclude_id:
            continue
        st, cr = _dt(a.get("starts_at")), _dt(a.get("created_at"))
        if not st or not cr or cr > at_dt or st <= at_dt:
            continue
        canc = _dt(a.get("cancelled_at"))
        if canc and canc <= at_dt:
            continue
        return True
    return False


def _events(appts, now):
    """Chronological ('attend'|'drop', appt, time). Future pending appts are not
    events but still count as future appointments in the diary."""
    evs = []
    for a in appts:
        st = _dt(a.get("starts_at"))
        if not st:
            continue
        if a.get("cancelled_at"):
            evs.append(("drop", a, _dt(a.get("cancelled_at")) or st))
        elif a.get("did_not_arrive"):
            evs.append(("drop", a, st))
        elif st <= now:
            evs.append(("attend", a, st))
    evs.sort(key=lambda e: e[2])
    return evs


def reactivation_records(history, now):
    appts = list(history)
    lapses = []           # (drop_date, end_attendance_or_None)
    lapse_drop = None
    for kind, a, t in _events(appts, now):
        if kind == "attend":
            if lapse_drop is not None:
                lapses.append((lapse_drop, t))
                lapse_drop = None
            if _is_ia(a) and not _has_future_appt(appts, _end_of_day(t), str(a.get("id"))):
                lapse_drop = t                      # IADNR — opens a fresh lapse
        else:                                       # CNA / DNA
            if _has_future_appt(appts, _end_of_day(t), str(a.get("id"))):
                continue                            # reschedule / still in diary
            if lapse_drop is None:
                lapse_drop = t                      # new lapse (else: continue current)
    if lapse_drop is not None:
        lapses.append((lapse_drop, None))

    records = []
    for drop_date, _end in lapses:
        eod = _end_of_day(drop_date)
        rebooks = [a for a in appts if (_dt(a.get("created_at")) or now) > eod]
        if not rebooks:
            continue                                # dropped, never rebooked
        rebook = min(rebooks, key=lambda a: a.get("created_at") or "")
        st = _dt(rebook.get("starts_at"))
        over_60 = bool(st and (st - drop_date).days > NEW_IA_AFTER_DAYS)
        records.append({
            "drop_date": drop_date,
            "rebook": rebook,
            "rebook_created": rebook.get("created_at"),
            "is_new_booking": _is_ia(rebook) and over_60,
        })
    return records


def reactivations_in_window(history, start, end, now):
    """Records whose rebooking was created in [start, end) and that count as a
    reactivation (i.e. not reclassified as a >60-day new booking)."""
    s_iso = start.astimezone(LONDON).strftime("%Y-%m-%dT%H:%M:%S%z")
    out = []
    for r in reactivation_records(history, now):
        if r["is_new_booking"]:
            continue
        cr = _dt(r["rebook_created"])
        if cr and start <= cr < end:
            out.append(r)
    return out


def is_reactivation_ia(this_appt, history, now):
    """For the bookings sheet: True if this IA booking is a reactivation rebook
    (≤60-day return) — so it is kept OUT of Total IAs. A >60-day new IA returns
    False (it is a genuine new booking)."""
    tid = str(this_appt.get("id"))
    for r in reactivation_records(history, now):
        if str(r["rebook"].get("id")) == tid:
            return not r["is_new_booking"]
    return False
