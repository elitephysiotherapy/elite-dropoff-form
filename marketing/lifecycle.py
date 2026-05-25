"""Lifecycle flows — 30-day follow-up, 90/180-day keep-in-touch, 12-month
reactivation, and birthday.

These are DAILY jobs, not every-10-minute ones. poller.py calls collect() only
inside the daily lifecycle window; dedup makes a double-run harmless anyway.

collect() -> list[Touch]
"""

from datetime import timedelta

import config
import phase2
from marketing import cliniko, common, results
from marketing.common import Touch

_LAPSE_BAND_DAYS = 7   # width of the "lapsed ~N days ago" detection window


def collect():
    touches = []
    _thirty_day(touches)
    _keep_in_touch(90, "keep_in_touch_90", "email", "keep_in_touch_90", touches)
    _keep_in_touch(180, "keep_in_touch_180", "sms", "keep_in_touch_180", touches)
    _keep_in_touch(365, "reactivation_12mo", "email", "reactivation_12mo", touches)
    if getattr(config, "MARKETING_BIRTHDAY_ENABLED", False):
        _birthday(touches)
    return touches


def _lapsed_candidates(days):
    """{patient_id: representative_appt} for patients whose last activity was a
    ~`days`-old appointment and who have done nothing since."""
    end = common.now_utc() - timedelta(days=days)
    start = end - timedelta(days=_LAPSE_BAND_DAYS)
    by_pat = {}
    for a in cliniko.appointments_started_between(start, end):
        if cliniko.is_class(a):
            continue
        pid = cliniko.patient_id_of(a)
        if not pid:
            continue
        cur = by_pat.get(pid)
        if cur is None or (a.get("starts_at") or "") > (cur.get("starts_at") or ""):
            by_pat[pid] = a
    after_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {pid: a for pid, a in by_pat.items()
            if not cliniko.has_future_booking(pid, after_iso)}


def _thirty_day(out):
    """Patients ~30 days past their last appointment, nothing since."""
    for pid, appt in _lapsed_candidates(30).items():
        aid = str(appt["id"])
        ctx = common.appt_ctx(appt)
        if cliniko.is_attended(appt):
            score = results.recent_score(pid)
            if score is not None and score >= 9:
                out.append(Touch("thirty_day_promoter", pid, "email",
                                  "thirty_day_promoter", aid, ctx))
            else:
                out.append(Touch("thirty_day_passive", pid, "email",
                                  "thirty_day_passive", aid, ctx))
        else:
            out.append(Touch("thirty_day_cna_dna", pid, "email",
                              "thirty_day_cna_dna", aid, ctx))


def _keep_in_touch(days, flow, channel, template, out):
    """90 / 180 / 365-day reactivation touches (marketing — consent-gated)."""
    for pid, appt in _lapsed_candidates(days).items():
        out.append(Touch(flow, pid, channel, template, str(appt["id"]),
                          common.appt_ctx(appt), is_marketing=True))


def _birthday(out):
    """Patients whose birthday is today. Off by default — scans the whole
    patient base, so enable only once the volume is understood."""
    today = common.now_utc().astimezone(common.LONDON)
    mmdd = today.strftime("%m-%d")
    year = today.strftime("%Y")
    clinic = config.CLINICS.get(config.DEFAULT_CLINIC, {})
    base_ctx = {
        "clinic_name": config.DEFAULT_CLINIC,
        "clinic_phone": clinic.get("phone", ""),
        "_clinic_key": config.DEFAULT_CLINIC,
    }
    for p in phase2.fetch_all("/patients"):
        if p.get("archived_at") or p.get("deleted_at"):
            continue
        dob = p.get("date_of_birth") or ""
        if len(dob) >= 10 and dob[5:10] == mmdd:
            out.append(Touch("birthday", str(p["id"]), "email", "birthday",
                              f"birthday-{year}", dict(base_ctx), is_marketing=True))
