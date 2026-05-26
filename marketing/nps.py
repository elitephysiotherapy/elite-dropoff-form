"""NPS survey flows — Initial Assessment and Discharge.

Each produces survey-invite touches that link the patient to the Tally form.
The branch follow-ups (promoter/passive/detractor) happen after the patient
responds and are handled webhook-side by detractor.py.

collect(appts, responder_ids) -> list[Touch]   (touches that are due now)
"""

from marketing import cliniko, common
from marketing.common import Touch

# Timing (hours after the appointment start).
IA_SMS_H = 0.25         # +15 min
IA_EMAIL_H = 2
IA_NURTURE_H = 24       # +1 day, only if no response yet
DISCHARGE_SMS_H = 24    # 1-day settle window before treating it as a discharge
DISCHARGE_EMAIL_H = 26
DISCHARGE_MAX_H = 120   # stop evaluating after 5 days

# A patient counts as genuinely discharged only after a real course of care.
DISCHARGE_MIN_ATTENDED = 3
# The "well done" congrats email only goes to patients who completed a fuller
# course of care THIS episode (Martin 2026: ≥6 attended in the current episode).
DISCHARGE_CONGRATS_MIN_ATTENDED = 6


def collect(appts, responder_ids):
    touches = []
    for appt in appts:
        if cliniko.is_class(appt):
            continue
        hrs = common.hours_since(appt.get("starts_at"))
        if hrs is None or hrs < 0:
            continue
        if not cliniko.is_attended(appt):
            continue
        if cliniko.is_initial_appointment(appt):
            _ia_touches(appt, hrs, responder_ids, touches)
        else:
            _discharge_touches(appt, hrs, touches)
    return touches


def _ia_touches(appt, hrs, responder_ids, out):
    aid = str(appt["id"])
    pid = cliniko.patient_id_of(appt)
    if not pid:
        return
    ctx = common.appt_ctx(appt)
    if hrs >= IA_SMS_H:
        out.append(Touch("ia_survey_sms", pid, "sms", "ia_survey", aid,
                          ctx, trigger_type="ia"))
    if hrs >= IA_EMAIL_H:
        out.append(Touch("ia_survey_email", pid, "email", "ia_survey", aid,
                          ctx, trigger_type="ia"))
    if hrs >= IA_NURTURE_H and str(pid) not in responder_ids:
        out.append(Touch("ia_survey_nurture", pid, "email", "ia_survey_nurture",
                          aid, ctx, trigger_type="ia"))


def _discharge_touches(appt, hrs, out):
    if hrs < DISCHARGE_SMS_H or hrs > DISCHARGE_MAX_H:
        return
    pid = cliniko.patient_id_of(appt)
    if not pid:
        return
    # A discharge is: course of care finished, nothing else booked.
    if cliniko.has_future_booking(pid):
        return
    if cliniko.attended_appt_count(pid) < DISCHARGE_MIN_ATTENDED:
        return
    aid = str(appt["id"])
    ctx = common.appt_ctx(appt)
    if hrs >= DISCHARGE_SMS_H:
        out.append(Touch("discharge_survey_sms", pid, "sms", "discharge_survey",
                          aid, ctx, trigger_type="discharge"))
    if hrs >= DISCHARGE_EMAIL_H:
        out.append(Touch("discharge_survey_email", pid, "email", "discharge_survey",
                          aid, ctx, trigger_type="discharge"))
        # "Well done" congrats email — only for a fuller course of care this episode.
        if cliniko.episode_attended_count(pid) >= DISCHARGE_CONGRATS_MIN_ATTENDED:
            out.append(Touch("discharge", pid, "email", "discharge_congrats",
                              aid, ctx, trigger_type="discharge"))
