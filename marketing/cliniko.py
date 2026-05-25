"""Cliniko data helpers for the marketing module.

Thin wrappers over phase2.py's Cliniko client. Flow modules use these and never
touch raw Cliniko JSON. Patient records are normalised into plain dicts.
"""

import time
from datetime import datetime, timedelta, timezone

import phase2
import config

# Appointment types that count as a first/initial appointment.
IA_TYPE_IDS = set(config.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS) \
    if hasattr(config, "PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS") else set()
if not IA_TYPE_IDS:
    IA_TYPE_IDS = set(phase2.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS)

_UTC = timezone.utc
_prac_cache = {}
_THROTTLE = [0.0]
_MIN_INTERVAL = 0.35   # ~3 req/s, well under Cliniko's 200/min


def _throttle():
    elapsed = time.time() - _THROTTLE[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _THROTTLE[0] = time.time()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- appointments ----------------

def _merge_live_cancelled(params):
    """Fetch individual appointments for `params`, merging live + cancelled."""
    live = list(phase2.fetch_all("/individual_appointments", params))
    cancelled = list(phase2.fetch_all(
        "/individual_appointments", params + [("q[]", "cancelled_at:?")]))
    by_id = {a["id"]: a for a in live}
    for a in cancelled:
        by_id[a["id"]] = a
    return list(by_id.values())


def recently_started_appointments(days_back=3):
    """Individual appointments whose start time is within the last `days_back`
    days (live + cancelled). Used for survey / discharge / no-show triggers."""
    end = datetime.now(_UTC)
    start = end - timedelta(days=days_back)
    return _merge_live_cancelled([
        ("q[]", f"starts_at:>={_iso(start)}"),
        ("q[]", f"starts_at:<{_iso(end)}"),
    ])


def recently_cancelled_appointments(days_back=3):
    """Appointments cancelled within the last `days_back` days — used for the
    cancellation rebooker (the trigger is the cancellation, not the slot time)."""
    cutoff = datetime.now(_UTC) - timedelta(days=days_back)
    return list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"cancelled_at:>={_iso(cutoff)}"),
    ]))


def appointments_started_between(start_dt_utc, end_dt_utc):
    """Individual appointments starting in [start, end) — used for lifecycle
    flows (e.g. appointments that happened ~30 days ago)."""
    return _merge_live_cancelled([
        ("q[]", f"starts_at:>={_iso(start_dt_utc)}"),
        ("q[]", f"starts_at:<{_iso(end_dt_utc)}"),
    ])


# ---------------- appointment attributes ----------------

def appt_type_id(appt):
    return phase2.id_from_link(appt.get("appointment_type"))


def is_initial_appointment(appt):
    return appt_type_id(appt) in IA_TYPE_IDS


def is_class(appt):
    return appt_type_id(appt) in config.EXCLUDED_FROM_TOTAL_APPTS


def is_attended(appt):
    return not appt.get("cancelled_at") and not appt.get("did_not_arrive")


def is_no_show(appt):
    return bool(appt.get("did_not_arrive")) and not appt.get("cancelled_at")


def is_cancelled(appt):
    return bool(appt.get("cancelled_at"))


def clinic_for(appt):
    """Clinic key (Cookstown / Maghera) for an appointment's location."""
    bid = phase2.id_from_link(appt.get("business"))
    return config.CLINIKO_BUSINESS_TO_CLINIC.get(str(bid), config.DEFAULT_CLINIC)


def practitioner_name(appt):
    """Physio display name (Marty / Daire / …) for the appointment."""
    global _prac_cache
    if not _prac_cache:
        for p in phase2.fetch_all("/practitioners"):
            _prac_cache[str(p["id"])] = p
    pid = phase2.id_from_link(appt.get("practitioner"))
    p = _prac_cache.get(str(pid)) if pid else None
    if not p:
        return ""
    full = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
    return config.PRACTITIONER_DISPLAY_NAME.get(full, p.get("first_name", ""))


def patient_id_of(appt):
    return phase2.id_from_link(appt.get("patient"))


# ---------------- patients ----------------

def get_patient(patient_id):
    """Fetch and normalise one patient. Returns a dict or None."""
    _throttle()
    try:
        r = phase2.SESSION.get(f"{phase2.BASE}/patients/{patient_id}", timeout=30)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return _normalise_patient(r.json())


def _normalise_patient(p):
    phones = p.get("patient_phone_numbers") or []
    mobile = ""
    for ph in phones:
        if (ph.get("phone_type") or "").lower() == "mobile":
            mobile = ph.get("number", "")
            break
    if not mobile and phones:
        mobile = phones[0].get("number", "")
    first = p.get("preferred_first_name") or p.get("first_name") or ""
    return {
        "id": str(p.get("id")),
        "first_name": first,
        "full_name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
        "email": p.get("email") or "",
        "mobile": mobile,
        "dob": p.get("date_of_birth") or "",
        "accepted_email_marketing": bool(p.get("accepted_email_marketing")),
        "accepted_sms_marketing": bool(p.get("accepted_sms_marketing")),
        "archived": bool(p.get("archived_at") or p.get("deleted_at")),
    }


# ---------------- history / future bookings ----------------

def has_future_booking(patient_id, after_iso=None):
    """True if the patient has a live (non-cancelled) appointment after `after_iso`
    (default: now)."""
    after = after_iso or _iso(datetime.now(_UTC))
    for a in phase2.fetch_all("/individual_appointments", [
        ("q[]", f"patient_id:={patient_id}"),
        ("q[]", f"starts_at:>{after}"),
    ]):
        if not a.get("cancelled_at") and not a.get("did_not_arrive"):
            return True
    return False


def attended_appt_count(patient_id, before_iso=None):
    """Count of the patient's attended appointments (optionally before a time)."""
    n = 0
    for a in phase2.fetch_patient_full_history(patient_id):
        if a.get("cancelled_at") or a.get("did_not_arrive"):
            continue
        if before_iso and (a.get("starts_at") or "") >= before_iso:
            continue
        n += 1
    return n
