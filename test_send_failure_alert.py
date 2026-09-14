"""Synthetic tests — a failed patient send must reach Slack, not just the log.

The gap this guards against: from May to Sept 2026 the webhook server had no
Twilio/Resend keys, so every promoter review ask failed and only the Render
log knew. No network calls here — both clients and Slack are stubbed.
"""
from dotenv import load_dotenv
load_dotenv(override=True)

import sys
import types

import config
from marketing import send

dms = []
sys.modules["slack_notifier"] = types.SimpleNamespace(
    _send_dm=lambda email, msg, target_label: dms.append((email, msg)))

config.MARKETING_LIVE, config.MARKETING_SAFE_MODE = True, False
T = []


def check(name, got, want):
    T.append((name, got, want, got == want))


def run(sms_result=None, email_result=None, to="+447700900123"):
    send._last_alert.clear()
    dms.clear()
    send.twilio_client.send_sms = lambda to, body: sms_result
    send.resend_client.send_email = lambda **kw: email_result
    if sms_result:
        send.send_sms(to=to, body="hi")
    if email_result:
        send.send_email(to="p@example.com", subject="s", text="t")
    return len(dms)


# 1. The actual May–Sept failure alerts.
check("missing Twilio keys alerts",
      run(sms_result=(False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")), 1)
check("missing Resend key alerts",
      run(email_result=(False, "RESEND_API_KEY not set")), 1)

# 2. Successful sends stay quiet.
check("successful SMS is silent", run(sms_result=(True, "SM123")), 0)

# 3. No contact details is routine, not a failure.
send._last_alert.clear(); dms.clear()
send.send_sms(to="", body="hi")
send.send_email(to="", subject="s", text="t")
check("no phone / no email is silent", len(dms), 0)

# 4. Shadow mode never alerts.
config.MARKETING_LIVE, config.MARKETING_SAFE_MODE = False, False
check("shadow mode is silent",
      run(sms_result=(False, "RESEND_API_KEY not set")), 0)
config.MARKETING_LIVE = True

# 5. A burst of identical failures is one DM, not one per patient.
send._last_alert.clear(); dms.clear()
send.twilio_client.send_sms = lambda to, body: (False, "HTTP 401: auth")
for _ in range(20):
    send.send_sms(to="+447700900123", body="hi")
check("20 identical failures -> 1 DM", len(dms), 1)

# 6. A patient's phone number never lands in Slack.
run(sms_result=(False, "HTTP 400: The 'To' number +447700900123 is not valid"))
check("phone number masked", "447700900123" in dms[0][1], False)

print("RESULT  test")
print("-" * 60)
allpass = True
for name, got, want, ok in T:
    allpass &= ok
    print(f"{'PASS' if ok else 'FAIL':5}   {name}   (got {got}, want {want})")
print("-" * 60)
print("ALL PASS" if allpass else "SOME FAILED")
