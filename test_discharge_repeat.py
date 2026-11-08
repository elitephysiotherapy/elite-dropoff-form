"""Discharge survey: at most once per patient per 90 days (Martin, 8 Nov 2026).

Patients who book one visit at a time look "discharged" after every visit
(nothing booked), so without this limit they got the survey every week.
Run: ./venv/bin/python test_discharge_repeat.py
"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from marketing import nps, sent_log, cliniko  # noqa: E402

failures = 0


def check(label, cond):
    global failures
    print(("PASS  " if cond else "FAIL  ") + label)
    failures += 0 if cond else 1


def ts(days_ago):
    return (sent_log._now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


cliniko.patient_id_of = lambda a: "P1"
cliniko.has_future_booking = lambda pid: False
cliniko.attended_appt_count = lambda pid: 10
nps.common.appt_ctx = lambda a: {}


def flows(ledger, aid="A2", hrs=30):
    sent_log._rows_cache = ledger
    out = []
    nps._discharge_touches({"id": aid}, hrs, out)
    return [t.flow_name for t in out]


row = lambda d, flow, anc="A1", st="sent": [ts(d), "P1", "X", flow, "sms", anc, "discharge_survey", st]

check("first discharge -> SMS + email", flows([]) == ["discharge_survey_sms", "discharge_survey_email"])
check("survey 7 days ago for an earlier visit -> nothing", flows([row(7, "discharge_survey_sms")]) == [])
check("survey 89 days ago -> nothing", flows([row(89, "discharge_survey_email")]) == [])
check("survey 91 days ago -> sent again", len(flows([row(91, "discharge_survey_sms")])) == 2)
check("same visit's SMS already sent -> its email still goes",
      "discharge_survey_email" in flows([row(0.1, "discharge_survey_sms", anc="A2")]))
check("earlier FAILED send doesn't block", len(flows([row(7, "discharge_survey_sms", st="failed: x")])) == 2)
check("other patients' surveys don't block",
      len(flows([[ts(3), "P9", "Y", "discharge_survey_sms", "sms", "A1", "", "sent"]])) == 2)
sent_log._rows_cache = sent_log._READ_FAILED
out = []; nps._discharge_touches({"id": "A2"}, 30, out)
check("ledger unreadable -> fail safe, nothing sent", out == [])

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
