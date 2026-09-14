"""Send layer — the single gate every outbound message passes through.

  - MARKETING_LIVE off + SAFE_MODE off  -> shadow: nothing is sent
  - SAFE_MODE on                        -> sent, but rerouted to the test contacts
  - MARKETING_LIVE on                   -> sent to the real patient

SAFE_MODE rerouting itself lives in resend_client / twilio_client.

A real send failure DMs Martin on Slack. Until 2026-09-14 failures only reached
the Render log: the webhook server had no Twilio/Resend keys from the May cloud
migration, so every promoter's Google review ask failed silently for 3.5 months.
"""

import re
import time

import config
from marketing import resend_client, twilio_client

# Same failure reason re-alerts at most this often per process. The webhook
# server is long-lived; a poller cron run is a fresh process every 10 minutes.
_ALERT_EVERY_SECS = 3600
_last_alert = {}


def _enabled():
    """True if a real network send should happen at all."""
    return config.MARKETING_LIVE or config.MARKETING_SAFE_MODE


def _alert_failure(channel, info):
    """DM Martin that a patient message failed to send. Never raises — an
    alerting problem must not break the send path. Long digit runs are masked
    so a Twilio error can't put a patient's phone number into Slack."""
    reason = re.sub(r"\+?\d[\d ]{5,}\d", "#", str(info))[:300]
    now = time.time()
    if now - _last_alert.get((channel, reason), 0) < _ALERT_EVERY_SECS:
        return
    _last_alert[(channel, reason)] = now
    msg = (f":rotating_light: *Patient {channel} failed to send*\n"
           f"Reason: `{reason}`\n"
           f"Patients are not receiving this message. Identical failures are "
           f"muted for an hour; check the Render logs for the full picture.")
    try:
        import slack_notifier
        slack_notifier._send_dm(config.CEO_SLACK_EMAIL, msg,
                                target_label="send-failure alert")
    except Exception as e:
        print(f"  WARN send-failure Slack alert failed: {e}")


def _checked(channel, result):
    ok, info = result
    if not ok:
        _alert_failure(channel, info)
    return result


def send_email(*, to, subject, html=None, text=None,
               from_name=None, from_email=None, reply_to=None):
    if not _enabled():
        return False, "shadow (not sent)"
    if not to:
        return False, "no email address"
    return _checked("email", resend_client.send_email(
        to=to, subject=subject, html=html, text=text,
        from_name=from_name, from_email=from_email, reply_to=reply_to))


def send_sms(*, to, body):
    if not _enabled():
        return False, "shadow (not sent)"
    if not to:
        return False, "no phone number"
    return _checked("SMS", twilio_client.send_sms(to=to, body=body))
