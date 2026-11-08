"""Tests for speed_to_lead's pure rules (no Sheets, Slack or Twilio).

Pins what Martin agreed 2026-11-08:
  - body area from keywords only; no clear match = no "about your ..." part,
  - conditions are never echoed back in the text,
  - one fixed call line, booking link per clinic, GSM-safe (no curly quotes),
  - texts held 21:00–08:00,
  - the 15-min call clock starts at desk opening (Mon–Thu 08:30–21:00,
    Fri 08:30–16:00, weekend closed -> Monday 08:30).

Run: ./venv/bin/python test_speed_to_lead.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speed_to_lead as stl  # noqa: E402

L = stl.LONDON
failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}   {detail}")


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=L)


# --- body areas -------------------------------------------------------------
check("knee", stl.body_areas("Sore knee after football") == ["knee"])
check("ACL -> knee", stl.body_areas("acl reconstruction rehab") == ["knee"])
check("sciatica -> back", stl.body_areas("Sciatica down left leg") == ["back"])
check("lower back", stl.body_areas("lower back pain for months") == ["back"])
check("two areas, in written order",
      stl.body_areas("neck and lower back stiffness") == ["neck", "back"])
check("max two areas",
      len(stl.body_areas("back, neck, shoulder and knee")) == 2)
check("nothing clear -> []", stl.body_areas("everything hurts") == [])
check("condition not matched", stl.body_areas("peripheral neuropathy") == [])
check("'backing' not back", stl.body_areas("looking for backing") == [])
check("blank", stl.body_areas("") == [])

# --- first name -------------------------------------------------------------
check("plain", stl.first_name("Sarah Quinn") == "Sarah")
check("caps fixed", stl.first_name("SARAH QUINN") == "Sarah")
check("lower fixed", stl.first_name("mary-kate o'neill") == "Mary-Kate")
check("curly apostrophe kept GSM", "’" not in stl.first_name("D’Arcy Smith"))
check("emoji stripped", stl.first_name("Ciara 😀") == "Ciara")
check("junk -> blank", stl.first_name("x") == "")
check("empty -> blank", stl.first_name("") == "")

# --- text -------------------------------------------------------------------
LINK = "https://elite-sms-shortener.onrender.com/r/HJvXlw"
t = stl.compose_text(name="Sarah Quinn", clinic="Maghera",
                     issue="Sore knee", link=LINK)
check("agreed wording", t.startswith(
    "Hi Sarah, thanks for getting in touch with Elite Physio Maghera about your knee. "
    f"You can pick a time here: {LINK}. Or reply with a good time and we'll give you a call."),
    t)
check("STOP line", t.endswith("Reply STOP to opt out."))
t2 = stl.compose_text(name="Sarah", clinic="Omagh", issue="type two diabetic neuro",
                      link=LINK)
check("no match -> no 'about your'", "about your" not in t2, t2)
check("condition never echoed", "diabetic" not in t2 and "neuro" not in t2)
check("no name -> Hi there",
      stl.compose_text(name="", clinic="Omagh", issue="", link=LINK).startswith("Hi there,"))
check("GSM-7 only (no curly quotes/dashes)", all(ord(c) < 128 for c in t), t)
check("fits 2 SMS parts (<=306)", len(t) <= 306, str(len(t)))

# --- quiet hours ------------------------------------------------------------
check("daytime sends now", stl.text_send_time(dt("2026-11-10 14:00")) == dt("2026-11-10 14:00"))
check("20:59 sends now", stl.text_send_time(dt("2026-11-10 20:59")) == dt("2026-11-10 20:59"))
check("21:30 -> 08:00 next day", stl.text_send_time(dt("2026-11-10 21:30")) == dt("2026-11-11 08:00"))
check("02:00 -> 08:00 same day", stl.text_send_time(dt("2026-11-11 02:00")) == dt("2026-11-11 08:00"))
check("overdue lead seen at 07:40 waits for 08:00",
      stl.text_send_time(max(dt("2026-11-07 19:00"), dt("2026-11-08 07:40"))) == dt("2026-11-08 08:00"))
check("Sat 11:00 sends now", stl.text_send_time(dt("2026-11-14 11:00")) == dt("2026-11-14 11:00"))

# --- desk clock (Mon 2026-11-09) ---------------------------------------------
check("Mon 10:00 open -> now", stl.clock_start(dt("2026-11-09 10:00")) == dt("2026-11-09 10:00"))
check("Mon 07:00 -> 08:30", stl.clock_start(dt("2026-11-09 07:00")) == dt("2026-11-09 08:30"))
check("Thu 21:15 -> Fri 08:30", stl.clock_start(dt("2026-11-12 21:15")) == dt("2026-11-13 08:30"))
check("Fri 15:59 open", stl.clock_start(dt("2026-11-13 15:59")) == dt("2026-11-13 15:59"))
check("Fri 16:30 -> Mon 08:30", stl.clock_start(dt("2026-11-13 16:30")) == dt("2026-11-16 08:30"))
check("Sat -> Mon 08:30", stl.clock_start(dt("2026-11-14 12:00")) == dt("2026-11-16 08:30"))
check("Sun 23:00 -> Mon 08:30", stl.clock_start(dt("2026-11-15 23:00")) == dt("2026-11-16 08:30"))

# --- misc -------------------------------------------------------------------
check("phone keys match", stl.norm_phone("p:+447700900123") == stl.norm_phone("07700 900123"))
check("mins fmt", stl.fmt_mins(dt("2026-11-09 10:47") - dt("2026-11-09 09:00")) == "1h 47m")
check("slack escape", stl.esc("<b> & co") == "&lt;b&gt; &amp; co")

# --- reminder: once, in #leads; no escalation ----------------------------------------
import config  # noqa: E402

posts = []


class FakeSlack:
    def chat_postMessage(self, **kw):
        posts.append(kw)
        return {"channel": kw["channel"], "ts": "1"}

    def chat_getPermalink(self, **kw):
        return {"permalink": "https://slack/x"}


row = {h: "" for h in stl.HEADERS}
row.update({"Lead Key": "l:1", "Name": "Test", "Phone": "07700900000",
            "Clinic": "Maghera", "Clock Start": "2026-11-09 08:30:00",
            "Pings": "0", "Slack Channel": "CLEADS", "Slack TS": "1", "_row": 2})
saved = {n: getattr(stl, n) for n in ("state_ws", "read_state", "read_leads",
         "leads_tab_statuses", "update_cells", "slack", "_uid")}
saved_cfg = (config.STL_LIVE, config.STL_CHANNEL)
try:
    stl.state_ws = lambda: None
    stl.read_state = lambda ws: {"l:1": row}
    stl.read_leads = lambda: []
    stl.leads_tab_statuses = lambda: {}
    stl.update_cells = lambda ws, r, vals: row.update(vals)
    stl.slack = lambda: FakeSlack()
    stl._uid = lambda email: "DMARTIN"
    config.STL_LIVE, config.STL_CHANNEL = True, "CLEADS"
    stl.tick(now=dt("2026-11-09 08:50"), log=lambda m: None)
    stl.tick(now=dt("2026-11-09 09:05"), log=lambda m: None)
finally:
    for n, f in saved.items():
        setattr(stl, n, f)
    config.STL_LIVE, config.STL_CHANNEL = saved_cfg
check("15-min reminder in #leads, pings reception",
      posts and posts[0]["channel"] == "CLEADS"
      and f"<@{config.STL_RECEPTION_USER_ID}>" in posts[0]["text"], posts[:1])
check("no 30-min escalation post at all", len(posts) == 1, posts[1:])

# --- end-of-day report --------------------------------------------------------
check("report Mon 21:05", stl.report_due_at(dt("2026-11-09 12:00")) == dt("2026-11-09 21:05"))
check("report Fri 16:05", stl.report_due_at(dt("2026-11-13 09:00")) == dt("2026-11-13 16:05"))
check("no report Sat/Sun", stl.report_due_at(dt("2026-11-14 12:00")) is None
      and stl.report_due_at(dt("2026-11-15 12:00")) is None)


def lead_row(key, name, created, texted, outcome="", mins="", closed="", clock=None):
    r = {h: "" for h in stl.HEADERS}
    r.update({"Lead Key": key, "Name": name, "Clinic": "Maghera", "Phone": "07700900000",
              "Seen At": created, "Lead Created": created, "Texted At": texted,
              "Text Status": "sent" if texted else "queued", "Outcome": outcome,
              "Mins to Call": mins, "Closed": closed, "Clock Start": clock or created})
    return r


rows = [
    lead_row("l:1", "Amy", "2026-11-09 10:00:00", "2026-11-09 10:01:10", "called", "6"),
    lead_row("l:2", "Ben", "2026-11-09 12:00:00", "2026-11-09 12:01:30", "called", "20"),
    lead_row("l:3", "Cal", "2026-11-09 14:00:00", "2026-11-09 14:01:00"),
    lead_row("l:4", "Dee", "2026-11-09 15:00:00", "2026-11-09 15:01:00", "no answer", "9"),
    lead_row("l:5", "Old", "2026-11-06 10:00:00", "2026-11-06 10:01:00"),  # earlier, un-actioned
    lead_row("test:1", "Test", "2026-11-09 11:00:00", "", closed="test"),
    {**{h: "" for h in stl.HEADERS}, "Lead Key": "__report__", "Seen At": "2026-11-06 21:05:00"},
]
rep = stl.build_report(rows, dt("2026-11-06 21:05"), dt("2026-11-09 21:05"))
check("report counts", "*4* leads · *4* texted · *2* called" in rep, rep)
check("median + within 15", "median time to call *20 min*" in rep
      and "within 15 min: *1/2*" in rep, rep)
check("text speed shown", "texted in 1 min" in rep, rep)
check("not actioned lists Cal + earlier Old, not Dee", "Not actioned (2)" in rep
      and "Cal (" in rep.split("Not actioned")[1] and "Old (" in rep.split("Not actioned")[1]
      and "Dee (" not in rep.split("Not actioned")[1], rep)
check("test rows + marker excluded", "Test" not in rep and "report marker" not in rep)
empty = stl.build_report([], dt("2026-11-09 08:00"), dt("2026-11-09 21:05"))
check("quiet day", "No new leads." in empty and "Every lead has been actioned" in empty)

# --- report sends once per desk day ------------------------------------------
sent = []
state = {}
saved = {n: getattr(stl, n) for n in ("slack", "_uid", "update_cells", "append_row")}
try:
    stl.slack = lambda: type("S", (), {"chat_postMessage":
                                       lambda self, **kw: sent.append(kw)})()
    stl._uid = lambda e: {"julie@elitephysiocookstown.co.uk": "DJULIE"}.get(e, "DMARTIN")
    stl.update_cells = lambda ws, r, v: state[stl.REPORT_KEY].update(v)
    config.STL_LIVE = True
    stl.append_row = lambda ws, v: state.update({stl.REPORT_KEY: {**v, "_row": 9}}) or 9
    for t in ("2026-11-09 21:00", "2026-11-09 21:06", "2026-11-09 21:07",
              "2026-11-10 09:00", "2026-11-10 21:06"):
        stl.maybe_send_report(None, state, dt(t), log=lambda m: None)
finally:
    for n, f in saved.items():
        setattr(stl, n, f)
    config.STL_LIVE = saved_cfg[0]
check("report: none before close, once at 21:06, again next evening",
      len(sent) == 4, len(sent))
check("report goes privately to Martin AND Julie, nowhere else",
      sorted(p["channel"] for p in sent) == ["DJULIE", "DJULIE", "DMARTIN", "DMARTIN"],
      [p["channel"] for p in sent])

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
