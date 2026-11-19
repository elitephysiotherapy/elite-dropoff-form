"""Pre-assessment form chase + welcome-email form link (Martin, 19 Nov 2026).

Run: ./venv/bin/python test_form_chase.py
No network: every Cliniko call is stubbed.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from marketing import cliniko, common, forms, nps, preforms, templates  # noqa: E402

failures = 0


def check(label, cond):
    global failures
    print(("PASS  " if cond else "FAIL  ") + label)
    failures += 0 if cond else 1


UTC = timezone.utc
import config  # noqa: E402
IA = sorted(config.FORM_CHASE_TYPE_IDS)[0]
NON_IA = "999999999999999999"


def link(kind, _id):
    return {"links": {"self": f"https://api.uk1.cliniko.com/v1/{kind}/{_id}"}}


def appt(booked, starts, type_id=IA, cancelled=False, aid="111111111111111111"):
    return {"id": aid, "created_at": booked.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "starts_at": starts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "appointment_type": link("appointment_types", type_id),
            "patient": link("patients", "222222222222222222"),
            "cancelled_at": "2026-11-20T00:00:00Z" if cancelled else None}


def d(day, hour, minute=0):  # Nov 2026 = UTC == London
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


# Visit Fri 20 Nov 10:00, booked Tue 17 Nov 10:00.
VISIT, BOOKED = d(20, 10), d(17, 10)
A = appt(BOOKED, VISIT)

# ---- timing of chase 1 (about 5 h after booking, 9:00-20:00 only) ----
f = lambda a, now, pending=True: preforms.chase_flow_for(a, pending, now)
check("chase 1 due 5.5 h after booking", f(A, d(17, 15, 30)) == "form_chase_1")
check("no chase at 4 h (too soon)", f(A, d(17, 14)) is None)
check("no chase at 21:30 (outside 9-20)", f(A, d(17, 21, 30)) is None)
check("no chase at 08:30", f(A, d(18, 8, 30)) is None)
check("chase 1 still due next morning 10:00 (age 24 h)", f(A, d(18, 10)) == "form_chase_1")
check("no chase 1 after 31 h (old booking = no backlog blast)", f(A, d(18, 18)) is None)

# ---- chase 2: evening before ----
check("chase 2 at 18:00 the evening before", f(A, d(19, 18)) == "form_chase_2")
check("no chase 2 at 16:00", f(A, d(19, 16)) is None)
check("no chase 2 at 20:30", f(A, d(19, 20, 30)) is None)
check("no chase 2 two days before", f(A, d(18, 18)) is None)
check("no chase on the morning of the visit", f(A, d(20, 8, 30)) is None)
short = appt(d(19, 9), VISIT)    # booked Thu 09:00 for Fri 10:00
check("short-notice booking gets chase 1, not chase 2, in the evening",
      f(short, d(19, 18)) == "form_chase_1")

# ---- gates ----
check("form done/expired/missing -> nothing", f(A, d(17, 15, 30), pending=False) is None)
check("cancelled appointment -> nothing",
      f(appt(BOOKED, VISIT, cancelled=True), d(17, 15, 30)) is None)
PROFILING = sorted(cliniko.IA_TYPE_IDS - config.FORM_CHASE_TYPE_IDS)[0]
check("profiling / other IA-list type (own form) -> nothing",
      f(appt(BOOKED, VISIT, type_id=PROFILING), d(17, 15, 30)) is None)
check("non-IA appointment -> nothing",
      f(appt(BOOKED, VISIT, type_id=NON_IA), d(17, 15, 30)) is None)
check("under 3 h before the visit -> nothing",
      f(appt(d(20, 1), d(20, 10)), d(20, 8)) is None)

# ---- forms.pending_url ----
now = d(18, 12)
form = {"url": "https://x.cliniko.com/f/?tok", "completed_at": None,
        "expires_at": "2026-11-25T08:00:00Z", "expired": False}
check("pending form returns its own link", forms.pending_url({"1": form}, "1", now) == form["url"])
check("completed form -> None",
      forms.pending_url({"1": dict(form, completed_at="2026-11-18T09:00:00Z")}, "1", now) is None)
check("expired flag -> None", forms.pending_url({"1": dict(form, expired=True)}, "1", now) is None)
check("past expires_at -> None",
      forms.pending_url({"1": dict(form, expires_at="2026-11-17T00:00:00Z")}, "1", now) is None)
check("no form for the booking -> None", forms.pending_url({}, "1", now) is None)
check("lookup failed (None) -> None", forms.pending_url(None, "1", now) is None)

# ---- collect(): fail-safe, dedup across both candidate lists, completed excluded ----
common.appt_ctx = lambda a: {"practitioner_name": "Marty"}
common.now_utc = lambda: d(17, 15, 30)
cliniko.appointments_created_between = lambda s, e: [A]
cliniko.appointments_started_between = lambda s, e: [A]
cliniko.is_class = lambda a: False

forms.by_appointment = lambda *a, **k: None
check("form lookup failure -> sends NOTHING", preforms.collect() == [])

forms.by_appointment = lambda *a, **k: {A["id"]: form}
got = preforms.collect()
check("pending form -> exactly one chase touch", len(got) == 1 and got[0].flow_name == "form_chase_1")
check("touch is SMS, anchored on the appointment, carries the form url",
      got[0].channel == "sms" and got[0].anchor == A["id"]
      and got[0].appt_ctx["_form_url"] == form["url"])
check("chase is not marketing (no consent gate)", got[0].is_marketing is False)
forms.by_appointment = lambda *a, **k: {A["id"]: dict(form, completed_at="2026-11-17T12:00:00Z")}
check("completed form -> no touch", preforms.collect() == [])
common.now_utc = lambda: d(17, 22)
forms.by_appointment = lambda *a, **k: {A["id"]: form}
check("outside the send window -> no touch", preforms.collect() == [])

# ---- welcome email carries the form paragraph only while undone ----
common.now_utc = lambda: d(17, 11, 30)
nps.cliniko.is_cancelled = lambda a: False
nps.cliniko.is_class = lambda a: False
nps.cliniko.is_initial_appointment = lambda a: True
nps.cliniko.appt_type_id = lambda a: IA
nps.cliniko.patient_id_of = lambda a: "P1"
nps.cliniko.is_new_patient = lambda pid, a: True
nps.common.hours_since = lambda ts: 2.0
cliniko.appointments_created_between = lambda s, e: [A]
forms.by_appointment = lambda *a, **k: {A["id"]: form}
w = nps.collect_welcome()
check("welcome gets the form paragraph with the patient's link",
      len(w) == 1 and form["url"] in w[0].appt_ctx["form_block"]
      and "reads your answers" in w[0].appt_ctx["form_block"])
forms.by_appointment = lambda *a, **k: {A["id"]: dict(form, completed_at="2026-11-17T12:00:00Z")}
check("welcome without the paragraph once form is done",
      nps.collect_welcome()[0].appt_ctx["form_block"] == "")
nps.cliniko.appt_type_id = lambda a: PROFILING
forms.by_appointment = lambda *a, **k: {A["id"]: form}
check("welcome for a profiling-type booking never gets the form paragraph",
      nps.collect_welcome()[0].appt_ctx["form_block"] == "")
nps.cliniko.appt_type_id = lambda a: IA
forms.by_appointment = lambda *a, **k: None
check("form lookup failed -> welcome still goes, without the paragraph",
      nps.collect_welcome()[0].appt_ctx["form_block"] == "")

# ---- rendering ----
ctx = {"first_name": "Sam", "practitioner_name": "Marty", "appointment_date": "Fri 20 Nov",
       "appointment_time": "10:00am", "form_link": "https://elite-sms-shortener.onrender.com/r/abc123"}
for tid in ("form_chase_1", "form_chase_2"):
    body = templates.render_sms(tid, ctx)
    check(f"{tid} renders, no stray placeholder, <= 2 SMS segments ({len(body)} chars)",
          "{" not in body and len(body) <= 306 and ctx["form_link"] in body)
with_block = dict(ctx, form_block=templates.FORM_BLOCK.format(form_url=form["url"]))
e1 = templates.render_email("welcome", with_block)["text"]
e2 = templates.render_email("welcome", dict(ctx, form_block=""))["text"]
check("welcome email includes link + physio-reads line", form["url"] in e1 and "reads your answers" in e1)
check("welcome email is unchanged apart from the block when form is done",
      "{form_block}" not in e2 and "pre-assessment form" not in e2
      and e1.replace(with_block["form_block"], "") == e2)

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
