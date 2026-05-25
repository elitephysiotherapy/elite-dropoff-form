"""Reads NPS survey results from the 'NPS - Raw Data' tab.

Used to (a) suppress the survey nurture for patients who already responded and
(b) pick the promoter vs passive variant of the 30-day follow-up.

Raw Data columns: A Date Sent  B Patient ID  C Name  D Physio  E Clinic
F Trigger  G Score  H Category  I Open Text  J Callback Req  K Callback No
L Status  M Date Responded
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from marketing.sheets import tab

LONDON = ZoneInfo("Europe/London")
_RAW = "NPS - Raw Data"
_rows_cache = None   # per-process cache (each poll is a fresh process)


def _rows():
    global _rows_cache
    if _rows_cache is None:
        try:
            _rows_cache = tab(_RAW).get_all_values()[1:]
        except Exception:
            _rows_cache = []
    return _rows_cache


def _parse(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime((s or "").strip(), fmt).replace(tzinfo=LONDON)
        except (ValueError, AttributeError):
            continue
    return None


def recent_responder_ids(days=3):
    """Patient IDs who returned a scored survey within the last `days`."""
    cutoff = datetime.now(LONDON) - timedelta(days=days)
    out = set()
    for r in _rows():
        if len(r) < 13 or not (r[6] or "").strip():
            continue
        d = _parse(r[12]) or _parse(r[0])
        if d is None or d >= cutoff:
            out.add((r[1] or "").strip())
    return out


def recent_score(patient_id):
    """The patient's most recent NPS score (int 0-10), or None."""
    pid = str(patient_id)
    best_dt, best_score = None, None
    for r in _rows():
        if len(r) < 13 or (r[1] or "").strip() != pid:
            continue
        raw = (r[6] or "").strip()
        if not raw:
            continue
        try:
            score = int(float(raw))
        except ValueError:
            continue
        d = _parse(r[12]) or _parse(r[0])
        if best_dt is None or (d and (best_dt is None or d >= best_dt)):
            best_dt, best_score = d, score
    return best_score
