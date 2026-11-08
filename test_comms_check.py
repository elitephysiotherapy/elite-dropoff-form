"""Tests for comms_check's rule engine (no Cliniko, Twilio, Sheets or Slack).

Pins Martin's 8 Nov 2026 decisions:
  - only three things are flagged: opt-out breaches, exact repeats, and
    Cliniko + bots both sending non-appointment messages within 48h,
  - message VOLUME is never flagged (SMS+email pairs, NPS + DNA rebook = normal),
  - appointment admin (confirmations, reminders, invoices, forms) never counts.

Run: ./venv/bin/python test_comms_check.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comms_check as cc  # noqa: E402

failures = 0
NOW = datetime(2026, 11, 10, 7, 15, tzinfo=timezone.utc)
PH = "7700900123"


def check(label, cond, detail=""):
    global failures
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"   {detail}"))
    failures += 0 if cond else 1


def ago(h):
    return NOW - timedelta(hours=h)


def cl(h, cat, typ="SMS", d="Sent", contact=PH, pid="111", ckey="k1"):
    return dict(t=ago(h), cat=cat, type=typ, dir=d, contact=contact, pid=pid, ckey=ckey)


def sms(h, bkey="b1", phone=PH, stl=False):
    return dict(t=ago(h), phone=phone, bkey=bkey, stl=stl)


def em(h, flow="dna_email", pid="111", anchor="A1"):
    return dict(t=ago(h), pid=pid, flow=flow, anchor=anchor, name="Test")


def rules(issues):
    return sorted(x["rule"] for x in issues)


# --- the agreed process is NOT flagged ----------------------------------------
normal_ia = [cl(100, "Appointment Confirmation", "Email"), cl(30, "Appointment Reminder", "Email"),
             cl(30, "Appointment Reminder", "SMS"), cl(29, "Invoice", "Email")]
check("IA week: confirmation+reminders+invoice + NPS sms+email -> nothing",
      cc.find_issues(normal_ia, [sms(5, "nps")], [em(5, "ia_survey_email")], [], NOW) == [])
check("DNA: review + rebook SMS + email -> nothing (volume never flagged)",
      cc.find_issues(normal_ia, [sms(20, "review"), sms(10, "dna")],
                     [em(20, "nps_followup_promoter"), em(10, "dna_email"), em(5, "dna_email_2")],
                     [], NOW) == [])

# --- R1 opt-out breach ----------------------------------------------------------
stop = [dict(t=ago(48), phone=PH, kind="stop")]
check("bot text after STOP -> R1", rules(cc.find_issues([], [sms(2)], [], stop, NOW)) == ["R1"])
check("Cliniko 'SMS Message' after STOP -> R1",
      rules(cc.find_issues([cl(2, "SMS Message")], [], [], stop, NOW)) == ["R1"])
check("appointment reminder after STOP -> not flagged",
      cc.find_issues([cl(2, "Appointment Reminder")], [], [], stop, NOW) == [])
check("text BEFORE the STOP -> not flagged",
      cc.find_issues([], [sms(50)], [], stop, NOW) == [])
check("STOP then START, then text -> not flagged",
      cc.find_issues([], [sms(2)], [], stop + [dict(t=ago(24), phone=PH, kind="start")], NOW) == [])
check("email after SMS STOP -> not flagged (STOP is SMS only)",
      cc.find_issues([], [], [em(2)], stop, NOW) == [])

# --- R2 exact repeats -------------------------------------------------------------
check("same bot text twice in 10 min -> R2 (the 9 Oct bug)",
      rules(cc.find_issues([], [sms(3.2), sms(3)], [], [], NOW)) == ["R2"])
check("same text 8 days apart -> not flagged", cc.find_issues([], [sms(200), sms(3)], [], [], NOW) == [])
check("two different bot texts -> not flagged",
      cc.find_issues([], [sms(3, "a"), sms(2, "b")], [], [], NOW) == [])
check("same email flow+visit twice -> R2",
      rules(cc.find_issues([], [], [em(5), em(4)], [], NOW)) == ["R2"])
check("same email flow, different visits -> not flagged",
      cc.find_issues([], [], [em(100, anchor="A1"), em(4, anchor="A2")], [], NOW) == [])
check("43 repeats = ONE issue, not 42",
      len(cc.find_issues([], [sms(3 + i / 6) for i in range(43)], [], [], NOW)) == 1)
check("old repeat (outside 72h) not re-reported",
      cc.find_issues([], [sms(120), sms(119)], [], [], NOW) == [])
check("Cliniko repeat of a reminder -> not flagged (appointment admin)",
      cc.find_issues([cl(5, "Appointment Reminder"), cl(4, "Appointment Reminder")], [], [], [], NOW) == [])

# --- R3 Cliniko + bots clash -------------------------------------------------------
check("Cliniko follow-up + bot text 20h apart -> R3",
      rules(cc.find_issues([cl(200, "Appointment Reminder"),            # links phone -> patient
                            cl(25, "Appointment Follow Up Message", "Email", contact="x@y.com")],
                           [sms(5)], [], [], NOW)) == ["R3"])
check("clash via Sent Log email (patient id match) -> R3",
      rules(cc.find_issues([cl(10, "Appointment Follow Up Message", "Email", contact="x@y.com")],
                           [], [em(5)], [], NOW)) == ["R3"])
check("manual reception text + bot message -> not flagged (Martin)",
      cc.find_issues([cl(10, "SMS Message")], [sms(5)], [em(4)], [], NOW) == [])
check("manual reception text sent twice -> not flagged",
      cc.find_issues([cl(10, "SMS Message"), cl(9, "SMS Message")], [], [], [], NOW) == [])
check("Cliniko + bot 3 days apart -> not flagged",
      cc.find_issues([cl(200, "Appointment Reminder"), cl(80, "Appointment Follow Up Message", "Email", contact="x@y.com")],
                     [sms(5)], [], [], NOW) == [])
FU = lambda h, k="k1": cl(h, "Appointment Follow Up Message", "Email", contact="x@y.com", ckey=k)
LINK = cl(200, "Appointment Reminder")    # ties the phone to the patient
check("bot text to a different patient -> not flagged",
      cc.find_issues([LINK, FU(10)], [sms(5, phone="7700900999")], [], [], NOW) == [])
check("speed-to-lead text never counts as a clash",
      cc.find_issues([LINK, FU(10)], [sms(5, stl=True)], [], [], NOW) == [])
check("several clashes for one patient -> one issue",
      len(cc.find_issues([LINK, FU(10), FU(9, "k2")], [sms(5, "a"), sms(4, "b")], [], [], NOW)) == 1)
check("staff notifications ignored",
      cc.find_issues([cl(10, "Practitioner Appointment Notification", "Email")], [sms(5)], [], [], NOW) == [])

# --- keys are stable so nothing is reported twice --------------------------------
a = cc.find_issues([], [sms(3.2), sms(3)], [], [], NOW)
b = cc.find_issues([], [sms(3.2), sms(3)], [], [], NOW + timedelta(hours=1))
check("same issue -> same key on the next run", [x["key"] for x in a] == [x["key"] for x in b])

# --- schedule --------------------------------------------------------------------
L = cc.LONDON
check("not due 07:14", not cc.due(datetime(2026, 11, 10, 7, 14, tzinfo=L), ""))
check("due 07:15", cc.due(datetime(2026, 11, 10, 7, 15, tzinfo=L), "2026-11-09"))
check("not twice a day", not cc.due(datetime(2026, 11, 10, 9, 0, tzinfo=L), "2026-11-10"))
check("catches up later if 07:15 missed", cc.due(datetime(2026, 11, 10, 13, 0, tzinfo=L), "2026-11-09"))

# --- helpers -----------------------------------------------------------------------
check("phone formats match", cc.norm_phone("+447700900123") == cc.norm_phone("07700 900123"))
check("links ignored in body match",
      cc.body_key("Hi A, book: https://x/r/1") == cc.body_key("Hi A, book: https://x/r/2"))

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
