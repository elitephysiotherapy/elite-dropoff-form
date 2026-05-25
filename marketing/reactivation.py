"""Reactivation flows — cancellation rebooker, no-show rebooker, did-not-rebook.

All three lead with rebooking. A patient with a live future booking is never
nudged (the booking means they're already on track).

collect(started_appts, cancelled_appts) -> list[Touch]
"""

from marketing import cliniko, common
from marketing.common import Touch

# Cancellation rebooker — timing from cancelled_at.
CNA_SMS_H = 1
CNA_EMAIL_H = 24
CNA_MAX_H = 120
# No-show rebooker — timing from the missed appointment's start.
DNA_SMS_H = 0.25
DNA_EMAIL_H = 1
DNA_EMAIL2_H = 24
DNA_MAX_H = 120
# Did-not-rebook after an Initial Assessment — timing from the IA start.
IADNR_H = 72
IADNR_MAX_H = 168


def collect(started_appts, cancelled_appts):
    touches = []
    for appt in cancelled_appts:
        _cna_touches(appt, touches)
    for appt in started_appts:
        if cliniko.is_class(appt):
            continue
        if cliniko.is_no_show(appt):
            _dna_touches(appt, touches)
        elif cliniko.is_attended(appt) and cliniko.is_initial_appointment(appt):
            _iadnr_touches(appt, touches)
    return touches


def _cna_touches(appt, out):
    if cliniko.is_class(appt):
        return
    hrs = common.hours_since(appt.get("cancelled_at"))
    if hrs is None or hrs < CNA_SMS_H or hrs > CNA_MAX_H:
        return
    pid = cliniko.patient_id_of(appt)
    if not pid or cliniko.has_future_booking(pid):
        return   # rebooked / rescheduled — leave them alone
    aid = str(appt["id"])
    ctx = common.appt_ctx(appt)
    out.append(Touch("cna_rebook_sms", pid, "sms", "cancellation_rebook", aid, ctx))
    if hrs >= CNA_EMAIL_H:
        out.append(Touch("cna_rebook_email", pid, "email", "cancellation_rebook",
                          aid, ctx))


def _dna_touches(appt, out):
    hrs = common.hours_since(appt.get("starts_at"))
    if hrs is None or hrs < DNA_SMS_H or hrs > DNA_MAX_H:
        return
    pid = cliniko.patient_id_of(appt)
    if not pid or cliniko.has_future_booking(pid):
        return
    aid = str(appt["id"])
    ctx = common.appt_ctx(appt)
    out.append(Touch("dna_sms", pid, "sms", "no_show", aid, ctx))
    if hrs >= DNA_EMAIL_H:
        out.append(Touch("dna_email", pid, "email", "no_show", aid, ctx))
    if hrs >= DNA_EMAIL2_H:
        out.append(Touch("dna_email_2", pid, "email", "no_show", aid, ctx))


def _iadnr_touches(appt, out):
    hrs = common.hours_since(appt.get("starts_at"))
    if hrs is None or hrs < IADNR_H or hrs > IADNR_MAX_H:
        return
    pid = cliniko.patient_id_of(appt)
    if not pid or cliniko.has_future_booking(pid):
        return
    out.append(Touch("iadnr_nudge", pid, "email", "iadnr_nudge",
                      str(appt["id"]), common.appt_ctx(appt)))
