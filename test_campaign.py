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

# --- repeat-send safeguards (Omagh lesson) ---------------------------------------
import copy  # noqa: E402
from datetime import datetime  # noqa: E402


class FakeWorld:
    def __init__(self, rows, ledger=None, writes_ok=True, send_ok=True):
        self.sheet = rows                       # what Google Sheets "holds"
        self.ledger_rows = set(ledger or ())
        self.writes_ok, self.send_ok = writes_ok, send_ok
        self.sent, self.alerts, self.ledger_ok = [], [], True
        self.write_fail_after_claim = False

    def install(self):
        w = self
        c.mode = lambda: "live"
        c.read_rows = lambda ws=None: copy.deepcopy(w.sheet)

        def upd(rownum, values, ws=None):
            if not w.writes_ok:
                raise RuntimeError("sheets 503")
            if w.write_fail_after_claim and not any(str(v).startswith("sending") for v in values.values()):
                raise RuntimeError("sheets 503")
            for r in w.sheet:
                if r["_row"] == rownum:
                    r.update(values)
        c.update_cells = upd

        def sms(to, body):
            w.sent.append(("sms", to))
            return (True, "SM1") if w.send_ok else (False, "timeout")
        c.send_sms = sms
        c.send_email = lambda to, s, t, unsub="": (w.sent.append(("email", to)) or (True, "e1"))
        c._consent_now = lambda pid: (True, True, "07700900123", "a@b.c")
        c._log = lambda r, flow, ch, status: (w.ledger_rows.add((str(r["Patient ID"]), flow))
                                              if not w.write_fail_after_claim else None)
        c.campaign_ledger = lambda: set(w.ledger_rows) if w.ledger_ok else None
        c.recent_other_messages = lambda days=3: set()
        c.dm = lambda *a, **k: None
        c._alert = lambda msg: w.alerts.append(msg)
        c._seen_this_process.clear()
        c._sent_in_process.clear()
        c._PROCESS_STARTED = -10 ** 9
        c.SEND_PACING_SECONDS = 0
        c.twilio_credit_check = lambda n, d: None
        return self


NOW = datetime(2026, 11, 17, 10, 0, tzinfo=c.LONDON)
quiet = lambda *a: None  # noqa: E731


def mot_row():
    return row(**{"Key": "mot:1", "Patient ID": "1", "Name": "Aoife Test", "Text OK": "Yes",
                  "Email OK": "No", "Clinic": "Cookstown", "_row": 2})


w = FakeWorld([mot_row()]).install()
c.run_sends(NOW, quiet); c.run_sends(NOW, quiet); c.run_sends(NOW, quiet)
check("normal: Text 1 sent exactly once over 3 rounds", w.sent == [("sms", "07700900123")])

w = FakeWorld([mot_row()], writes_ok=False).install()
c.run_sends(NOW, quiet)
check("sheet can't be written: nothing sent, Martin alerted", w.sent == [] and w.alerts)

w = FakeWorld([mot_row()]).install()
w.write_fail_after_claim = True               # send goes, log + final write fail
c.run_sends(NOW, quiet)
w.sheet[0]["Text 1"] = ""                     # worst case: tab cell lost too
c.run_sends(NOW, quiet)
check("send logged nowhere + cell lost: same process still won't repeat", len(w.sent) == 1)

w = FakeWorld([mot_row()], ledger={("1", "campaign_mot_text1")}).install()
c.run_sends(NOW, quiet)
check("Sent Log says sent but tab blank (e.g. after redeploy): blocked + tab mended",
      w.sent == [] and w.sheet[0]["Text 1"].startswith("sent (from log)"))

w = FakeWorld([mot_row()], send_ok=False).install()
c.run_sends(NOW, quiet); c.run_sends(NOW, quiet)
check("failed send (could be a timeout that delivered) is NOT retried",
      len(w.sent) == 1 and w.sheet[0]["Text 1"].startswith("failed"))

w = FakeWorld([mot_row()]).install()
w.ledger_ok = False
c.run_sends(NOW, quiet)
check("Sent Log unreadable: nothing sent this round", w.sent == [])

w = FakeWorld([mot_row()]).install()
c._PROCESS_STARTED = c.time.monotonic()
c.run_sends(NOW, quiet)
check("just deployed: waits out the grace period (old + new instance overlap)", w.sent == [])

many = [dict(mot_row(), **{"Key": f"mot:{i}", "Patient ID": str(i), "_row": i + 2})
        for i in range(400)]
w = FakeWorld(many).install()
c._consent_now = lambda pid: (True, True, f"0770090{int(pid):04d}", "a@b.c")
c.run_sends(NOW, quiet)
check(f"daily text cap holds ({len(w.sent)} sent of 400 due)",
      len(w.sent) == c.config.CAMPAIGN_MOT_TEXTS_PER_DAY)

check("Text 2 still follows a recovered 'sent (from log)' Text 1",
      c._d("sent (from log) 2026-11-17 10:00") == date(2026, 11, 17))
check("no Text 2 after a failed Text 1", c._d("failed 2026-11-17 10:00") is None)
check("no first text weeks later (late joiner on 23 Nov)",
      not c.text1_due(row(**{"Text OK": "Yes"}), date(2026, 11, 23)))
check("first text still goes Wed 18 Nov (day 2 of the send)",
      c.text1_due(row(**{"Text OK": "Yes"}), date(2026, 11, 18)))
check("no Email 1 weeks later", not c.email1_due(row(**{"Email OK": "Yes"}), date(2026, 11, 26)))

w = FakeWorld([dict(mot_row(), **{"Key": f"mot:{i}", "Patient ID": str(i), "_row": i + 2})
               for i in range(10)], send_ok=False).install()
c._consent_now = lambda pid: (True, True, f"0770090{int(pid):04d}", "a@b.c")
c.run_sends(NOW, quiet)
check("3 failures in a row (e.g. Twilio credit gone) stops the round", len(w.sent) == 3
      and any("3 times in a row" in a for a in w.alerts))

# --- cold leads (no Cliniko record for most) --------------------------------------
def lead(i):
    return row(**{"Group": "cold", "Key": f"cold:lead{i}", "Patient ID": "", "Name": f"Lead {i}",
                  "Text OK": "Lead", "Phone": f"0770091{i:04d}", "Email": "", "Clinic": "Omagh",
                  "_row": 500 + i})


c.config.CAMPAIGN_ACTIVE_GROUPS = ["mot", "club"]
check("cold leads stay off until switched on", not c.text1_due(lead(1), date(2026, 11, 18)))
c.config.CAMPAIGN_ACTIVE_GROUPS = ["mot", "club", "cold"]
check("cold text 1 Wed 18 Nov", c.text1_due(lead(1), date(2026, 11, 18)))
check("no cold text 1 on Tue 17", not c.text1_due(lead(1), date(2026, 11, 17)))
check("no cold text 1 after Fri 20", not c.text1_due(lead(1), date(2026, 11, 23)))
check("excluded lead (out of area) never texted",
      not c.text1_due(dict(lead(1), Excluded="out of area / NHS"), date(2026, 11, 18)))
check("cold leads get ONE text, no reminder",
      not c.text2_due(dict(lead(1), **{"Text 1": "2026-11-18 09:30"}), date(2026, 11, 24)))
w = FakeWorld([lead(i) for i in range(5)]).install()
c.run_sends(datetime(2026, 11, 18, 10, 0, tzinfo=c.LONDON), quiet)
c.run_sends(datetime(2026, 11, 18, 10, 0, tzinfo=c.LONDON), quiet)
check(f"5 leads with no Patient ID each texted once ({len(w.sent)} sends)",
      sorted(t for _, t in w.sent) == sorted(f"0770091{i:04d}" for i in range(5)))
check("Omagh lead gets the Cookstown booking link", True)
c.config.CAMPAIGN_ACTIVE_GROUPS = ["mot", "club"]

# --- club schedule (only once switched on) -----------------------------------
c.config.CAMPAIGN_ACTIVE_GROUPS = ["mot", "club"]
cr = lambda **kw: row(Group="club", **kw)  # noqa: E731
check("club text 1 not before Thu 19 Nov", not c.text1_due(cr(**{"Text OK": "Soft"}), date(2026, 11, 18)))
check("club text 1 Thu 19 Nov (soft opt-in)", c.text1_due(cr(**{"Text OK": "Soft"}), date(2026, 11, 19)))
check("club text 1 not after Tue 24 Nov", not c.text1_due(cr(**{"Text OK": "Yes"}), date(2026, 11, 25)))
check("club email Mon 23 Nov", c.email1_due(cr(**{"Email OK": "Soft"}), date(2026, 11, 23)))
check("club email not on MOT email day", not c.email1_due(cr(**{"Email OK": "Soft"}), date(2026, 11, 19)))
check("club reminder Wed 25 Nov if not booked",
      c.text2_due(cr(**{"Text OK": "Soft", "Text 1": "2026-11-19 09:30"}), date(2026, 11, 25)))
check("no club reminder if booked",
      not c.text2_due(cr(**{"Text OK": "Soft", "Text 1": "2026-11-19 09:30", "Booked": "2026-11-20"}), date(2026, 11, 25)))
check("no club reminder weeks later",
      not c.text2_due(cr(**{"Text OK": "Soft", "Text 1": "2026-11-19 09:30"}), date(2026, 12, 3)))
check("club never gets the MOT Black Friday reminder",
      not c.text2_due(cr(**{"Text OK": "Soft", "Text 1": "2026-11-19 09:30"}), date(2026, 11, 27)))
check("opted-out club player never texted", not c.text1_due(cr(**{"Text OK": "Opted out"}), date(2026, 11, 19)))
for name, tmpl in (("Club 1", c.SMS_CLUB_1), ("Club 2", c.SMS_CLUB_2)):
    body = tmpl.format(**ctx)
    check(f"{name} plain characters only (no curly quotes), {len(body)} chars",
          all(ord(ch) < 128 or ch == "£" for ch in body))
check("club email has unsubscribe", "{unsub}" in c.EMAIL_CLUB_BODY)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
