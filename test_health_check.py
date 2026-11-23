"""Tests for the end-of-day health check section (no Sheets or Slack).

Run: ./venv/bin/python test_health_check.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speed_to_lead as stl  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}   {detail}")


L = stl.LONDON
NOW = datetime(2026, 11, 23, 21, 5, tzinfo=L)       # Monday report time
leads = [
    {"Lead Key": "a", "Outcome": "no answer", "Closed": "", "Clock Start": "2026-11-23 09:23:00",
     "Attempt Log": "Mon 23 Nov 09:40 no answer (U1); Mon 23 Nov 14:10 no answer (U1)"},
    {"Lead Key": "b", "Outcome": "", "Closed": "", "Clock Start": "2026-11-23 08:30:00",
     "Attempt Log": ""},
    {"Lead Key": "__duelist__", "Seen At": "2026-11-23 17:31:00"},
]
react = [
    {"Appt ID": "1", "Posted At": "2026-11-23 09:04:16", "Slack TS": "1", "Attempts": "1",
     "Outcome": "no answer", "Attempt Log": "23/11 Kerry: no answer (call 1)"},
    {"Appt ID": "2", "Posted At": "2026-11-23 09:04:18", "Slack TS": "2", "Attempts": "0",
     "Outcome": "", "Attempt Log": ""},
    {"Appt ID": "3", "Posted At": "2026-11-23 09:29:20", "Slack TS": "3", "Attempts": "0",
     "Outcome": "", "Scheduled For": "2026-11-27", "Sched Posted": ""},
    {"Appt ID": "__review__", "Posted At": "2026-11-23 08:34:15"},
]
save = [{"Alerted At": "2026-11-23 10:14:00", "Outcome": ""}]
notnow = [{"Key": "x", "Reminded At": "2026-11-23 09:34:21"}]
lines = stl.build_health(leads, save, react, notnow, {"speed_to_lead": 0, "save_calls": 0}, NOW)
txt = "\n".join(lines)
print(txt)
check("counts today's lead taps", "2 call taps today" in txt)
check("flags a lead nobody tapped", "1 lead with no tap at all" in txt)
check("due lists ran", "Due-a-call lists ran today" in txt)
check("save call not tapped flagged", "1 not tapped" in txt)
check("reactivation: 3 posted, 1 tap, 1 never tapped, 1 on hold",
      "3 cards posted, 1 tap today" in txt and "1 card never tapped" in txt and "1 on hold" in txt)
check("Monday review posted", "Monday review posted" in txt)
check("not-now sent", "1 sent to Sinéad Rocks" in txt)
check("no errors", "No automation errors" in txt)
lines = stl.build_health([{"Lead Key": "__duelist__", "Seen At": "2026-11-20 12:31:00"}], None, None,
                         None, {"reactivation_calls": 4}, NOW)
txt = "\n".join(lines)
check("missing due list flagged", "No due-a-call list ran today" in txt)
check("unreadable tabs flagged", "couldn't read the Save Calls tab" in txt)
check("failing job named", "Failing:* Reactivation cards (4 runs)" in txt, txt)
print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
