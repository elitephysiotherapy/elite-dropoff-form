"""Synthetic tests — the 30-day email must skip injection patients, but ONLY
once the injection flow that replaces it is switched on.

The gap this guards against: suppressing the generic 30-day before the Day 28
injection check-in exists would leave injection patients with no contact at all.
"""
from dotenv import load_dotenv
load_dotenv(override=True)

import config
from marketing import lifecycle

INJ = "1192928323588592985"   # 1. Injection Therapy — suppressed
REV = "382563815511823515"    # 2. Review Appointment — not suppressed


def appt(aid, typ):
    return {"id": aid,
            "appointment_type": {"links": {"self": f"/appointment_types/{typ}"}},
            "patient": {"links": {"self": f"/patients/{aid}"}},
            "starts_at": "2026-07-25T09:00:00Z",
            "cancelled_at": None, "did_not_arrive": False}


def thirty_day_flows(candidates, injection_enabled):
    """Run _thirty_day against fixed candidates, with the API and the NPS
    score lookup stubbed out."""
    orig_cand = lifecycle._lapsed_candidates
    orig_score = lifecycle.results.recent_score
    orig_flag = config.MARKETING_INJECTION_ENABLED
    lifecycle._lapsed_candidates = lambda days: candidates
    lifecycle.results.recent_score = lambda pid: None
    config.MARKETING_INJECTION_ENABLED = injection_enabled
    try:
        out = []
        lifecycle._thirty_day(out)
        return sorted(t.patient_id for t in out)
    finally:
        lifecycle._lapsed_candidates = orig_cand
        lifecycle.results.recent_score = orig_score
        config.MARKETING_INJECTION_ENABLED = orig_flag


T = []
def check(name, got, want):
    T.append((name, got, want, got == want))


INJ_ONLY = {"1": appt("1", INJ)}
MIXED = {"1": appt("1", INJ), "2": appt("2", REV)}

# 1. Flag OFF (today) — nothing changes, injection patients still get the email.
check("flag off: injection patient still emailed",
      thirty_day_flows(INJ_ONLY, False), ["1"])

# 2. Flag ON — injection patient suppressed (Day 28 check-in covers them).
check("flag on: injection patient suppressed",
      thirty_day_flows(INJ_ONLY, True), [])

# 3. Flag ON — a non-injection patient is untouched by the suppression.
check("flag on: review patient still emailed",
      thirty_day_flows(MIXED, True), ["2"])

# 4. Flag OFF — both still emailed.
check("flag off: both still emailed",
      thirty_day_flows(MIXED, False), ["1", "2"])

print("RESULT  test")
print("-" * 60)
allpass = True
for name, got, want, ok in T:
    allpass &= ok
    print(f"{'PASS' if ok else 'FAIL':5}   {name}   (got {got}, want {want})")
print("-" * 60)
print("ALL PASS" if allpass else "SOME FAILED")
