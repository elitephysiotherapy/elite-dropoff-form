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
  • Windows (Martin, 2026-07-20): ANY rebooking within 42 days is a reactivation.
    A FOLLOW-UP within 90 days is a reactivation. An IA type MORE than 42 days
    after the drop-off is a NEW EPISODE of care — the original drop-off holds and
    no reactivation is credited. Beyond those, neither.
  • Never double-counted: a ≤42-day return is a reactivation only (kept out of
    Total IAs); a >42-day new IA is a new booking only.

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
# Reactivation windows (Martin, 2026-07-20 — superseded the flat 60-day rule):
#   * ANY rebooking within 42 days of the drop-off  -> reactivation
#   * a FOLLOW-UP within 90 days                    -> reactivation
#   * an IA type MORE than 42 days after            -> NEW EPISODE of care: the
#     original drop-off holds and no reactivation is credited
NEW_IA_AFTER_DAYS = 42
FOLLOWUP_AFTER_DAYS = 90


def _dt(iso):
    return phase2.parse_iso(iso) if iso else None


def _is_ia(appt):
    # "IA" = a real initial assessment (Initial Appointment / Club IA / PHI IA /
    # ACL IA), NOT consultations or scans — used for the IADNR drop trigger and
    # the >60-day-new-IA carve-out. (config.BOOKINGS_IA_TYPE_IDS is broader and
    # would wrongly treat a Club Consultation as an IA.)
    return phase2.id_from_link(appt.get("appointment_type")) in phase2.STRICT_IA_TYPE_IDS


# Follow-up / continuation-of-care types. Only these count as a reactivation when
# a drop-off rebooks MORE than 60 days after the drop (Martin, 2026-07-01) — a new
# IA after 60d is a new booking, and a one-off (ultrasound, consultation, massage)
# after 60d is neither. Within 60 days, any rebooking counts regardless of type.
FOLLOWUP_TYPE_IDS = {
    "382589431795684515",   # 4. Club Follow Up Appointment
    "382563815511823515",   # 2. Review Appointment
}


def _is_followup(appt):
    return phase2.id_from_link(appt.get("appointment_type")) in FOLLOWUP_TYPE_IDS


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


def _drop_kind(appt):
    if appt is None:
        return "?"
    if appt.get("cancelled_at"):
        return "CNA"
    if appt.get("did_not_arrive"):
        return "DNA"
    return "IADNR"


def reactivation_records(history, now):
    appts = list(history)
    lapses = []           # (drop_date, drop_appt)
    lapse_drop = None
    lapse_drop_appt = None
    for kind, a, t in _events(appts, now):
        if kind == "attend":
            returning = lapse_drop is not None       # this attendance ends a lapse
            if returning:
                lapses.append((lapse_drop, lapse_drop_appt))
                lapse_drop = lapse_drop_appt = None
            # IADNR opens a lapse ONLY if the patient was already active — an IA
            # that was itself the patient's return (closed a lapse) plus a follow-up
            # booked shortly after is ONE comeback, not a new booking + a separate
            # reactivation (Martin, 2026-07-01 — the Shea Coney case).
            if (not returning and _is_ia(a)
                    and not _has_future_appt(appts, _end_of_day(t), str(a.get("id")))):
                lapse_drop, lapse_drop_appt = t, a
        else:                                        # CNA / DNA
            if _has_future_appt(appts, _end_of_day(t), str(a.get("id"))):
                continue                             # reschedule / still in diary
            if lapse_drop is None:
                lapse_drop, lapse_drop_appt = t, a   # new lapse (else: continue current)
    if lapse_drop is not None:
        lapses.append((lapse_drop, lapse_drop_appt))

    records = []
    for drop_date, drop_appt in lapses:
        eod = _end_of_day(drop_date)
        did = str(drop_appt.get("id")) if drop_appt else None
        # The rebooking is a genuine LATER appointment: booked after the drop day
        # and starting after the drop (excludes the drop appointment itself, e.g.
        # an IA entered the day after it happened — the Shea Coney bug).
        rebooks = [a for a in appts
                   if str(a.get("id")) != did
                   and (_dt(a.get("created_at")) or now) > eod
                   and (_dt(a.get("starts_at")) or now) > drop_date]
        if not rebooks:
            continue                                # dropped, never rebooked
        rebook = min(rebooks, key=lambda a: a.get("created_at") or "")
        st = _dt(rebook.get("starts_at"))
        gap = (st - drop_date).days if st else None
        if gap is not None and gap <= NEW_IA_AFTER_DAYS:
            is_react, is_new = True, False          # ≤42d: any rebooking counts
        elif _is_ia(rebook):
            # >42d new IA = a NEW EPISODE of care. The original drop-off holds
            # and no reactivation is credited for it.
            is_react, is_new = False, True
        elif _is_followup(rebook) and gap is not None and gap <= FOLLOWUP_AFTER_DAYS:
            is_react, is_new = True, False          # follow-up within 90d still counts
        else:
            # >90d follow-up, or a one-off (scan/consult/massage) beyond 42d:
            # neither a reactivation nor a new booking — the drop-off stands.
            is_react, is_new = False, False
        records.append({
            "drop_date": drop_date,
            "drop_appt": drop_appt,
            "drop_kind": _drop_kind(drop_appt),
            "rebook": rebook,
            "rebook_created": rebook.get("created_at"),
            "is_reactivation": is_react,
            "is_new_booking": is_new,
        })
    return records


def reactivations_in_window(history, start, end, now):
    """Records whose rebooking was created in [start, end) and that count as a
    reactivation (i.e. not reclassified as a >60-day new booking)."""
    out = []
    for r in reactivation_records(history, now):
        if not r["is_reactivation"]:
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
            return r["is_reactivation"]
    return False
