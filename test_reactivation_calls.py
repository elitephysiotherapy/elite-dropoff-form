"""Tests for reactivation call cards: review flow, dated notes, taps (no Sheets,
Slack or Cliniko).

Run: ./venv/bin/python test_reactivation_calls.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import phase1_fetch as p1  # noqa: E402
import reactivation_calls as rc  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}   {detail}")


MON, TUE, WED = date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25)


def row(**kw):
    base = {"appointment_id": "a1", "Reactivation Status": "pending",
            "Clinical / Non-Clinical": "non clinical", "Next Step (Physio)": "reactivate",
            "Next Action Step": "", "_tab": "W/C 23 Nov 2026",
            "pulled_at": "2026-11-23 5:16:44", "Cancellation Date": "2026-11-22 8:55",
            "Patient Name": "Sarah Quinn", "Physio": "Aoife O'Kane",
            "Clinic": "Elite Physiotherapy Maghera", "Body Area": "Knee",
            "Drop-off Type": "cancelled", "Session #": "3", "Cancellation Reason": "Work",
            "Physio Action": "", "Physio Reactivation Notes": "",
            "Reception Reactivation Notes": "", "Marty's Comments": ""}
    base.update(kw)
    return base


# --- sheet layout ----------------------------------------------------------
cols = p1.SHEET_COLUMNS
check("Next Action Step sits right after Marty's Comments",
      cols.index("next_action_step") == cols.index("martys_comments") + 1)
check("header label", p1.HEADER_LABELS["next_action_step"] == "Next Action Step")
check("7 dropdown options incl. Marty to call",
      p1.NEXT_ACTION_OPTIONS == ["Reception to call", "Sinead Rocks to call", "Marty to call",
                                 "Physio to call", "Physio To Complete Tricky Patient Form",
                                 "Leave", "Rebooked"], p1.NEXT_ACTION_OPTIONS)
check("21 columns, last is U", len(cols) == 21 and p1.LAST_COL == "U")
check("id + pulled_at still the hidden pair at the end",
      cols[-2:] == ["appointment_id", "pulled_at"] and p1.HIDDEN_COLUMNS == ("appointment_id", "pulled_at"))
old20 = ["2026-11-19 16:40"] + [""] * 17 + ["2076975515457034014", "2026-11-17 5:16:44"]
new21 = old20[:16] + [""] + old20[16:]
check("current-layout row is not mistaken for an old-layout row", not p1.is_old_layout_row(new21))

# --- drop-off week ---------------------------------------------------------
check("new drop-off: card on the day it lands", rc.decide(row(), None, MON)["kind"] == "week")
d = rc.decide(row(), {"Attempts": "1", "Last Attempt At": "2026-11-23", "Slack TS": "1",
                      "Outcome": "no answer", "Owner": rc.WEEK_OWNER}, TUE)
check("no answer -> retry next working day", d and d["kind"] == "retry" and not d["new_card"], d)
check("2 calls -> waits for review",
      rc.decide(row(), {"Attempts": "2", "Last Attempt At": "2026-11-24", "Slack TS": "1",
                        "Outcome": "no answer"}, WED) is None)
check("last week's blank row waits for the review",
      rc.decide(row(_tab="W/C 16 Nov 2026"), None, MON) is None)
check("clinical row not in reception's week",
      rc.decide(row(**{"Clinical / Non-Clinical": "clinical"}), None, MON) is None)
check("physio contacting directly not in reception's week",
      rc.decide(row(**{"Next Step (Physio)": "physio contacting patient directly"}), None, MON) is None)
check("already rebooked -> nothing", rc.decide(row(**{"Reactivation Status": "rebooked"}), None, MON) is None)

# --- Monday review decisions -----------------------------------------------
for na in ("Reception to call", "Sinead Rocks to call", "Marty to call", "Physio to call"):
    d = rc.decide(row(_tab="W/C 16 Nov 2026", **{"Next Action Step": na}), None, MON)
    check(f"'{na}' -> new card now", d and d["kind"] == "assigned" and d["owner"] == na, d)
d = rc.decide(row(_tab="W/C 16 Nov 2026", **{"Next Action Step": "Marty to call",
                                             "Clinical / Non-Clinical": "clinical"}), None, MON)
check("review decision overrides the clinical flag", d and d["owner"] == "Marty to call")
st = {"Owner": "Sinead Rocks to call", "Attempts": "0", "Slack TS": "1"}
check("same owner, card already out -> nothing new today",
      rc.decide(row(**{"Next Action Step": "Sinead Rocks to call"}), {**st, "Attempts": "1",
                "Last Attempt At": "2026-11-23", "Outcome": "no answer"}, MON) is None)
d = rc.decide(row(**{"Next Action Step": "Sinead Rocks to call"}),
              {**st, "Attempts": "1", "Last Attempt At": "2026-11-23", "Outcome": "no answer"}, TUE)
check("owner's no answer -> retry next day", d and d["kind"] == "retry", d)
d = rc.decide(row(**{"Next Action Step": "Marty to call"}),
              {"Owner": "Sinead Rocks to call", "Attempts": "2", "Outcome": "no answer"}, TUE)
check("decision changed to Marty -> fresh card", d and d["kind"] == "assigned" and d["owner"] == "Marty to call")
check("Leave -> finished", rc.decide(row(**{"Next Action Step": "Leave"}), None, MON) is None)
check("Rebooked -> finished", rc.decide(row(**{"Next Action Step": "Rebooked"}), None, MON) is None)
d = rc.decide(row(**{"Next Action Step": rc.NA_TRICKY}), None, MON)
check("tricky form -> one DM", d and d["kind"] == "tricky")
check("tricky form sent -> not again",
      rc.decide(row(**{"Next Action Step": rc.NA_TRICKY}), {"Owner": rc.NA_TRICKY}, TUE) is None)
check("needs review: blank + open", rc.needs_review(row()))
check("needs review: not if decided", not rc.needs_review(row(**{"Next Action Step": "Leave"})))
check("needs review: not if rebooked", not rc.needs_review(row(**{"Reactivation Status": "reactivated"})))

# --- dated notes -----------------------------------------------------------
P = rc.parse_note_date
check("'contact patient tuesday evening' (Mon) -> Tue", P("contact patient tuesday evening", MON) == TUE)
check("'w/b 30th nov' -> Mon 30 Nov", P("w/b 30th nov", MON) == date(2026, 11, 30))
check("'WB 30/11' -> Mon 30 Nov", P("WB 30/11", MON) == date(2026, 11, 30))
check("'week commencing 2nd Dec' -> its Monday", P("week commencing 2nd Dec", MON) == date(2026, 11, 30))
check("'ring 2/12' -> 2 Dec", P("ring 2/12", MON) == date(2026, 12, 2))
check("'call back on the 4th December' -> 4 Dec", P("call back on the 4th December", MON) == date(2026, 12, 4))
check("'call Dec 4' -> 4 Dec", P("call Dec 4", MON) == date(2026, 12, 4))
check("'try again tomorrow' -> Tue", P("try again tomorrow", MON) == TUE)
check("'ring next week' -> next Monday", P("ring next week", MON) == date(2026, 11, 30))
check("'call in 2 weeks' -> +14", P("call in 2 weeks", MON) == date(2026, 12, 7))
check("'contact friday' said on Friday -> next Friday",
      P("contact friday", date(2026, 11, 27)) == date(2026, 12, 4))
check("past date is a record, not a plan: 'rang 20/11 no answer'", P("rang 20/11 no answer", MON) is None)
check("date with no call word ignored: 'seen 30/11'", P("seen 30/11", MON) is None)
check("'call after christmas' not guessed", P("call after christmas", MON) is None)
check("…but flagged for the review", rc.looks_dated("call after christmas"))
check("plain note not flagged", not rc.looks_dated("knee much better, happy with exercises"))
check("January date in December rolls to next year",
      P("ring 5/1", date(2026, 12, 14)) == date(2027, 1, 5))
check("bot's own lines ignored", rc.note_segments("24/11 Kerry: no answer (call 1) | ring thursday")
      == ["ring thursday"])
check("Marty's comments read too",
      rc.note_segments("", "Contact w/b 30th Nov") == ["Contact w/b 30th Nov"])
check("same text = same hash", rc.seg_hash("Ring  Thursday") == rc.seg_hash("ring thursday"))

d = rc.decide(row(_tab="W/C 16 Nov 2026"), {"Scheduled For": "2026-11-24",
                                            "Scheduled Note": "ring tuesday"}, TUE)
check("scheduled note brings an old row back that day",
      d and d["kind"] == "scheduled" and d["owner"] == "Reception to call", d)
check("scheduled for later -> nothing before then",
      rc.decide(row(), {"Scheduled For": "2026-11-30"}, MON) is None)
d = rc.decide(row(**{"Next Action Step": "Sinead Rocks to call"}),
              {"Scheduled For": "2026-11-24", "Owner": "Sinead Rocks to call"}, TUE)
check("scheduled call goes to the current owner", d and d["owner"] == "Sinead Rocks to call")
check("scheduled card only once",
      rc.decide(row(_tab="W/C 16 Nov 2026"), {"Scheduled For": "2026-11-24",
                "Sched Posted": "2026-11-24"}, WED) is None)

# --- taps ------------------------------------------------------------------
cells, status, na, note = rc.apply_tap("noanswer", {"Attempts": "0"}, MON, "Kerry")
check("no answer -> contact_attempted, dated note", status == "contact_attempted"
      and note == "23/11 Kerry: no answer (call 1)" and na == "", (status, note))
cells, status, na, note = rc.apply_tap("rebooked", {"Attempts": "0"}, MON, "Megan")
check("rebooked -> Next Action 'Rebooked', status left to Cliniko", na == "Rebooked" and status == "")
cells, status, na, note = rc.apply_tap("notinterested", {"Attempts": "1"}, MON, "Megan")
check("not interested -> Leave + leave", na == "Leave" and status == "leave")
cells, status, na, note = rc.apply_tap("callback", {"Attempts": "0"}, date(2026, 11, 27), "Megan")
check("call back next week", cells["Next Due"] == "2026-12-04")
d = rc.decide(row(_tab="W/C 16 Nov 2026"), {**cells, "Owner": rc.WEEK_OWNER, "Slack TS": "1"},
              date(2026, 12, 4))
check("call back comes round on the day, even after the week ends", d and d["kind"] == "retry", d)

# --- text ------------------------------------------------------------------
txt = rc.card_text(row(**{"Marty's Comments": "worth a call"}), "07700900123", 1,
                   rc.card_head("assigned", "Marty to call", row(), None))
check("card head names the review decision + Marty",
      "Marty to call" in txt and f"<@{config.REACT_MARTY_USER_ID}>" in txt and "worth a call" in txt, txt)
check("call x of 2", "Call 1 of 2" in txt)
check("win = name + physio", rc.win_text(row(), "<@U1>") == "Sarah Quinn <@U1>")
check("win IA", rc.win_text(row(**{"Drop-off Type": "iadna"}), "<@U1>") == "Sarah Quinn IA")
n2 = rc.tap_note("noanswer", {"Attempts": "2"}, "U1", datetime(2026, 11, 24, 10, 30))
check("2nd no answer -> goes to Monday's review", "Monday's review" in n2, n2)

L = rc.LONDON
dt = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=L)
check("review prompt Monday 08:30", rc.review_due_now(dt("2026-11-23 08:31"), None))
check("no review prompt on Tuesday", not rc.review_due_now(dt("2026-11-24 08:31"), None))
check("list at 10:00", rc.list_due_now(dt("2026-11-23 10:02"), None))
check("post window closed at night", not rc.in_post_window(dt("2026-11-23 21:00")))
check("post window open mid-morning", rc.in_post_window(dt("2026-11-23 11:00")))

# --- 23 Nov fixes: notes already on the sheet, more phrases, untapped cards --
FRI, NEXTMON = date(2026, 11, 27), date(2026, 11, 30)
check("'check in with sean at end of week' (Mon) -> Fri", P("check in with sean at end of week", MON) == FRI)
check("'call later in week' (Mon) -> Thu", P("call later in week", MON) == date(2026, 11, 26))
check("'f/up by end of next week' -> Fri next week", P("f/up by end of next week", MON) == date(2026, 12, 4))
check("'he's working away for a week' -> +7", P("reactivate - he's working away for a week", MON) == NEXTMON)
check("'18/11/26 - SR check in 2/12 how NHS physio going' -> 2 Dec",
      P("18/11/26 - SR check in 2/12  how NHS physio going", MON) == date(2026, 12, 2))
check("leading date is when it was written: '19/11 - ring tomorrow' -> 20/11 (past)",
      P("19/11 - ring tomorrow", MON) == date(2026, 11, 20))
check("'19/11- awaiting Xray' -> nothing", P("19/11- Sean awaiting Xray before being able to attend", MON) is None)
check("'ring next month' -> 1 Dec", P("ring next month", MON) == date(2026, 12, 1))
d, note = rc.note_hold(["call later in week", "f/up by end of next week"], MON)
check("hold = earliest future date", d == date(2026, 11, 26) and note == "call later in week", (d, note))
check("no dated note -> no hold", rc.note_hold(["reactivate"], MON) == (None, ""))
st_today = {"Owner": "Reception to call", "Attempts": "0", "Slack TS": "1", "Posted At": "2026-11-23 09:04:16"}
check("card posted this morning, untapped -> not a 'try again'",
      rc.decide(row(**{"Next Action Step": "Reception to call"}), st_today, MON) is None)
d = rc.decide(row(**{"Next Action Step": "Reception to call"}), st_today, TUE)
check("still untapped next day -> on the list again", d and d["kind"] == "retry", d)
check("held card waits for its day",
      rc.decide(row(**{"Next Action Step": "Reception to call"}),
                {**st_today, "Scheduled For": "2026-11-27"}, TUE) is None)
d = rc.decide(row(**{"Next Action Step": "Reception to call"}), {**st_today, "Scheduled For": "2026-11-27"}, FRI)
check("held card comes back on the day as a new card", d and d["kind"] == "scheduled" and d["new_card"], d)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
