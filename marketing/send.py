"""Send layer — the single gate every outbound message passes through.

  - MARKETING_LIVE off + SAFE_MODE off  -> shadow: nothing is sent
  - SAFE_MODE on                        -> sent, but rerouted to the test contacts
  - MARKETING_LIVE on                   -> sent to the real patient

SAFE_MODE rerouting itself lives in resend_client / twilio_client.
"""

import config
from marketing import resend_client, twilio_client


def _enabled():
    """True if a real network send should happen at all."""
    return config.MARKETING_LIVE or config.MARKETING_SAFE_MODE


def send_email(*, to, subject, html=None, text=None,
               from_name=None, from_email=None, reply_to=None):
    if not _enabled():
        return False, "shadow (not sent)"
    if not to:
        return False, "no email address"
    return resend_client.send_email(
        to=to, subject=subject, html=html, text=text,
        from_name=from_name, from_email=from_email, reply_to=reply_to)


def send_sms(*, to, body):
    if not _enabled():
        return False, "shadow (not sent)"
    if not to:
        return False, "no phone number"
    return twilio_client.send_sms(to=to, body=body)
