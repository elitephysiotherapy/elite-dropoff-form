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

VALID_TRIGGERS = {"ia", "discharge", "cna", "dna"}


def build_survey_url(*, patient_id, patient_name, patient_email, patient_phone,
                     physio_name, clinic_name, appointment_date, trigger_type):
    """Return the per-patient Tally survey URL. `trigger_type` ∈ VALID_TRIGGERS."""
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
    return f"https://tally.so/r/{config.TALLY_FORM_ID}?{urlencode(params)}"
