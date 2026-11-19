"""Pre-assessment form chase — two short texts, only while the form is undone.

Martin, 19 Nov 2026. Our own numbers (582 forms sent since 1 Sep): median
completion 4.3 h after sending, 73% of completers done within 24 h, almost none
after 3 days; only 32% completed overall. The form is sent at booking (Cliniko
confirmation email) and the welcome email (+1 h) links it again, so this is the
NUDGE, not the first ask:

  form_chase_1  ~5 h after booking (so within 5–31 h), only 9:00–20:00 local
  form_chase_2  the evening before the visit (17:00–20:00 local)

Both are first-consultation-only (config.FORM_CHASE_TYPE_IDS — not profiling, which has its own forms), SMS-only, once per appointment (sent_log dedup), and only if
the appointment's own form is still pending and unexpired. chase_2 is skipped
when the booking is under 29 h old so it can never stack on chase_1.
Service messages about the patient's own visit — not marketing, so no
marketing-consent gate (STOP handling is unchanged).
"""

from datetime import timedelta

import phase2
import config
from marketing import cliniko, common, forms
from marketing.common import Touch

CHASE1_MIN_AGE_H = 5       # booked at least this long ago…
CHASE1_MAX_AGE_H = 31      # …but not so long that we'd be chasing a backlog
CHASE2_MIN_BOOKING_AGE_H = 29
SEND_FROM_HOUR = 9         # local clock; the poller's own quiet hours are 21–08
SEND_UNTIL_HOUR = 20
CHASE2_FROM_HOUR = 17
MIN_HOURS_BEFORE_VISIT = 3  # never nudge in the last few hours before the visit


def _hours(a, b):
    return (a - b).total_seconds() / 3600.0


def chase_flow_for(appt, pending, now_utc):
    """Which chase (if any) is due for this appointment right now.
    Pure function of the appointment, form state and clock — the unit under test.
    Returns "form_chase_1", "form_chase_2" or None."""
    if not pending:
        return None
    if cliniko.is_cancelled(appt) or cliniko.appt_type_id(appt) not in config.FORM_CHASE_TYPE_IDS:
        return None
    starts = phase2.parse_iso(appt.get("starts_at"))
    created = phase2.parse_iso(appt.get("created_at"))
    if not starts or not created:
        return None
    if _hours(starts, now_utc) < MIN_HOURS_BEFORE_VISIT:
        return None
    local = now_utc.astimezone(common.LONDON)
    age = _hours(now_utc, created)

    # Evening before the visit.
    if (CHASE2_FROM_HOUR <= local.hour < SEND_UNTIL_HOUR
            and starts.astimezone(common.LONDON).date()
            == (local + timedelta(days=1)).date()
            and age >= CHASE2_MIN_BOOKING_AGE_H):
        return "form_chase_2"

    # ~5 h after booking, inside the 9–20 send window.
    if (SEND_FROM_HOUR <= local.hour < SEND_UNTIL_HOUR
            and CHASE1_MIN_AGE_H <= age <= CHASE1_MAX_AGE_H):
        return "form_chase_1"
    return None


def collect():
    """Touches due now. Reads Cliniko only; never writes."""
    now = common.now_utc()
    local = now.astimezone(common.LONDON)
    if not (SEND_FROM_HOUR <= local.hour < SEND_UNTIL_HOUR):
        return []
    pending_forms = forms.by_appointment()
    if pending_forms is None:      # couldn't verify the form state -> send nothing
        return []

    candidates = {}
    for appt in cliniko.appointments_created_between(
            now - timedelta(hours=CHASE1_MAX_AGE_H), now):
        candidates[str(appt["id"])] = appt
    for appt in cliniko.appointments_started_between(
            now, now + timedelta(hours=40)):
        candidates[str(appt["id"])] = appt

    out = []
    for aid, appt in candidates.items():
        if cliniko.is_class(appt):
            continue
        url = forms.pending_url(pending_forms, aid, now)
        flow = chase_flow_for(appt, bool(url), now)
        if not flow:
            continue
        pid = cliniko.patient_id_of(appt)
        if not pid:
            continue
        ctx = common.appt_ctx(appt)
        # The text is written in the physio's own voice ("it's Marty from…"), so
        # without a real practitioner name there is no sensible text — skip it.
        if ctx.get("practitioner_name") in (None, "", "your physiotherapist"):
            continue
        ctx["_form_url"] = url
        out.append(Touch(flow, pid, "sms", flow, aid, ctx))
    return out
