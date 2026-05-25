"""Email sending via Resend (https://resend.com).

Honours config.MARKETING_SAFE_MODE: when on, every email is rerouted to the
test address with the real recipient shown in the subject. The caller (poller)
is responsible for checking config.MARKETING_LIVE before sending.
"""

import os

import requests

import config

_RESEND_URL = "https://api.resend.com/emails"


def send_email(*, to, subject, html=None, text=None,
               from_name=None, from_email=None, reply_to=None):
    """Send one email. Returns (ok: bool, info: str).

    `from_name`/`from_email` default to the clinic sender; pass config.EMAIL_SINEAD
    or config.EMAIL_MARTIN (name, address) tuples for the personal templates.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return False, "RESEND_API_KEY not set"
    if not (html or text):
        return False, "no email body (need html or text)"

    from_name = from_name or config.EMAIL_FROM_NAME
    from_email = from_email or config.EMAIL_FROM_ADDRESS
    reply_to = reply_to or from_email

    if config.MARKETING_SAFE_MODE:
        subject = f"[TEST → {to}] {subject}"
        to = config.MARKETING_TEST_EMAIL

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to],
        "subject": subject,
        "reply_to": reply_to,
    }
    if html:
        payload["html"] = html
    if text:
        payload["text"] = text

    try:
        r = requests.post(
            _RESEND_URL, json=payload,
            headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    except requests.RequestException as e:
        return False, f"request failed: {e}"

    if r.status_code in (200, 201):
        try:
            return True, r.json().get("id", "sent")
        except ValueError:
            return True, "sent"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"
