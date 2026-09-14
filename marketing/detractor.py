"""Webhook-side handler for Tally NPS survey responses.

server.py's /tally/webhook route parses the Tally payload into a normalised
dict and calls handle_response(). This module:
  - records the response in 'NPS - Raw Data'
  - routes by score: Promoter / Passive / Detractor
  - logs detractors into 'NPS - Detractor Tracker' for the callback workflow

Patient follow-ups and Sinead's alerts are NOT sent here — see
marketing/followups.py, run by the poller.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from marketing.sheets import tab

LONDON = ZoneInfo("Europe/London")
_RAW = "NPS - Raw Data"
_DET = "NPS - Detractor Tracker"

TRIGGER_LABEL = {
    "ia": "Initial Assessment", "discharge": "Discharge",
    "cna": "Cancellation", "dna": "No-show",
}


def category(score):
    if score >= 9:
        return "Promoter"
    if score >= 7:
        return "Passive"
    return "Detractor"


def _resolve_full_name(resp):
    """The survey link only carries the patient's FIRST name (used for friendly
    greetings like "Hi Debbie"). For the recorded NPS rows + the detractor
    callback workflow reception needs the FULL name, so look it up from Cliniko
    by patient_id. Best-effort: falls back to the link's name if the lookup is
    unavailable, so a Cliniko hiccup never blocks recording the response."""
    fallback = resp.get("patient_name", "") or ""
    pid = resp.get("patient_id")
    if not pid:
        return fallback
    try:
        from marketing import cliniko
        p = cliniko.get_patient(pid)
        if p and p.get("full_name"):
            return p["full_name"]
    except Exception as e:
        print(f"  WARN: full-name lookup failed for patient {pid}: {e}")
    return fallback


def handle_response(resp):
    """Process one survey response. `resp` keys:
      patient_id, patient_name, patient_email, patient_phone, physio_name,
      clinic_name, trigger_type, appointment_date, nps_score (int),
      open_text, callback_wanted (bool), callback_number.
    Returns a short status string.
    """
    score = resp.get("nps_score")
    if score is None:
        return "ignored: no score in payload"
    cat = category(int(score))

    # Idempotency guard: a Tally webhook retry or a patient double-submit must NOT
    # record the response twice (it would skew the per-physio NPS averages) nor
    # re-fire the follow-up SMS/email/alert. Bail out whole if already seen.
    if _already_recorded(resp):
        return f"duplicate: already recorded (response {resp.get('response_id') or '—'})"

    # Resolve the full name once (for sheet records + internal alert). Greetings
    # keep using resp["patient_name"] (first name) for a friendly tone.
    resp["patient_full_name"] = _resolve_full_name(resp)

    try:
        _write_raw_row(resp, cat)
    except Exception as e:
        print(f"  WARN: could not write NPS Raw Data row: {e}")

    if cat == "Detractor":
        try:
            _write_detractor_row(resp)
        except Exception as e:
            print(f"  WARN: could not write Detractor Tracker row: {e}")

    # Follow-ups and Sinead's alerts are sent by the marketing poller from the
    # Raw Data row (marketing/followups.py) within ~10 minutes. This service has
    # never held the Twilio/Resend keys, so sending here failed from May to Sept.
    return f"processed: {cat} ({score}/10) — follow-ups queued for the poller"


# ---------------- sheet writes ----------------

def _today():
    return datetime.now(LONDON).strftime("%Y-%m-%d")


# Column N (0-based index 13) on "NPS - Raw Data" holds the Tally response id.
_RESPONSE_ID_COL_IDX = 13


def _already_recorded(resp):
    """True if this NPS response is already in 'NPS - Raw Data' (dedup guard).

    Primary key = Tally response id (column N). For legacy rows written before
    column N existed (or a submit with no id) it falls back to a composite of
    patient_id + trigger_type + score + date, which catches a same-day retry
    without clashing across genuinely different surveys.
    Read failure → return False (never block recording a real response)."""
    rid = str(resp.get("response_id") or "").strip()
    pid = str(resp.get("patient_id") or "").strip()
    score = str(resp.get("nps_score", "")).strip()
    trig = str(resp.get("trigger_type") or "").strip()
    today = _today()
    try:
        rows = tab(_RAW).get_all_values()
    except Exception as e:
        print(f"  WARN: NPS dedup read failed, proceeding without dedup: {e}")
        return False
    for r in rows[1:]:                                       # skip header row
        existing_rid = r[_RESPONSE_ID_COL_IDX].strip() if len(r) > _RESPONSE_ID_COL_IDX else ""
        if rid and existing_rid and existing_rid == rid:
            return True
        if not rid and pid:                                 # composite fallback
            r_date = r[0].strip() if len(r) > 0 else ""
            r_pid = r[1].strip() if len(r) > 1 else ""
            r_trig = r[5].strip() if len(r) > 5 else ""
            r_score = r[6].strip() if len(r) > 6 else ""
            if r_pid == pid and r_trig == trig and r_score == score and r_date == today:
                return True
    return False


def _write_raw_row(resp, cat):
    callback = "Yes" if resp.get("callback_wanted") else "No"
    tab(_RAW).append_row([
        _today(),                       # A Date Sent (response day — see build report)
        str(resp.get("patient_id", "")),
        resp.get("patient_full_name") or resp.get("patient_name", ""),  # C full name
        resp.get("physio_name", ""),
        resp.get("clinic_name", ""),
        resp.get("trigger_type", ""),
        resp.get("nps_score", ""),
        cat,
        resp.get("open_text", ""),
        callback if cat == "Detractor" else "",
        resp.get("callback_number", "") if cat == "Detractor" else "",
        "Response received",
        _today(),                       # M Date Responded
        str(resp.get("response_id", "")),  # N Response ID (Tally idempotency key)
    ], value_input_option="USER_ENTERED")


def _write_detractor_row(resp):
    tab(_DET).append_row([
        _today(),
        resp.get("patient_full_name") or resp.get("patient_name", ""),  # B full name
        str(resp.get("patient_id", "")),
        resp.get("physio_name", ""),
        resp.get("clinic_name", ""),
        TRIGGER_LABEL.get(resp.get("trigger_type"), resp.get("trigger_type", "")),
        resp.get("nps_score", ""),
        resp.get("open_text", ""),
        "Yes" if resp.get("callback_wanted") else "No",
        resp.get("callback_number", ""),
        "Pending",                      # K Resolution Status
        "", "", "",                     # L-N filled in by Sinead
    ], value_input_option="USER_ENTERED")
