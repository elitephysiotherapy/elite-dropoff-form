"""Tests for the bookings-sheet / EOD alignment (2026-10-01).

Pins the rules that make the EOD "New Bookings" figure and the bookings sheet's
weekly tab agree:
  - EOD New Bookings + Leads use the same Sunday-Saturday week as the sheet's
    W/C tabs; the Sunday 07:00 weekly wrap reports the week that just ended,
  - a row's Status says Deleted / Cancelled / Changed to <type>, and stays blank
    for a live IA (including a DNA — the booking was still made),
  - the Dashboard leaves Status-flagged rows out of its counts.

Run: ./venv/bin/python test_bookings_status.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import bookings_fetch as bf  # noqa: E402
import eod_stats as eod  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}" + (f"   ({detail})" if detail else ""))
    else:
        failures += 1
        print(f"FAIL  {label}" + (f"   ({detail})" if detail else ""))


L = eod.LONDON

# --- EOD bookings week = the sheet's W/C tab ---------------------------------
for now, want_sun in [
    (datetime(2026, 9, 28, 12, 0, tzinfo=L), date(2026, 9, 27)),   # Mon
    (datetime(2026, 10, 1, 20, 0, tzinfo=L), date(2026, 9, 27)),   # Thu
    (datetime(2026, 10, 2, 15, 45, tzinfo=L), date(2026, 9, 27)),  # Fri
    (datetime(2026, 10, 3, 9, 0, tzinfo=L), date(2026, 9, 27)),    # Sat
]:
    sun, end = eod.bookings_week(now)
    check(f"{now:%a} {now:%d %b} -> week from Sun {want_sun:%d %b}",
          sun == want_sun and end == now, f"{sun}, {end}")
    tab = bf.week_tab_name(now.replace(tzinfo=None))
    check(f"{now:%a} matches sheet tab", tab == f"W/C {sun:%d %b %Y}", tab)

wrap = datetime(2026, 10, 4, 7, 0, tzinfo=L)                          # Sun wrap
sun, end = eod.bookings_week(wrap)
check("Sunday wrap reports the week just ended",
      sun == date(2026, 9, 27) and end == datetime(2026, 10, 4, tzinfo=L),
      f"{sun}, {end}")

# --- Status -------------------------------------------------------------------
IA = next(iter(config.BOOKINGS_IA_TYPE_IDS))
NON_IA = "999"
types = {NON_IA: "4. Club Follow Up Appointment"}


def appt(type_id=IA, **kw):
    return {"appointment_type": {"links": {"self": f"x/appointment_types/{type_id}"}}, **kw}


check("live IA -> blank", bf.status_for(appt(), types) == "")
check("DNA IA -> blank", bf.status_for(appt(did_not_arrive=True), types) == "")
check("cancelled -> Cancelled",
      bf.status_for(appt(cancelled_at="2026-09-29T15:16:35Z"), types) == "Cancelled")
check("deleted -> Deleted",
      bf.status_for(appt(deleted_at="2026-09-29T15:04:54Z"), types) == "Deleted")
check("deleted wins over type change",
      bf.status_for(appt(NON_IA, deleted_at="x"), types) == "Deleted")
got = bf.status_for(appt(NON_IA), types)
check("type changed -> Changed to <name>",
      got == "Changed to 4. Club Follow Up Appointment", got)

# --- Column layout: Status added on the right, nothing shifted ----------------
check("appointment_id still column M", bf.COLUMNS.index("appointment_id") == 12)
check("pulled_at still column N", bf.COLUMNS.index("pulled_at") == 13)
check("status is column O", bf.COLUMNS.index("status") == 14)

print()
print("ALL PASS" if not failures else f"{failures} FAILURE(S)")
sys.exit(1 if failures else 0)
