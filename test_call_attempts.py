"""Tests for call-attempt logging, the due-a-call list and the save-call alert
rules (no Sheets, Slack, Twilio or Cliniko).

Run: ./venv/bin/python test_call_attempts.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import speed_to_lead as stl  # noqa: E402
import save_call_alert as sca  # noqa: E402

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


def lead(**kw):
    base = {"Lead Key": "k1", "Name": "Sarah Quinn", "Phone": "07700 900123",
            "Clinic": "Maghera", "Body Area": "knee", "Text Status": "sent",
            "Closed": "", "Outcome": "", "Called At": "", "Attempts": "",
            "Attempt Log": "", "Last Attempt At": "",
            "Lead Created": "2026-11-23 19:40:00",      # Mon evening
            "Clock Start": "2026-11-23 19:40:00"}
    base.update(kw)
    return base


# --- attempts_of: old rows ------------------------------------------------
check("no taps = 0 attempts", stl.attempts_of(lead()) == 0)
check("old row with Outcome but no count = 1",
      stl.attempts_of(lead(Outcome="no answer")) == 1)
check("count read when present", stl.attempts_of(lead(Attempts="3", Outcome="no answer")) == 3)

# --- record_attempt -------------------------------------------------------
s = lead()
cells, s1 = stl.record_attempt(s, "no answer", "U1", dt("2026-11-23 19:50"), dt("2026-11-23 19:40"))
check("first tap -> attempt 1", s1["Attempts"] == "1")
check("first tap sets Called At + Mins to Call",
      cells.get("Called At") and cells.get("Mins to Call") == "10")
cells2, s2 = stl.record_attempt(s1, "no answer", "U1", dt("2026-11-23 19:50") .replace(second=30),
                                dt("2026-11-23 19:40"))
check("double tap within 60s = still 1 attempt", s2["Attempts"] == "1", s2["Attempts"])
cells3, s3 = stl.record_attempt(s1, "no answer", "U1", dt("2026-11-24 12:35"), dt("2026-11-23 19:40"))
check("later tap -> attempt 2", s3["Attempts"] == "2")
check("Called At (first-call speed) unchanged on later taps", "Called At" not in cells3)
check("log keeps both attempts", s3["Attempt Log"].count("no answer") == 2, s3["Attempt Log"])
_, s4 = stl.record_attempt(s3, "called", "U2", dt("2026-11-24 18:00"), dt("2026-11-23 19:40"))
check("Called counts as an attempt too", s4["Attempts"] == "3")

# --- next_call_due --------------------------------------------------------
check("never tapped -> due at clock start",
      stl.next_call_due(lead()) == dt("2026-11-23 19:40"))
d = stl.next_call_due(s3)          # last try Tue 12:35 -> 15:35
check("retry 3h after last try", d == dt("2026-11-24 15:35"), d)
late = lead(Attempts="1", Outcome="no answer", **{"Last Attempt At": "2026-11-23 19:50:00"})
d = stl.next_call_due(late)        # 22:50 is after the desk shuts -> Tue 08:30
check("retry outside desk hours moves to next opening", d == dt("2026-11-24 08:30"), d)
fri = lead(Attempts="1", Outcome="no answer", **{"Lead Created": "2026-11-27 10:00:00",
           "Clock Start": "2026-11-27 10:00:00", "Last Attempt At": "2026-11-27 14:00:00"})
d = stl.next_call_due(fri)         # Fri 17:00 desk shut 16:00, weekend -> Mon 08:30
check("Friday afternoon retry rolls to Monday", d == dt("2026-11-30 08:30"), d)
check("spoken to -> no more calls", stl.next_call_due(s4) is None)
check("booked -> no more calls", stl.next_call_due(lead(Closed="booked")) is None)
check("expired (never tapped) still chased within 4 days",
      stl.next_call_due(lead(Closed="expired")) is not None)
check("6 attempts -> stop",
      stl.next_call_due(lead(Attempts=str(config.STL_MAX_ATTEMPTS), Outcome="no answer",
                             **{"Last Attempt At": "2026-11-24 12:00:00"})) is None)
old = lead(Attempts="2", Outcome="no answer", **{"Last Attempt At": "2026-11-27 13:00:00"})
check("past 4 days -> stop (final text takes over)", stl.next_call_due(old) is None,
      stl.next_call_due(old))
check("repeat enquiry not chased",
      stl.next_call_due(lead(**{"Text Status": "repeat - not texted"})) is None)

# --- due list -------------------------------------------------------------
rows = [lead(), lead(**{"Lead Key": "k2", "Name": "John Doe"}, Attempts="1",
                Outcome="no answer", **{"Last Attempt At": "2026-11-24 11:00:00"}),
        lead(**{"Lead Key": "k3", "Name": "Done Person"}, Outcome="called", Attempts="1"),
        {"Lead Key": "__report__", "Name": "(marker)", "Closed": "meta"}]
txt = stl.build_due_list(rows, dt("2026-11-24 12:30"))
check("list includes never-called lead", "Sarah Quinn" in txt and "not called yet" in txt, txt)
check("list excludes lead tried 90 min ago", "John Doe" not in txt, txt)
check("list excludes spoken-to lead and markers", "Done Person" not in txt and "marker" not in txt)
txt = stl.build_due_list(rows, dt("2026-11-24 17:30"))
check("evening list includes the lunchtime no-answer", "John Doe" in txt and "call 2 of 6" in txt, txt)
check("nobody due -> no message", stl.build_due_list([rows[2]], dt("2026-11-24 17:30")) is None)

check("slot posts at 12:31", stl.list_slot_due(dt("2026-11-24 12:31"), None) == dt("2026-11-24 12:30"))
check("slot not twice", stl.list_slot_due(dt("2026-11-24 12:40"), dt("2026-11-24 12:31")) is None)
check("stale slot skipped after restart", stl.list_slot_due(dt("2026-11-24 13:30"), None) is None)
check("no 17:30 list on Friday", stl.list_slot_due(dt("2026-11-27 17:31"), None) is None)
check("no list at weekend", stl.list_slot_due(dt("2026-11-28 12:31"), None) is None)
check("no list on a closure day",
      stl.list_slot_due(dt("2026-12-24 12:31"), None) is None
      if __import__("datetime").date(2026, 12, 24) in config.CLINIC_CLOSED_DAYS else True)

# --- attempt note ---------------------------------------------------------
note = stl.attempt_note(s3, "no answer", "U1", dt("2026-11-24 12:35"), dt("2026-11-23 19:40"))
check("note shows attempt 2 of 6", "attempt 2 of 6" in note, note)
check("note says when next call is due", "Next call due after 15:35 today" in note, note)
_, s6 = stl.record_attempt(lead(Attempts="5", Outcome="no answer",
                                **{"Last Attempt At": "2026-11-24 09:00:00"}),
                           "no answer", "U1", dt("2026-11-24 17:30"), dt("2026-11-23 19:40"))
note = stl.attempt_note(s6, "no answer", "U1", dt("2026-11-24 17:30"), dt("2026-11-23 19:40"))
check("6th no answer says that's the last call", "last call" in note, note)

# --- save-call rules ------------------------------------------------------
IA = "382563815654429852"
start_at = dt("2026-11-21 18:00")
now = dt("2026-11-24 10:15")


def appt(**kw):
    a = {"id": "a1", "appointment_type": {"links": {"self": f"https://x/appointment_types/{IA}"}},
         "starts_at": "2026-11-24T15:00:00Z", "created_at": "2026-11-20T10:00:00Z",
         "cancelled_at": "2026-11-24T10:05:00Z", "did_not_arrive": False,
         "cancellation_reason": 50, "cancellation_note": ""}
    a.update(kw)
    return a


types = config.FORM_CHASE_TYPE_IDS
check("cancelled first visit qualifies", sca.qualifies(appt(), now, start_at, types))
check("review appointment type ignored",
      not sca.qualifies(appt(appointment_type={"links": {"self": "https://x/appointment_types/999"}}),
                        now, start_at, types))
check("cancelled before go-live ignored",
      not sca.qualifies(appt(cancelled_at="2026-11-21T10:00:00Z"), dt("2026-11-21 19:00"),
                        start_at, types))
check("cancelled >24h ago ignored",
      not sca.qualifies(appt(cancelled_at="2026-11-23T09:00:00Z"), now, start_at, types))
na = appt(cancelled_at=None, did_not_arrive=True, updated_at="2026-11-24T10:00:00Z")
check("no-show qualifies", sca.qualifies(na, now, start_at, types))
check("event label for no-show", sca.event_of(na)[0] == "did not arrive")
check("reason text: code 50 = Other", sca.reason_text(appt()) == "Other")
check("reason text includes note",
      'Work: "can\'t get off"' == sca.reason_text(appt(cancellation_reason=60,
                                                      cancellation_note="can't get off")),
      sca.reason_text(appt(cancellation_reason=60, cancellation_note="can't get off")))
check("hours before", sca.hours_before(appt(), sca.event_of(appt())[1]) == "4h before")
check("ring line in desk hours", "within the hour" in sca.ring_line(dt("2026-11-24 10:15")))
check("ring line out of hours names opening",
      "Mon 08:30" in sca.ring_line(dt("2026-11-28 11:00")), sca.ring_line(dt("2026-11-28 11:00")))
b = sca.card_blocks({"name": "Sarah Quinn", "phone": "07700900123", "clinic": "Maghera",
                     "start": dt("2026-11-24 15:00"), "physio": "Aoife", "type": "Initial Appointment",
                     "event": "cancelled", "reasons": ["Meta lead", "Brand new patient"],
                     "booked": dt("2026-11-20 10:00"), "reason": "Other", "before": "4h before"},
                    now, "a1", live=False)
t = b[0]["text"]["text"]
check("card names the visit, the tags and safe mode",
      "First visit cancelled" in t and "Meta lead + " not in t and "Meta lead · Brand new" in t
      and "SAFE MODE" in t, t)
check("card has 3 save buttons", [e["action_id"] for e in b[1]["elements"]] ==
      ["save|rebooked|a1", "save|declined|a1", "save|noanswer|a1"])

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
