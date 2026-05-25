"""SMS sending via Twilio.

Sender is the alphanumeric ID "ElitePhysio" (config.SMS_SENDER_ID) — this is
ONE-WAY: patients cannot reply. SMS copy must route to a phone number or link,
never "reply" (see patient_communication_system.md).

Honours config.MARKETING_SAFE_MODE. The caller checks config.MARKETING_LIVE.
"""

import os

import requests

import config

_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def normalise_uk_phone(phone):
    """Best-effort convert a UK number to E.164 (+44…). Returns input if unknown."""
    p = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
    if not p:
        return ""
    if p.startswith("+"):
        return p
    if p.startswith("0044"):
        return "+" + p[2:]
    if p.startswith("44"):
        return "+" + p
    if p.startswith("0"):
        return "+44" + p[1:]
    return p


def send_sms(*, to, body):
    """Send one SMS. Returns (ok: bool, info: str)."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        return False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set"

    dest = normalise_uk_phone(to)
    if not dest:
        return False, "no destination phone number"

    if config.MARKETING_SAFE_MODE:
        body = f"[TEST → {dest}] {body}"
        dest = config.MARKETING_TEST_PHONE

    try:
        r = requests.post(
            _API.format(sid=sid),
            data={"From": config.SMS_SENDER_ID, "To": dest, "Body": body},
            auth=(sid, token), timeout=20)
    except requests.RequestException as e:
        return False, f"request failed: {e}"

    if r.status_code in (200, 201):
        try:
            return True, r.json().get("sid", "sent")
        except ValueError:
            return True, "sent"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"
