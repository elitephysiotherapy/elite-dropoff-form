"""Builds the Tally NPS survey URL with hidden fields baked in.

The patient never sees or types these — they ride in the link as URL
parameters and come back in the Tally webhook, so every response is matched to
the right patient and clinic with zero manual entry.

Hidden fields (see tally_nps_form.md §2):
  patient_id, patient_name, patient_email, patient_phone, physio_name,
  clinic_name, appointment_date, trigger_type, google_review_url
"""

from urllib.parse import urlencode

import config
from marketing import url_shortener

VALID_TRIGGERS = {"ia", "discharge", "cna", "dna"}


def build_survey_url(*, patient_id, patient_name, patient_email, patient_phone,
                     physio_name, clinic_name, appointment_date, trigger_type):
    """Return a SHORT redirect URL pointing at the per-patient Tally survey.

    Builds the full Tally URL with all hidden-field params (as before), then
    routes it through marketing.url_shortener so the SMS body stays under one
    160-char segment. If shortening fails for any reason (Sheets outage etc.),
    falls back to the long Tally URL — SMS sending never breaks because of
    shortener infra.

    `trigger_type` ∈ VALID_TRIGGERS.
    """
    if not config.TALLY_FORM_ID:
        raise RuntimeError(
            "config.TALLY_FORM_ID is not set — build the Tally form (see "
            "tally_nps_form.md) and paste its form code into config.py")
    if trigger_type not in VALID_TRIGGERS:
        raise ValueError(f"trigger_type must be one of {VALID_TRIGGERS}, got {trigger_type!r}")

    clinic = config.CLINICS.get(clinic_name) or config.CLINICS.get(config.DEFAULT_CLINIC, {})
    params = {
        "patient_id": str(patient_id or ""),
        "patient_name": patient_name or "",
        "patient_email": patient_email or "",
        "patient_phone": patient_phone or "",
        "physio_name": physio_name or "",
        "clinic_name": clinic_name or "",
        "appointment_date": str(appointment_date or ""),
        "trigger_type": trigger_type,
        "google_review_url": clinic.get("google_review_url", ""),
    }
    long_url = f"https://tally.so/r/{config.TALLY_FORM_ID}?{urlencode(params)}"
    label = f"{trigger_type}_{patient_id or ''}_{appointment_date or ''}"
    return url_shortener.make_short_url(long_url, label=label)
