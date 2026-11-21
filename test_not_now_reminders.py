"""Tests for the day-30 "not now" reminders (no Sheets or Slack).

Run: ./venv/bin/python test_not_now_reminders.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import not_now_reminders as nn  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}   {detail}")


def row(**kw):
    base = {"Date": "20/10/26", "Patient": "Sarah Quinn", "Phone": "07700 900123",
            "Preferred Clinic": "Maghera", "Lead Source": "Meta Ad", "Body Area": "Knee",
            "Status": "pending", "Last Contacted": "21/10/26",
            "Reason Not Booked": "Not now, will think about it", "Notes": "",
            "Callback Needed (by who)": "", "Meta Lead ID": "l:1"}
    base.update(kw)
    return base


for t in ["not now", "Not yet - knee settling", "will think about it", "said she'd ring us back",
          "ring back after Christmas", "too busy with work", "wants to wait and see",
          "check with her husband", "in a few weeks", "maybe later", "holding off till new year"]:
    check(f"not now: '{t}'", nn.says_not_now(t))
for t in ["not interested", "wrong number", "too far - booked elsewhere? not interested",
          "thought it was NHS", "no answer x3", ""]:
    check(f"not a 'not now': '{t}'", not nn.says_not_now(t))

check("said on = Last Contacted", nn.said_on(row(), date(2026, 11, 21)) == date(2026, 10, 21))
check("said on falls back to lead date",
      nn.said_on(row(**{"Last Contacted": ""}), date(2026, 11, 21)) == date(2026, 10, 20))

w, due = nn.plan([row()], {}, date(2026, 11, 21))
check("tracked with remind date +30 (next working day)",
      w and w[0][1]["Remind On"] == "2026-11-20", w)
check("backlog: due on first run", len(due) == 1)
w, due = nn.plan([row(**{"Last Contacted": "20/11/26"})], {}, date(2026, 11, 21))
check("recent not-now: tracked, not due yet", len(w) == 1 and not due and
      w[0][1]["Remind On"] == "2026-12-21", w)
check("booked lead ignored", nn.plan([row(Status="booked")], {}, date(2026, 11, 21)) == ([], []))
check("very old not-now ignored",
      nn.plan([row(**{"Last Contacted": "01/06/26"})], {}, date(2026, 11, 21)) == ([], []))
st = {"l:1": {"First Seen": "2026-10-21", "Said On": "21/10/26", "Remind On": "2026-11-20",
              "Reminded At": "2026-11-20 09:30:00"}}
check("reminded once -> not again", nn.plan([row()], st, date(2026, 11, 23)) == ([], []))
w, due = nn.plan([row(**{"Last Contacted": "22/11/26"})], st, date(2026, 11, 23))
check("new 'not now' after the call -> fresh 30 days",
      w and w[0][1]["Remind On"] == "2026-12-22" and w[0][1]["Reminded At"] == "", w)
check("Christmas closure skipped", nn.next_working_day_on_or_after(date(2026, 12, 25)) > date(2026, 12, 25))

txt = nn.build_dm([(row(), {"Said On": "21/10/26", "Remind On": "2026-11-20"}, "l:1")],
                  date(2026, 11, 23))
check("DM names the lead, what they said and when", "Sarah Quinn" in txt and "21/10/26" in txt
      and "will think about it" in txt and "Check Cliniko first" in txt, txt)
from datetime import datetime  # noqa: E402
L = nn.LONDON
check("DM at 09:30 weekdays", nn.dm_due_now(datetime(2026, 11, 23, 9, 35, tzinfo=L), None))
check("not before 09:30", not nn.dm_due_now(datetime(2026, 11, 23, 9, 0, tzinfo=L), None))
check("not at weekends", not nn.dm_due_now(datetime(2026, 11, 22, 9, 35, tzinfo=L), None))

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
