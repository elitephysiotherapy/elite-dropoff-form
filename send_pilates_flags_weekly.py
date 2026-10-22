"""Weekly Pilates + Profiling referrals → Slack DMs (Friday mornings).

The physios' note templates carry "Patient suitable for Pilates?" and
"Patient suitable for Profiling?" questions. Every Friday this scans the
treatment notes written in the previous 7 days (Fri 00:00 → Fri 00:00) and DMs
every patient a physio marked as suitable:
  Pilates   → Kerry Lennon
  Profiling → Sinéad Rocks

Pure STRUCTURED-FIELD scan, like progress_scan.py — only the patient's name,
the Matwork/Reformer choice (Pilates), the physio and the note date go into the
DM. No clinical free text leaves Cliniko.

Two answer styles exist in the templates (seen Oct 2026):
  radiobuttons  "Yes Matwork" / "Yes Reformer" / "No"   (the selected option)
                "Yes" / "No"                            (Profiling)
  paragraph     "Yes - Matwork: [X]<br>Yes - Reformer: []<br>No"   (ticked box)
                "Yes: [x]<br>No: []"                    (Profiling)

A patient flagged in more than one note that week appears once (latest note,
Matwork/Reformer choices merged).

Modes:
  python send_pilates_flags_weekly.py            preview only — prints both DMs
  python send_pilates_flags_weekly.py --post     send to Kerry and Sinéad

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DMs are rerouted to the CEO.
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
SINEAD_EMAIL = "sinead@elitephysiocookstown.co.uk"     # Sinéad Rocks (Ops Manager)

# One DM per flag: (key, question substring, recipient email, recipient first name, title)
FLAGS = [
    ("pilates", "suitable for pilates", KERRY_EMAIL, "Kerry", "Pilates"),
    ("profiling", "suitable for profiling", SINEAD_EMAIL, "Sinéad", "Profiling"),
]

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


def flag_choice(note, question):
    """Return None if the note doesn't answer `question` (a lowercase name
    substring) with a yes, else a set of {'Matwork', 'Reformer'} (empty set =
    'yes', type not specified — always the case for Profiling)."""
    for sec in (note.get("content") or {}).get("sections") or []:
        for q in sec.get("questions") or []:
            if question not in (q.get("name") or "").lower():
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
    """Flagged patients for the window: {flag key: list of dicts, one per patient}."""
    s = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    e = end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = phase2.fetch_all("/treatment_notes", [
        ("q[]", f"created_at:>={s}"), ("q[]", f"created_at:<{e}")])

    by_flag = {key: {} for key, *_ in FLAGS}
    for note in notes:
        if note.get("deleted_at") or note.get("archived_at"):
            continue
        for key, question, *_ in FLAGS:
            kinds = flag_choice(note, question)
            if kinds is None:
                continue
            by_patient = by_flag[key]
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
    return {key: sorted(bp.values(), key=lambda r: (r["date"], r["patient"]))
            for key, bp in by_flag.items()}


def build_dm_text(rows, start_local, end_local, *, name="Kerry", title="Pilates"):
    span = (f"{start_local.strftime('%a %d %b')} – "
            f"{(end_local - timedelta(days=1)).strftime('%a %d %b')}")
    lines = [
        f"Good morning {name},",
        "",
        f"*Patients flagged as suitable for {title}*  ({span})",
        "",
    ]
    if not rows:
        lines.append(f"No patients were flagged for {title} in the notes this week.")
        return "\n".join(lines)
    lines.append(f"{len(rows)} patient{'s' if len(rows) != 1 else ''} flagged:")
    lines.append("")
    for r in rows:
        physio = config.PRACTITIONER_DISPLAY_NAME.get(r["physio"], r["physio"])
        date = datetime.strptime(r["date"], "%Y-%m-%d").strftime("%a %d %b")
        # Pilates names Matwork/Reformer; Profiling has no type to show.
        kind = " + ".join(sorted(r["kinds"])) or ("type not specified"
                                                 if title == "Pilates" else "")
        kind = f" — {kind}" if kind else ""
        lines.append(f"• *{r['patient']}*{kind}  _(flagged by {physio}, {date})_")
    lines.append("")
    lines.append(f"_From the \"Patient suitable for {title}?\" question in this week's "
                 "treatment notes._")
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv
    start, end = previous_week_window()
    print(f"Scanning notes {start:%Y-%m-%d} → {end:%Y-%m-%d} for Pilates/Profiling "
          "flags…", flush=True)
    by_flag = scan(start, end)

    failed = False
    for key, _q, email, name, title in FLAGS:
        rows = by_flag[key]
        text = build_dm_text(rows, start, end, name=name, title=title)
        if not post:
            print()
            print(f"--- {title} flags weekly → {name} ({email}) ---")
            print(text)
            continue
        # Cloud logs: counts only, no patient names.
        print(f"{title}: {len(rows)} patient(s) flagged.")
        ok = slack_notifier._send_dm(
            email, text,
            target_label=f"Weekly {title} flags ({start:%d %b}–{end:%d %b %Y})",
        )
        print("  Sent." if ok else "  FAILED to send.")
        failed |= not ok

    if not post:
        print()
        print("(Preview only — re-run with --post to send to Kerry and Sinéad.)")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
