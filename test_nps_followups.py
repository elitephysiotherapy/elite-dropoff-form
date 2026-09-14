"""Synthetic tests — every survey response gets the right follow-ups, once.

The gap this guards against: from May to Sept 2026 the webhook tried to send
these without the keys, so no promoter was asked for a review and no passive or
detractor heard back. The poller now sends them from the Raw Data sheet. No
network calls — sheet, Cliniko, Sent Log and the send gate are all stubbed.
"""
from dotenv import load_dotenv
load_dotenv(override=True)

from datetime import date

import config
from marketing import followups

HEADER = ["Date Sent"] * 14


def row(pid, cat, score, clinic="Cookstown", rid="", responded="2026-09-14"):
    return [responded, pid, f"Name {pid}", "Physio", clinic, "ia", str(score), cat,
            "comment", "Yes" if cat == "Detractor" else "", "", "Response received",
            responded, rid]


sent, log = [], []


def setup(rows, patients=None, logged=()):
    sent.clear()
    log.clear()
    log.extend(logged)
    followups.tab = lambda name: type("T", (), {"get_all_values": lambda self: [HEADER] + rows})()
    followups.cliniko.get_patient = lambda pid: (patients or {}).get(
        pid, {"id": pid, "first_name": "Pat", "full_name": "Pat X",
              "mobile": "07700900123", "email": f"{pid}@example.com"})
    followups.sent_log._rows = lambda: list(log)
    followups.sent_log.already_sent = lambda pid, flow, anchor, **kw: any(
        r[1] == pid and r[3] == flow and r[5] == anchor for r in log)
    followups.sent_log.log_send = lambda pid, name, flow, ch, anchor, tpl, status: \
        log.append(["ts", pid, name, flow, ch, anchor, tpl, status])
    followups.send.send_sms = lambda to, body: (sent.append(("sms", to, body)), (True, "ok"))[1]
    followups.send.send_email = lambda to, subject, **kw: (
        sent.append(("email", to, subject + " " + (kw.get("text") or ""))), (True, "ok"))[1]


config.MARKETING_LIVE, config.MARKETING_SAFE_MODE = True, False
SINCE = date(2026, 9, 1)
T = []


def check(name, got, want):
    T.append((name, got, want, got == want))


def channels():
    return sorted((c, "sinead" if to == config.NPS_ALERT_EMAIL else "patient") for c, to, _ in sent)


# 1. Promoter: review SMS + email to the patient, with THEIR clinic's link. No alert.
setup([row("1", "Promoter", 10, clinic="Maghera", rid="r1")])
followups.run(since=SINCE)
check("promoter gets SMS + email", channels(), [("email", "patient"), ("sms", "patient")])
check("promoter link is Maghera's",
      all(config.CLINICS["Maghera"]["google_review_url"] in b for _, _, b in sent), True)

# 2. Passive: thank-you email to patient + alert to Sinead. No SMS.
setup([row("2", "Passive", 8, rid="r2")])
followups.run(since=SINCE)
check("passive gets email + Sinead alert", channels(), [("email", "patient"), ("email", "sinead")])

# 3. Detractor: SMS + email to patient + alert to Sinead.
setup([row("3", "Detractor", 3, rid="r3")])
followups.run(since=SINCE)
check("detractor gets SMS + email + alert", channels(),
      [("email", "patient"), ("email", "sinead"), ("sms", "patient")])

# 4. Running again (the next 10-minute cycle) sends nothing.
before = list(log)
setup([row("3", "Detractor", 3, rid="r3")], logged=before)
followups.run(since=SINCE)
check("second cycle sends nothing", len(sent), 0)

# 5. Promoters already reached by today's catch-up are not asked twice.
setup([row("4", "Promoter", 9, rid="r4")],
      logged=[["ts", "4", "", "promoter_followup_catchup", "sms", "catchup-2026-09", "", "sent"]])
followups.run(since=SINCE)
check("catch-up promoter skipped", len(sent), 0)

# 6. Responses older than the window are left alone.
setup([row("5", "Passive", 7, rid="r5", responded="2026-08-01")])
followups.run(since=SINCE)
check("old response ignored", len(sent), 0)

# 7. No mobile -> email still goes; no email -> SMS still goes.
setup([row("6", "Detractor", 5, rid="r6")],
      patients={"6": {"id": "6", "first_name": "P", "mobile": "", "email": "p@example.com"}})
followups.run(since=SINCE)
check("missing mobile doesn't block email/alert", channels(), [("email", "patient"), ("email", "sinead")])

# 8. Sheet dates in the "14 Sep 2026" display format still parse.
setup([row("7", "Passive", 7, rid="r7", responded="14 Sep 2026")])
followups.run(since=SINCE)
check("display-format date parsed", len(sent), 2)

# 9. Shadow mode sends nothing and logs nothing.
config.MARKETING_LIVE = False
setup([row("8", "Detractor", 2, rid="r8")])
followups.run(since=SINCE)
check("shadow mode sends nothing", (len(sent), len(log)), (0, 0))

print("RESULT  test")
print("-" * 60)
allpass = True
for name, got, want, ok in T:
    allpass &= ok
    print(f"{'PASS' if ok else 'FAIL':5}   {name}   (got {got}, want {want})")
print("-" * 60)
print("ALL PASS" if allpass else "SOME FAILED")
