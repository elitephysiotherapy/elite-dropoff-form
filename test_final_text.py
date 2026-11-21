"""Tests for the speed-to-lead FINAL TEXT rules (no Sheets, Slack or Twilio).

Pins what Martin agreed 2026-11-21: one last text with intro video + free
discovery call link, 4 days after the lead reached the sheet, Mon-Fri
09:00-20:00 only, never to anyone booked / spoken to / replied / opted out.

Run: ./venv/bin/python test_final_text.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
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


def row(**kw):
    base = {"Lead Key": "k1", "Name": "Sarah Quinn", "Phone": "07700 900123",
            "Text Status": "sent", "Closed": "expired", "Outcome": "",
            "Replied At": "", "Final Text Status": "",
            "Lead Created": "2026-11-23 10:00:00"}
    base.update(kw)
    return base


NOW = dt("2026-11-27 11:00")          # exactly 4 days + 1h after creation
P = stl.norm_phone("07700 900123")


def decide(s=None, now=NOW, status="", booked=(), supp=(), live=True):
    return stl.final_text_decision(s or row(), now=now, lead_status=status,
                                   booked_phones=set(booked),
                                   suppressed_phones=set(supp), live=live)


check("due 4 days on -> send", decide()[0] == "send")
check("3 days -> wait", decide(now=dt("2026-11-26 11:00"))[0] == "wait")
check("8 days -> too old, skip", decide(now=dt("2026-12-01 11:00"))[0] == "skip")
check("before go-live cut-off skipped",
      decide(row(**{"Lead Created": "2026-11-10 10:00:00"}), now=dt("2026-11-14 11:00"))[0] == "skip")
check("already sent -> skip", decide(row(**{"Final Text Status": "sent"}))[0] == "skip")
check("failed is not retried blindly",
      decide(row(**{"Final Text Status": "FAILED: x"}))[0] == "skip")
check("safe-mode test row can go live later",
      decide(row(**{"Final Text Status": "test sent (safe mode)"}))[0] == "send")
check("first text never sent -> skip",
      decide(row(**{"Text Status": "too old when seen (42h) - not texted"}))[0] == "skip")
check("safe-mode first text not enough when live",
      decide(row(**{"Text Status": "sent (safe mode)"}))[0] == "skip")
check("safe-mode first text ok in safe mode",
      decide(row(**{"Text Status": "sent (safe mode)"}), live=False)[0] == "send")
check("booked on sheet -> skip", decide(status="booked")[0] == "skip")
check("declined on sheet -> skip", decide(status="declined")[0] == "skip")
check("pending on sheet -> send", decide(status="pending")[0] == "send")
check("booked in Cliniko by phone -> skip", decide(booked={P})[0] == "skip")
check("spoken to (Called) -> skip", decide(row(Outcome="called"))[0] == "skip")
check("'no answer' still gets it", decide(row(Outcome="no answer"))[0] == "send")
check("any reply -> skip", decide(row(**{"Replied At": "2026-11-24 09:00:00"}))[0] == "skip")
check("opted out (closed) -> skip", decide(row(Closed="opted out"))[0] == "skip")
check("replied no (closed) -> skip", decide(row(Closed="replied no"))[0] == "skip")
check("repeat enquiry row -> skip", decide(row(Closed="repeat"))[0] == "skip")
check("suppressed phone -> skip", decide(supp={P})[0] == "skip")
check("no phone -> skip", decide(row(Phone=""))[0] == "skip")

# --- send window: Mon-Fri 09:00-20:00, not closure days ----------------------
check("Fri 10:00 open", stl.final_window_open(dt("2026-11-27 10:00")))
check("Fri 20:00 closed", not stl.final_window_open(dt("2026-11-27 20:00")))
check("Fri 08:59 closed", not stl.final_window_open(dt("2026-11-27 08:59")))
check("Sat closed", not stl.final_window_open(dt("2026-11-28 11:00")))
check("Sun closed", not stl.final_window_open(dt("2026-11-29 11:00")))
check("Mon 19:59 open", stl.final_window_open(dt("2026-11-30 19:59")))
check("closure day closed", not stl.final_window_open(dt("2026-12-28 11:00")))

# --- reply wording -------------------------------------------------------------
for w in ["STOP", "stop.", "No thanks", "not interested", "Please remove me",
          "leave me alone", "wrong number", "UNSUBSCRIBE", "no thank you!"]:
    check(f"no-contact: {w!r}", stl.wants_no_contact(w))
for w in ["Tuesday after 5 suits", "Yes please call me", "can you ring tomorrow",
          "what does it cost?", ""]:
    check(f"normal reply kept: {w!r}", not stl.wants_no_contact(w))

# --- the text ------------------------------------------------------------------
body = stl.compose_final_text(name="SARAH QUINN", video="https://e.example/v",
                              discovery="https://e.example/d")
check("greets by tidy first name", body.startswith("Hi Sarah, "))
check("both links present", "https://e.example/v" in body and "https://e.example/d" in body)
check("opt-out line", body.endswith("Reply STOP to opt out."))
check("GSM-safe (ASCII only)", body.isascii())
check("no pressure words",
      not any(w in body.lower() for w in ("last chance", "limited", "hurry", "spaces")))
check("blank name -> Hi there",
      stl.compose_final_text(name="", video="v", discovery="d").startswith("Hi there, "))
check("tells them which service to pick", "Free Discovery Call" in body)
check("held until the video link is set (discovery link is preset)",
      config.FINAL_TEXT_VIDEO_LINK == ""
      and config.FINAL_TEXT_DISCOVERY_LINK == "https://elitephysiocookstown.co.uk/contact/")

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
