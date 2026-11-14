"""Rule tests for campaign.py (no Cliniko, Twilio, Sheets or Slack).

Run: ./venv/bin/python test_campaign.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign as c  # noqa: E402

failures = 0
D = date(2026, 11, 18)


def check(label, cond):
    global failures
    print(("PASS  " if cond else "FAIL  ") + label)
    failures += 0 if cond else 1


def row(**kw):
    r = {h: "" for h in c.HEADERS}
    r.update({"Group": "mot", "Phone": "07700900123", "Email": "a@b.c"})
    r.update(kw)
    return r


# --- texts (Black Friday schedule) ------------------------------------------
T = date(2026, 11, 17)
check("MOT text 1 due when consented", c.text1_due(row(**{"Text OK": "Yes"}), T))
check("MOT text 1 not before Tue 17 Nov", not c.text1_due(row(**{"Text OK": "Yes"}), date(2026, 11, 16)))
check("no text without consent", not c.text1_due(row(**{"Text OK": "No"}), T))
check("no text after 23 Dec", not c.text1_due(row(**{"Text OK": "Yes"}), date(2026, 12, 24)))
check("no text if already booked", not c.text1_due(row(**{"Text OK": "Yes", "Booked": "2026-11-17"}), T))
check("no second text 1", not c.text1_due(row(**{"Text OK": "Yes", "Text 1": "2026-11-23 10:00"}), T))
check("club NOT texted while only MOT is active", not c.text1_due(row(Group="club", **{"Text OK": "Yes"}), D))
check("MOT past patient not opted out ('Soft') gets text 1", c.text1_due(row(**{"Text OK": "Soft"}), T))
check("opted out never texted", not c.text1_due(row(**{"Text OK": "Opted out"}), T))
check("under 18 never texted", not c.text1_due(row(**{"Text OK": "Under 18"}), T))
check("club 'Soft' not texted (MOT only)", not c.text1_due(row(Group="club", **{"Text OK": "Soft"}), T))
check("cold leads never texted by campaign", not c.text1_due(row(Group="cold", **{"Text OK": "Yes"}), T))
t1 = {"Text OK": "Yes", "Text 1": "2026-11-24 09:30"}
check("text 2 not before Black Friday", not c.text2_due(row(**t1), date(2026, 11, 26)))
check("text 2 on Black Friday", c.text2_due(row(**t1), date(2026, 11, 27)))
check("text 2 not after Cyber Monday", not c.text2_due(row(**t1), date(2026, 12, 1)))
check("no text 2 if they replied", not c.text2_due(row(Replied="2026-11-24", **t1), date(2026, 11, 27)))
check("club gets no text 2", not c.text2_due(row(Group="club", **t1), date(2026, 11, 27)))
check("no December text (Text 3 off)", not c.text3_due(row(**t1), date(2026, 12, 8)))
e1 = {"Email OK": "Yes", "Email 1": "2026-11-19 08:30"}
check("email 2 on Cyber Monday only", c.email2_due(row(**e1), date(2026, 11, 30)) and not c.email2_due(row(**e1), date(2026, 11, 29)))
check("no email 2 once booked", not c.email2_due(row(Booked="2026-11-25", **e1), date(2026, 11, 30)))
check("no email 2 without email 1", not c.email2_due(row(**{"Email OK": "Yes"}), date(2026, 11, 30)))
check("Black Friday voucher if booked by 30 Nov", c.booked_by_deadline(row(Booked="2026-11-30")))
check("standard voucher if booked 1 Dec", not c.booked_by_deadline(row(Booked="2026-12-01")))
check("email 1 from Thu 19 Nov", c.email1_due(row(**{"Email OK": "Yes"}), date(2026, 11, 19)) and not c.email1_due(row(**{"Email OK": "Yes"}), date(2026, 11, 18)))
check("no email when 'No'", not c.email1_due(row(**{"Email OK": "No"}), date(2026, 11, 19)))
check("'Soft' email (not unsubscribed) gets email 1", c.email1_due(row(**{"Email OK": "Soft"}), date(2026, 11, 19)))
check("unsubscribed never emailed", not c.email1_due(row(**{"Email OK": "Opted out"}), date(2026, 11, 19)))
check("under 18 never emailed", not c.email1_due(row(**{"Email OK": "Under 18"}), date(2026, 11, 19)))
check("unsubscribe token is opaque and stable", c.unsub_token("mot:123") == c.unsub_token("mot:123") and "123" not in c.unsub_url("mot:123"))

# --- calls --------------------------------------------------------------------
check("non-consented MOT: call from day 1", c.call_due(row(**{"Text OK": "No"}), D))
check("consented MOT: no call while texts run", not c.call_due(row(**t1), D))
t2 = dict(t1, **{"Text 2": "2026-11-22 10:00"})  # call rule still keys off Text 2
check("consented MOT: call 3 days after text 2", c.call_due(row(**t2), date(2026, 11, 25)))
check("consented MOT: not 2 days after text 2", not c.call_due(row(**t2), date(2026, 11, 24)))
check("reply → call today", c.call_due(row(Replied="2026-11-17", **t1), D))
check("club: no call unless replied", not c.call_due(row(Group="club"), D))
check("club: call after reply", c.call_due(row(Group="club", Replied="2026-11-17"), D))
check("cold lead: call", c.call_due(row(Group="cold"), D))
check("booked → off the list", not c.call_due(row(Group="cold", Booked="2026-11-17"), D))
check("not interested → off the list", not c.call_due(row(Group="cold", Excluded="not interested"), D))
check("max attempts → off the list", not c.call_due(row(Group="cold", Attempts="3"), D))
check("call-back date respected", not c.call_due(row(Group="cold", **{"Call Due": "2026-11-20"}), D))

# --- buttons --------------------------------------------------------------------
x = c.apply_choice(row(Attempts="1"), "noanswer", "<@U1>", D)
check("no answer: attempts +1 and back in 2 days",
      x["Attempts"] == "2" and x["Call Due"] == (D + timedelta(days=2)).isoformat())
check("booked marks Booked", c.apply_choice(row(), "booked", "u", D)["Booked"].startswith("2026-11-18"))
check("not interested excludes", c.apply_choice(row(), "notinterested", "u", D)["Excluded"] == "not interested")

# --- priority + scorecard -------------------------------------------------------
rs = [row(Group="mot", Key="m"), row(Group="cold", Key="c"), row(Group="rebook", Key="r"),
      row(Group="mot", Key="mr", Replied="2026-11-17")]
check("replies first, then rebooks, cold, MOT",
      [r["Key"] for r in sorted(rs, key=c.call_priority)] == ["mr", "r", "c", "m"])
n = c.scorecard_numbers([row(Group="mot", Booked="x"), row(Group="mot"), row(Group="cold")])
check("scorecard counts", n["mot"]["listed"] == 2 and n["mot"]["booked"] == 1 and n["mot"]["est_value"] == 180)

# --- wording --------------------------------------------------------------------
ctx = dict(first="Aoife", physio="Ciara", link="https://elite-sms-shortener.onrender.com/r/HJvXlw")
for name, tmpl in (("MOT 1", c.SMS_MOT_1), ("MOT 2", c.SMS_MOT_2), ("MOT 3", c.SMS_MOT_3), ("Club", c.SMS_CLUB_1)):
    body = tmpl.format(**ctx)
    check(f"{name} text has STOP + link, {len(body)} chars", "STOP" in body and ctx["link"] in body)
e = c.EMAIL_MOT_BODY.format(first="A", link="L", unsub="https://x/u")
check("email has unsubscribe link, no STOP line", "https://x/u" in e and "STOP" not in e)
check("first name cleaned", c.first_name("  o'neill mary") == "O'neill")

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
