"""Tests for the reactivation call card rules (no Sheets, Slack or Cliniko).

Run: ./venv/bin/python test_reactivation_calls.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import reactivation_calls as rc  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}   {detail}")


def row(**kw):
    base = {"appointment_id": "a1", "Reactivation Status": "pending",
            "Clinical / Non-Clinical": "non clinical", "Next Step (Physio)": "reactivate",
            "pulled_at": "2026-11-17 5:16:44", "Cancellation Date": "2026-11-16 8:55",
            "Patient Name": "Sarah Quinn", "Physio": "Aoife O'Kane", "Clinic": "Elite Physiotherapy Maghera",
            "Body Area": "Knee", "Drop-off Type": "cancelled", "Session #": "3",
            "Cancellation Reason": "Work", "Physio Action": "", "Physio Reactivation Notes": "",
            "Reception Reactivation Notes": ""}
    base.update(kw)
    return base


L = rc.LONDON

# eligibility
check("pending non-clinical is eligible", rc.row_eligible(row())[0])
check("blank status eligible", rc.row_eligible(row(**{"Reactivation Status": ""}))[0])
check("leave not eligible", not rc.row_eligible(row(**{"Reactivation Status": "leave"}))[0])
check("rebooked not eligible", not rc.row_eligible(row(**{"Reactivation Status": "rebooked"}))[0])
check("clinical not eligible", not rc.row_eligible(row(**{"Clinical / Non-Clinical": "clinical"}))[0])
check("not appropriate not eligible",
      not rc.row_eligible(row(**{"Next Step (Physio)": "not appropriate"}))[0])
check("physio contacting directly not eligible",
      not rc.row_eligible(row(**{"Next Step (Physio)": "physio contacting patient directly"}))[0])
check("no appointment id not eligible", not rc.row_eligible(row(appointment_id=""))[0])

# ready date
check("classified row ready the day it lands", rc.ready_date(row()) == date(2026, 11, 17))
check("unclassified row waits 2 working days",
      rc.ready_date(row(**{"Clinical / Non-Clinical": "", "Next Step (Physio)": ""})) == date(2026, 11, 19))
check("unclassified landing Thu waits to Mon",
      rc.ready_date(row(**{"Clinical / Non-Clinical": "", "pulled_at": "2026-11-19 5:00:00"})) == date(2026, 11, 23))
check("Saturday landing rolls to Monday",
      rc.ready_date(row(pulled_at="2026-11-21 5:00:00")) == date(2026, 11, 23))

# cadence
check("first call due on ready date", rc.next_due(row(), None) == date(2026, 11, 17))
check("pre-existing contact_attempted counts as call 1",
      rc.attempts_of(row(**{"Reactivation Status": "contact_attempted"}), None) == 1)
st1 = {"Attempts": "1", "Last Attempt At": "2026-11-24", "Outcome": "no answer"}
check("call 2 one day after call 1", rc.next_due(row(), st1) == date(2026, 11, 25))
st2 = {"Attempts": "2", "Last Attempt At": "2026-11-25", "Outcome": "no answer"}
check("call 3 eleven days after call 2", rc.next_due(row(), st2) == date(2026, 12, 7), rc.next_due(row(), st2))
st3 = {"Attempts": "3", "Last Attempt At": "2026-12-07", "Outcome": "leave"}
check("after 3 calls -> finished", rc.next_due(row(), st3) is None)
stf = {"Attempts": "1", "Last Attempt At": "2026-11-27", "Outcome": "no answer"}
check("Friday no answer -> Monday", rc.next_due(row(), stf) == date(2026, 11, 30))
stc = {"Attempts": "1", "Outcome": "call back", "Next Due": "2026-12-01"}
check("call back uses its date", rc.next_due(row(), stc) == date(2026, 12, 1))
check("rebooked outcome -> finished",
      rc.next_due(row(), {"Attempts": "1", "Outcome": "rebooked"}) is None)

# taps
cells, status, note = rc.apply_tap("noanswer", {"Attempts": "0"}, date(2026, 11, 24), "Kerry")
check("no answer 1 -> contact_attempted", status == "contact_attempted" and cells["Attempts"] == "1")
check("note is dated + named", note == "24/11 Kerry: no answer (call 1)", note)
cells, status, note = rc.apply_tap("noanswer", {"Attempts": "2"}, date(2026, 12, 7), "Kerry")
check("3rd no answer -> leave", status == "leave" and cells["Outcome"] == "leave", (status, cells))
cells, status, note = rc.apply_tap("rebooked", {"Attempts": "0"}, date(2026, 11, 24), "Megan")
check("rebooked leaves status to the morning Cliniko check", status == "" and cells["Outcome"] == "rebooked")
cells, status, note = rc.apply_tap("callback", {"Attempts": "0"}, date(2026, 11, 27), "Megan")
check("call back next week (Fri -> Fri)", cells["Next Due"] == "2026-12-04", cells)
cells, status, note = rc.apply_tap("notinterested", {"Attempts": "0"}, date(2026, 11, 24), "Megan")
check("not interested -> leave", status == "leave")

# card + list time
txt = rc.card_text(row(**{"Physio Reactivation Notes": "knee settled, wants gym plan"}), "07700900123", 1)
check("card shows name, clinic, drop-off, physio note, call 1 of 3",
      "Sarah Quinn" in txt and "Maghera" in txt and "Cancelled, nothing rebooked" in txt
      and "gym plan" in txt and "Call 1 of 3" in txt, txt)
check("4 buttons", len(rc.actions_block("a1")["elements"]) == 4)
dt = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=L)
check("list posts at 10:00 weekday", rc.list_due_now(dt("2026-11-23 10:02"), None))
check("not before 10", not rc.list_due_now(dt("2026-11-23 09:55"), None))
check("not twice", not rc.list_due_now(dt("2026-11-23 10:30"), dt("2026-11-23 10:02")))
check("not at weekend", not rc.list_due_now(dt("2026-11-28 10:02"), None))

# #reactivation win post
check("win = name + physio mention", rc.win_text(row(), "<@U1>") == "Sarah Quinn <@U1>")
check("IA cancelled -> 'Name IA'",
      rc.win_text(row(**{"Drop-off Type": "iacna"}), "<@U1>") == "Sarah Quinn IA")
check("IA no-show -> 'Name IA'",
      rc.win_text(row(**{"Drop-off Type": "iadna"}), "<@U1>") == "Sarah Quinn IA")
check("IA attended, didn't rebook -> physio (they were seen)",
      rc.win_text(row(**{"Drop-off Type": "iadnr"}), "<@U1>") == "Sarah Quinn <@U1>")
check("unknown physio -> plain name", rc.physio_mention("Nobody Here") == "Nobody Here")
n_ok = rc.tap_note("rebooked", {"Attempts": "1"}, "U1", datetime(2026, 11, 23, 10, 30), True)
n_bad = rc.tap_note("rebooked", {"Attempts": "1"}, "U1", datetime(2026, 11, 23, 10, 30), False)
check("card says win was posted", "Posted in #reactivation" in n_ok)
check("card asks for manual post if bot can't", "please post the win" in n_bad)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
