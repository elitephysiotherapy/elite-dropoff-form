"""Webhook-side handler for Tally NPS survey responses.

server.py's /tally/webhook route parses the Tally payload into a normalised
dict and calls handle_response(). This module:
  - records the response in 'NPS - Raw Data'
  - routes by score: Promoter / Passive / Detractor
  - sends the branch follow-ups and the internal alert to Sinead
  - logs detractors into 'NPS - Detractor Tracker' for the callback workflow
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import config
from marketing import templates, send
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
    clinic = config.CLINICS.get(resp.get("clinic_name") or "", {})

    try:
        _write_raw_row(resp, cat)
    except Exception as e:
        print(f"  WARN: could not write NPS Raw Data row: {e}")

    if cat == "Detractor":
        try:
            _write_detractor_row(resp)
        except Exception as e:
            print(f"  WARN: could not write Detractor Tracker row: {e}")
        _alert("detractor_alert", resp)
        _followups_detractor(resp, clinic)
    elif cat == "Passive":
        _alert("passive_alert", resp)
        _followup_passive(resp)
    else:
        _followups_promoter(resp, clinic)

    return f"processed: {cat} ({score}/10)"


# ---------------- sheet writes ----------------

def _today():
    return datetime.now(LONDON).strftime("%Y-%m-%d")


def _write_raw_row(resp, cat):
    callback = "Yes" if resp.get("callback_wanted") else "No"
    tab(_RAW).append_row([
        _today(),                       # A Date Sent (response day — see build report)
        str(resp.get("patient_id", "")),
        resp.get("patient_name", ""),
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
    ], value_input_option="USER_ENTERED")


def _write_detractor_row(resp):
    tab(_DET).append_row([
        _today(),
        resp.get("patient_name", ""),
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


# ---------------- internal alert ----------------

def _alert(template_id, resp):
    ctx = {
        "patient_name": resp.get("patient_name", ""),
        "score": resp.get("nps_score", ""),
        "physio_name": resp.get("physio_name", ""),
        "clinic_name": resp.get("clinic_name", ""),
        "trigger_label": TRIGGER_LABEL.get(resp.get("trigger_type"),
                                           resp.get("trigger_type", "")),
        "appointment_date": resp.get("appointment_date", ""),
        "callback_requested": "Yes" if resp.get("callback_wanted") else "No",
        "callback_number": resp.get("callback_number", "") or "(not given)",
        "open_text": resp.get("open_text", "") or "(no comment left)",
        "patient_phone": resp.get("patient_phone", ""),
        "patient_email": resp.get("patient_email", ""),
    }
    r = templates.render_internal(template_id, ctx)
    ok, info = send.send_email(to=config.NPS_ALERT_EMAIL, subject=r["subject"],
                               html=r["html"], text=r["text"])
    print(f"  internal alert ({template_id}) -> {config.NPS_ALERT_EMAIL}: "
          f"{'OK' if ok else 'FAIL'} {info}")


# ---------------- patient follow-ups ----------------

def _ctx(resp, clinic):
    return {
        "first_name": resp.get("patient_name", "") or "there",
        "clinic_name": resp.get("clinic_name", ""),
        "clinic_phone": clinic.get("phone", ""),
        "google_review_url": clinic.get("google_review_url", ""),
    }


def _followups_detractor(resp, clinic):
    ctx = _ctx(resp, clinic)
    body = templates.render_sms("detractor_followup", ctx)
    ok, info = send.send_sms(to=resp.get("patient_phone"), body=body)
    print(f"  detractor SMS: {'OK' if ok else 'FAIL'} {info}")
    e = templates.render_email("detractor_followup", ctx)
    ok, info = send.send_email(to=resp.get("patient_email"), **_email_kwargs(e))
    print(f"  detractor email: {'OK' if ok else 'FAIL'} {info}")


def _followup_passive(resp):
    e = templates.render_email("passive_followup",
                               {"first_name": resp.get("patient_name", "") or "there",
                                "clinic_name": resp.get("clinic_name", "")})
    ok, info = send.send_email(to=resp.get("patient_email"), **_email_kwargs(e))
    print(f"  passive email: {'OK' if ok else 'FAIL'} {info}")


def _followups_promoter(resp, clinic):
    ctx = _ctx(resp, clinic)
    body = templates.render_sms("promoter_followup", ctx)
    ok, info = send.send_sms(to=resp.get("patient_phone"), body=body)
    print(f"  promoter SMS: {'OK' if ok else 'FAIL'} {info}")
    e = templates.render_email("promoter_followup", ctx)
    ok, info = send.send_email(to=resp.get("patient_email"), **_email_kwargs(e))
    print(f"  promoter email: {'OK' if ok else 'FAIL'} {info}")


def _email_kwargs(rendered):
    return {
        "subject": rendered["subject"],
        "html": rendered["html"],
        "text": rendered["text"],
        "from_name": rendered["from_name"],
        "from_email": rendered["from_email"],
        "reply_to": rendered["reply_to"],
    }
