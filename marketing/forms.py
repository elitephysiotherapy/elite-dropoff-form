"""Cliniko pre-assessment forms — which bookings still have one to complete.

Cliniko creates one Pre-Assessment Form per booking when the confirmation email
goes out. Each has its own patient-facing link (…/f/?token) and EXPIRES 7 days
after it is created, so a visit booked more than a week ahead has an expired form
before the patient arrives. The welcome email and the form-chase texts use this
module to (a) link straight to the patient's own form and (b) stay silent once
it is done, expired or missing.

Fails SAFE: if Cliniko can't be read, `by_appointment()` returns None and every
caller sends nothing — we never chase on a guess.
"""

from datetime import datetime, timedelta, timezone

import config
import phase2

_UTC = timezone.utc
LOOKBACK_DAYS = 10   # forms live 7 days; a little extra for clock/clinic slack


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def by_appointment(lookback_days=LOOKBACK_DAYS):
    """{appointment_id: form} for recent Pre-Assessment Forms, or None if the
    lookup failed. If a booking somehow has several, the completed one wins."""
    since = datetime.now(_UTC) - timedelta(days=lookback_days)
    try:
        forms = list(phase2.fetch_all(
            "/patient_forms", [("q[]", f"created_at:>={_iso(since)}")]))
    except Exception as e:  # noqa: BLE001
        print(f"  WARN: pre-assessment form lookup failed: {e}")
        return None
    out = {}
    for fm in forms:
        if phase2.id_from_link(fm.get("patient_form_template")) != \
                config.PRE_ASSESSMENT_FORM_TEMPLATE_ID:
            continue
        if fm.get("archived_at"):
            continue
        aid = phase2.id_from_link(fm.get("booking"))
        if not aid:
            continue
        prev = out.get(str(aid))
        if prev is None or (fm.get("completed_at") and not prev.get("completed_at")):
            out[str(aid)] = fm
    return out


def pending_url(forms, appt_id, now=None):
    """The patient-facing link for this appointment's form if it still needs
    completing (not completed, not expired); otherwise None."""
    if not forms:
        return None
    fm = forms.get(str(appt_id))
    if not fm or fm.get("completed_at") or fm.get("expired"):
        return None
    exp = phase2.parse_iso(fm.get("expires_at"))
    if exp is not None and exp <= (now or datetime.now(_UTC)):
        return None
    return fm.get("url") or None
