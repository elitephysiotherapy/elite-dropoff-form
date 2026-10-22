"""Weekly Pilates referrals → Slack DM to Kerry (Friday mornings).

The physios' note templates carry a "Patient suitable for Pilates?" question.
Every Friday this scans the treatment notes written in the previous 7 days
(Fri 00:00 → Fri 00:00) and DMs Kerry Lennon every patient a physio marked as
suitable, so she can contact them about Pilates.

Pure STRUCTURED-FIELD scan, like progress_scan.py — only the patient's name,
the Matwork/Reformer choice, the physio and the note date go into the DM. No
clinical free text leaves Cliniko.

Two answer styles exist in the templates (seen Oct 2026):
  radiobuttons  "Yes Matwork" / "Yes Reformer" / "No"   (the selected option)
  paragraph     "Yes - Matwork: [X]<br>Yes - Reformer: []<br>No"   (ticked box)

A patient flagged in more than one note that week appears once (latest note,
Matwork/Reformer choices merged).

Modes:
  python send_pilates_flags_weekly.py            preview only — prints the DM
  python send_pilates_flags_weekly.py --post     send the DM to Kerry

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DM is rerouted to the CEO.
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import phase2
import slack_notifier
from progress_scan import patient_name

LONDON = ZoneInfo("Europe/London")
KERRY_EMAIL = "kerry@elitephysiocookstown.co.uk"

PILATES_Q = "suitable for pilates"

# Paragraph style: one option per line, ticked with [x] / [X].
_TICKED_RE = re.compile(r"^(.*?):?\s*\[\s*x\s*\]", re.I)


def previous_week_window(now=None):
    """(start_local, end_local) for the 7 days ending today 00:00 (Fri → Fri)."""
    if now is None:
        now = datetime.now(LONDON)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=7), end


def _lines(html):
    """Split a paragraph answer into clean lines (one checkbox option each)."""
    text = re.sub(r"<br\s*/?>|</p>", "\n", str(html or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text).replace("​", "")
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if ln.strip()]


def _kind(option):
    """'Matwork' / 'Reformer' / '' (unspecified yes) from an option label."""
    o = option.lower()
    if "mat" in o:
        return "Matwork"
    if "reformer" in o:
        return "Reformer"
    return ""


def pilates_choice(note):
    """Return None if the note doesn't flag Pilates, else a set of
    {'Matwork', 'Reformer'} (empty set = 'yes', type not specified)."""
    for sec in (note.get("content") or {}).get("sections") or []:
        for q in sec.get("questions") or []:
            if PILATES_Q not in (q.get("name") or "").lower():
                continue
            # Radiobuttons: the selected option(s).
            selected = [o.get("value", "") for o in (q.get("answers") or [])
                        if isinstance(o, dict) and o.get("selected")]
            # Paragraph: options whose box is ticked; a bare "yes" also counts.
            if not selected and q.get("answer"):
                lines = _lines(q.get("answer"))
                selected = [m.group(1) for ln in lines if (m := _TICKED_RE.match(ln))]
                has_boxes = any("[" in ln for ln in lines)
                if not selected and not has_boxes and lines[0].lower().startswith("yes"):
                    selected = [lines[0]]
            yes = [s for s in selected if s.strip().lower().startswith("yes")]
            if yes:
                return {k for k in map(_kind, yes) if k}
    return None


def scan(start_local, end_local):
    """Flagged patients for the window: list of dicts, one per patient."""
    s = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    e = end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = phase2.fetch_all("/treatment_notes", [
        ("q[]", f"created_at:>={s}"), ("q[]", f"created_at:<{e}")])

    by_patient = {}
    for note in notes:
        if note.get("deleted_at") or note.get("archived_at"):
            continue
        kinds = pilates_choice(note)
        if kinds is None:
            continue
        pid = phase2.id_from_link(note.get("patient"))
        when = (note.get("created_at") or "")[:10]
        prev = by_patient.get(pid)
        if prev:
            prev["kinds"] |= kinds
            if when >= prev["date"]:
                prev.update(date=when, physio=note.get("author_name", "?"))
            continue
        by_patient[pid] = {
            "patient": patient_name(note),
            "physio": note.get("author_name", "?"),
            "date": when,
            "kinds": set(kinds),
        }
    return sorted(by_patient.values(), key=lambda r: (r["date"], r["patient"]))


def build_dm_text(rows, start_local, end_local):
    span = (f"{start_local.strftime('%a %d %b')} – "
            f"{(end_local - timedelta(days=1)).strftime('%a %d %b')}")
    lines = [
        "Good morning Kerry,",
        "",
        f"*Patients flagged as suitable for Pilates*  ({span})",
        "",
    ]
    if not rows:
        lines.append("No patients were flagged for Pilates in the notes this week.")
        return "\n".join(lines)
    lines.append(f"{len(rows)} patient{'s' if len(rows) != 1 else ''} flagged:")
    lines.append("")
    for r in rows:
        kind = " + ".join(sorted(r["kinds"])) or "type not specified"
        physio = config.PRACTITIONER_DISPLAY_NAME.get(r["physio"], r["physio"])
        date = datetime.strptime(r["date"], "%Y-%m-%d").strftime("%a %d %b")
        lines.append(f"• *{r['patient']}* — {kind}  _(flagged by {physio}, {date})_")
    lines.append("")
    lines.append("_From the \"Patient suitable for Pilates?\" question in this week's "
                 "treatment notes._")
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv
    start, end = previous_week_window()
    print(f"Scanning notes {start:%Y-%m-%d} → {end:%Y-%m-%d} for Pilates flags…",
          flush=True)
    rows = scan(start, end)
    text = build_dm_text(rows, start, end)

    if not post:
        print()
        print(f"--- Pilates flags weekly → Kerry ({KERRY_EMAIL}) ---")
        print(text)
        print()
        print("(Preview only — re-run with --post to send to Kerry.)")
        return

    # Cloud logs: counts only, no patient names.
    print(f"{len(rows)} patient(s) flagged.")
    ok = slack_notifier._send_dm(
        KERRY_EMAIL, text,
        target_label=f"Weekly Pilates flags ({start:%d %b}–{end:%d %b %Y})",
    )
    print("Sent." if ok else "FAILED to send.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
