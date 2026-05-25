"""Shared helpers for the marketing flow modules.

A `Touch` is one message, to one patient, on one channel. Flow modules collect
the touches that are *due now*; poller.py dedups, renders, sends and logs them.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config
import phase2
from marketing import cliniko

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


@dataclass
class Touch:
    flow_name: str            # unique per touch, e.g. "ia_survey_sms" — dedup key part
    patient_id: str
    channel: str              # "sms" | "email"
    template_id: str          # key into templates.SMS / templates.EMAIL
    anchor: str               # dedup anchor — appointment id, or synthetic
    appt_ctx: dict = field(default_factory=dict)   # appointment-derived template vars
    trigger_type: str = None  # ia | discharge | cna | dna — set for survey touches
    is_marketing: bool = False  # True => only send if patient consented to marketing


def now_utc():
    return datetime.now(UTC)


def _london(dt):
    return dt.astimezone(LONDON)


def fmt_date(starts_at):
    """ISO timestamp -> 'Mon 18 May' in clinic local time."""
    dt = phase2.parse_iso(starts_at)
    return _london(dt).strftime("%a %d %b") if dt else ""


def fmt_time(starts_at):
    """ISO timestamp -> '2:30pm' in clinic local time."""
    dt = phase2.parse_iso(starts_at)
    if not dt:
        return ""
    s = _london(dt).strftime("%I:%M%p").lower()
    return s[1:] if s.startswith("0") else s


def hours_since(starts_at):
    """Whole hours between an appointment's start and now (UTC). None if unparseable."""
    dt = phase2.parse_iso(starts_at)
    if not dt:
        return None
    return (now_utc() - dt).total_seconds() / 3600.0


def appt_ctx(appt):
    """Template variables derivable from a Cliniko appointment."""
    clinic_key = cliniko.clinic_for(appt)
    clinic = config.CLINICS.get(clinic_key, {})
    starts = appt.get("starts_at")
    return {
        "clinic_name": clinic_key,
        "clinic_phone": clinic.get("phone", ""),
        "clinic_address": clinic.get("address", ""),
        "practitioner_name": cliniko.practitioner_name(appt) or "your physiotherapist",
        "appointment_date": fmt_date(starts),
        "appointment_time": fmt_time(starts),
        "appointment_type": "",
        "booking_link": config.BOOKING_LINK,
        "exercise_library_link": config.EXERCISE_LIBRARY_LINK,
        "form_link": config.PRE_ASSESSMENT_FORM_LINK,
        "google_review_url": clinic.get("google_review_url", ""),
        # internal (underscored) — not used in templates, used by the poller
        "_clinic_key": clinic_key,
        "_starts_at": starts,
    }
