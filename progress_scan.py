"""Weekly off-track / progress review.

Each Monday this scans the previous week's follow-up treatment notes and flags
patients whose physio recorded them as off-track or not improving — replacing
Martin's manual download-and-read pass.

It is a pure STRUCTURED-FIELD scan — no AI, no clinical free-text leaves Cliniko.
Two radiobutton questions in the "Follow Up Consultation 2026" note drive it:

  "Patient Progress Since Last Session"  -> flag if "No/Minimal Improvement"
                                            or "Patient Regressing"
  "Patient on track to achieve end goal" -> flag if "Not on Track"

Output: an "Off-Track Review" tab in the drop-off master sheet.

Usage:
  python progress_scan.py                 # last completed Mon-Sun week
  python progress_scan.py --week 2026-05-11   # the week starting that Monday
  python progress_scan.py --week 2026-05-11 --no-write   # preview, no sheet write
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import phase2
import phase1_fetch as master
import config

LONDON = ZoneInfo("Europe/London")

PROGRESS_Q = "Patient Progress Since Last Session"
ONTRACK_Q = "on track to achieve end goal"

TAB = "Off-Track Review"
HEADERS = ["Physio", "Patient", "Note Date", "Flag(s)",
           "Progress Rating", "On-Track Status"]


def week_window(monday=None):
    """(start_utc, end_utc, label) for a Mon-Sun week. Defaults to the most
    recently completed week."""
    if monday:
        y, m, d = (int(x) for x in monday.split("-"))
        start = datetime(y, m, d, tzinfo=LONDON)
    else:
        today = datetime.now(LONDON).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=today.weekday() + 7)   # Monday of last week
    end = start + timedelta(days=7)
    label = f"W/C {start.strftime('%d %b %Y')}"
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


def _clean(html):
    """Strip HTML tags / collapse whitespace from a treatment-note answer."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).strip()


def answer_for(note, name_substring):
    """Clean answer text for the first question whose name contains the substring.

    Handles both note styles seen in the data: free-text / paragraph answers
    (stored in 'answer'), and radiobutton / checkbox answers (the selected
    option(s) in 'answers', each carrying "selected": true)."""
    sub = name_substring.lower()
    for sec in (note.get("content") or {}).get("sections") or []:
        for q in sec.get("questions") or []:
            if sub in (q.get("name") or "").lower():
                if q.get("answer"):
                    return _clean(q.get("answer"))
                selected = [o.get("value", "") for o in (q.get("answers") or [])
                            if isinstance(o, dict) and o.get("selected")]
                return _clean("; ".join(selected))
    return ""


def evaluate_flags(progress, on_track):
    """Apply Martin's flag terms to the progress (C) and on-track (D) answers.

    Precise matching — avoids the two false-positive traps in the real data:
      - "off track" appears in the checkbox LABEL "On Track: [x] Off Track: [ ]"
        on almost every note — so a bare substring match is useless. We only
        flag off-track when the OFF-track box is ticked, or when "off track"
        appears with no "on track" anywhere (a plain "Off Track").
      - "regress" matches "no regression" (the physio saying there is none).
        We match the gerund "regressing" only — the genuine flag wording.
    """
    p, d = progress.lower(), on_track.lower()
    flags = []
    if "not on track" in d:
        flags.append("Not on track")
    elif re.search(r"off track\s*:?\s*[\[\(]\s*x", d):
        flags.append("Off track (x)")
    elif "off track" in d and "on track" not in d:
        flags.append("Off track")
    if "no/minimal improvement" in p:
        flags.append("No/minimal improvement")
    if "regressing" in p:
        flags.append("Regressing")
    return flags


def is_followup_note(note):
    return bool(answer_for(note, PROGRESS_Q)) or any(
        PROGRESS_Q.lower() in (q.get("name") or "").lower()
        for sec in (note.get("content") or {}).get("sections") or []
        for q in sec.get("questions") or [])


_patient_cache = {}


def patient_name(note):
    pid = phase2.id_from_link(note.get("patient"))
    if not pid:
        return "?"
    if pid not in _patient_cache:
        try:
            r = phase2.SESSION.get(f"{phase2.BASE}/patients/{pid}", timeout=30)
            p = r.json() if r.status_code == 200 else {}
            _patient_cache[pid] = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "?"
        except Exception:
            _patient_cache[pid] = "?"
    return _patient_cache[pid]


def scan(start_utc, end_utc):
    """Return (flagged_rows, total_followup_notes)."""
    s = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = list(phase2.fetch_all("/treatment_notes", [
        ("q[]", f"created_at:>={s}"), ("q[]", f"created_at:<{e}")]))

    flagged = []
    n_followup = 0
    for note in notes:
        if note.get("deleted_at") or note.get("archived_at"):
            continue
        if not is_followup_note(note):
            continue
        n_followup += 1
        progress = answer_for(note, PROGRESS_Q)
        on_track = answer_for(note, ONTRACK_Q)
        flags = evaluate_flags(progress, on_track)
        if not flags:
            continue
        when = (note.get("finalized_at") or note.get("created_at") or "")[:10]
        flagged.append({
            "physio": note.get("author_name", "?"),
            "patient": patient_name(note),
            "date": when,
            "flags": "; ".join(flags),
            "progress": progress or "(blank)",
            "on_track": on_track or "(blank)",
        })
    return flagged, n_followup


def write_tab(flagged, label, n_followup):
    sh = master.open_spreadsheet()
    out = [
        [f"Off-Track Review — {label}"],
        [f"Scanned {n_followup} follow-up notes  |  {len(flagged)} flagged  |  "
         f"updated {datetime.now(LONDON).strftime('%Y-%m-%d %H:%M')}"],
        [],
        HEADERS,
    ]
    order = {d: i for i, d in enumerate(config.PRACTITIONER_DISPLAY_ORDER)}

    def sort_key(r):
        disp = config.PRACTITIONER_DISPLAY_NAME.get(r["physio"], r["physio"])
        return (order.get(disp, 99), r["date"], r["patient"])

    for r in sorted(flagged, key=sort_key):
        out.append([r["physio"], r["patient"], r["date"], r["flags"],
                    r["progress"], r["on_track"]])
    try:
        ws = sh.worksheet(TAB)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(title=TAB, rows=300, cols=len(HEADERS))
    ws.update(values=out, range_name="A1", value_input_option="RAW")


def print_summary(flagged, n_followup, label):
    """Per-physio counts only — no patient names to the console."""
    print(f"\n{label}: {n_followup} follow-up notes scanned, "
          f"{len(flagged)} flagged.")
    by_phys = {}
    for r in flagged:
        disp = config.PRACTITIONER_DISPLAY_NAME.get(r["physio"], r["physio"])
        by_phys[disp] = by_phys.get(disp, 0) + 1
    for disp in sorted(by_phys, key=lambda d: -by_phys[d]):
        print(f"  {disp}: {by_phys[disp]} flagged")


def main():
    monday = None
    for i, a in enumerate(sys.argv):
        if a == "--week" and i + 1 < len(sys.argv):
            monday = sys.argv[i + 1]
    write = "--no-write" not in sys.argv

    start, end, label = week_window(monday)
    print(f"Scanning follow-up notes for {label} (Mon-Sun)…")
    flagged, n_followup = scan(start, end)
    print_summary(flagged, n_followup, label)
    if write:
        write_tab(flagged, label, n_followup)
        print(f"\nWritten to the '{TAB}' tab of the drop-off master sheet.")
    else:
        print("\n(Preview only — re-run without --no-write to update the sheet.)")


if __name__ == "__main__":
    main()
