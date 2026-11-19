"""Day-before IA call list (Martin, 20 Nov 2026).

Run: ./venv/bin/python test_day_before_calls.py
No network: nothing here calls Cliniko, Sheets or Slack.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import send_day_before_calls as dbc  # noqa: E402

failures = 0


def check(label, cond):
    global failures
    print(("PASS  " if cond else "FAIL  ") + label)
    failures += 0 if cond else 1


UTC = timezone.utc
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")

# ---- which day's list ----
check("Friday's list is Monday's", dbc.next_working_day(date(2026, 11, 20)) == date(2026, 11, 23))
check("Monday's list is Tuesday's", dbc.next_working_day(date(2026, 11, 23)) == date(2026, 11, 24))
check("Thursday's list is Friday's", dbc.next_working_day(date(2026, 11, 19)) == date(2026, 11, 20))
check("skips the Christmas closure (23 Dec -> next open day after 31 Dec)",
      dbc.next_working_day(date(2026, 12, 23)) == date(2027, 1, 1))
check("skips a bank holiday closure (Fri 28 Aug 2026 -> Tue 1 Sep, as Mon 31 Aug is closed)",
      dbc.next_working_day(date(2026, 8, 28)) == date(2026, 9, 1))
check("weekend is not a working day", not dbc.is_working_day(date(2026, 11, 21)))
check("closed day is not a working day", not dbc.is_working_day(date(2026, 12, 25)))

# ---- classification ----
APPT = {"id": "A0", "starts_at": iso(datetime(2026, 11, 23, 10, 0, tzinfo=UTC)),
        "created_at": iso(datetime(2026, 11, 18, 12, 0, tzinfo=UTC))}


def h(i, days_before, cancelled=False, dna=False):
    s = datetime(2026, 11, 23, 10, 0, tzinfo=UTC) - timedelta(days=days_before)
    return {"id": f"H{i}", "starts_at": iso(s),
            "cancelled_at": iso(s - timedelta(days=1)) if cancelled else None,
            "did_not_arrive": dna}


c = lambda hist, meta=(): dbc.classify(APPT, hist, list(meta), NOW)
LEAD_BEFORE = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LEAD_AFTER = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)

r, _ = c([])
check("no history at all -> brand new patient", r == [dbc.NEW_LABEL])
r, _ = c([h(1, 30)])
check("attended before, clean -> not on the list", r == [])
r, d = c([h(1, 30), h(2, 90, dna=True)])
check("previous patient with a no-show -> poor attender", r == [dbc.POOR_LABEL] and d["dna"] == 1)
r, d = c([h(1, 30), h(2, 90, cancelled=True), h(3, 200, cancelled=True)])
check("previous patient with 2 cancellations (2y) -> poor attender",
      r == [dbc.POOR_LABEL] and d["cna"] == 2)
r, _ = c([h(1, 30), h(2, 90, cancelled=True)])
check("ONE cancellation is not enough", r == [])
r, _ = c([h(1, 30), h(2, 800, cancelled=True), h(3, 900, cancelled=True)])
check("cancellations older than 2 years don't count", r == [])
r, _ = c([h(1, 30), h(2, 800, dna=True)])
check("a no-show older than 2 years doesn't count", r == [])
r, _ = c([h(1, 30)], meta=[LEAD_BEFORE])
check("Meta lead created before the booking -> Meta lead", r == [dbc.META_LABEL])
r, _ = c([h(1, 30)], meta=[LEAD_AFTER])
check("Meta lead created AFTER the booking is not this booking's source", r == [])
r, _ = c([], meta=[LEAD_BEFORE])
check("Meta + brand new shows both reasons", r == [dbc.META_LABEL, dbc.NEW_LABEL])
r, _ = c([h(1, 30, cancelled=True)])
check("cancelled-only history = never attended = brand new", r == [dbc.NEW_LABEL])
r, _ = c([{"id": "A0", "starts_at": APPT["starts_at"], "cancelled_at": None, "did_not_arrive": False}])
check("the appointment itself is ignored in the history", r == [dbc.NEW_LABEL])
r, _ = c([{"id": "F1", "starts_at": iso(datetime(2026, 12, 5, 10, tzinfo=UTC)),
           "cancelled_at": None, "did_not_arrive": False}])
check("a LATER appointment doesn't make them 'attended before'", r == [dbc.NEW_LABEL])

# ---- message ----
check("history note wording",
      dbc._history_note({"dna": 1, "cna": 2}) == "1 no-show + 2 cancellations in the last 2 years"
      and dbc._history_note({"dna": 0, "cna": 0}) == "")
tgt = date(2026, 11, 23)
rows = [{"starts": NOW, "time": "9:00am", "clinic": "Cookstown", "physio": "Marty",
         "name": "Test Patient", "phone": "07700900123",
         "reasons": [dbc.META_LABEL, dbc.NEW_LABEL], "note": "", "form": "form NOT done"},
        {"starts": NOW, "time": "2:30pm", "clinic": "Maghera", "physio": "Molaí",
         "name": "Other Person", "phone": "no mobile on file",
         "reasons": [dbc.POOR_LABEL], "note": "1 no-show in the last 2 years", "form": "form done"}]
m = dbc.build_message(tgt, rows)
check("message names the day and the count", "Monday 23 November: 2 to make" in m)
check("message shows each reason, the time, clinic, physio, phone",
      "Meta lead + Brand new patient" in m and "Previous poor attender" in m
      and "9:00am" in m and "Maghera" in m and "Molaí" in m and "07700900123" in m)
check("message shows the form status and the history note",
      "form NOT done" in m and "form done" in m and "1 no-show in the last 2 years" in m)
check("message does NOT tell reception to ask 'are you still coming'",
      "not to ask if they're still coming" in m)
check("message has no stray braces", "{" not in m and "}" not in m)
check("empty day -> short 'no calls needed' message",
      "No calls needed" in dbc.build_message(tgt, []))
check("meta-check failure is mentioned", "couldn't check Meta leads" in dbc.build_message(tgt, rows, meta_ok=False))

# ---- wiring ----
import run_cloud  # noqa: E402
check("run_cloud has the job", "day_before_calls" in run_cloud.COMMANDS)
mon9 = datetime(2026, 11, 23, 9, 3, tzinfo=run_cloud.LONDON)
sat9 = datetime(2026, 11, 21, 9, 3, tzinfo=run_cloud.LONDON)
check("job fires Mon-Fri 09:00 London, not at weekends or other hours",
      run_cloud.should_run("day_before_calls", mon9)
      and not run_cloud.should_run("day_before_calls", sat9)
      and not run_cloud.should_run("day_before_calls", mon9.replace(hour=10)))
check("reception gets it via the config list",
      config.DAY_BEFORE_CALLS_SLACK_EMAILS == ["reception@elitephysiocookstown.co.uk"])

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
