"""Marty's Club Consultations for a year — named follow-up check, as a Word doc.

Run it yourself (it reads patient names from Cliniko, so it is not run by Claude):
    ~/cliniko-dropoffs/venv/bin/python3 ~/cliniko-dropoffs/club_consult_report.py

Writes ~/Downloads/Club_Consultations_<year>_Marty.docx. Upload it to Drive
yourself if you want it there. It prints only totals, never names.

Follow-up = the patient's next attended 1:1 appointment of any type (classes
excluded) within 90 days of the consult.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

import config
import phase2
from docx import Document

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
CLUB_CONSULT = "1396206071189608060"
MARTY = {"382563813490168854", "1521620301232739358"}
LONDON = ZoneInfo("Europe/London")
EXCL = set(config.EXCLUDED_FROM_TOTAL_APPTS)
GROUPS = ["Follow-up within 90 days", "One-off (not seen again)",
          "Came back more than 90 days later", "Too recent to judge (under 3 weeks)"]


def P(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def live(a):
    return not (a.get("cancelled_at") or a.get("deleted_at") or a.get("did_not_arrive"))


def main():
    now = datetime.now(timezone.utc)
    print(f"Pulling {YEAR} Club Consultations from Cliniko…", flush=True)
    types = {str(t["id"]): t["name"] for t in phase2.fetch_all("/appointment_types")}
    consults = sorted(
        (a for a in phase2.fetch_all("/individual_appointments",
                                     [("q[]", f"appointment_type_id:={CLUB_CONSULT}")])
         if live(a) and phase2.id_from_link(a.get("practitioner")) in MARTY
         and P(a["starts_at"]) <= now and P(a["starts_at"]).astimezone(LONDON).year == YEAR),
        key=lambda a: a["starts_at"])

    hist, names = {}, {}
    rows = {g: [] for g in GROUPS}
    for a in consults:
        s = P(a["starts_at"])
        pid = phase2.id_from_link(a.get("patient"))
        if pid not in hist:
            hist[pid] = list(phase2.fetch_all(f"/patients/{pid}/appointments", []))
            p = next(iter(phase2.fetch_all("/patients", [("q[]", f"id:={pid}")])), {})
            names[pid] = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or f"Patient {pid}"
        later = sorted((h for h in hist[pid] if h.get("starts_at") and P(h["starts_at"]) > s
                        and live(h) and phase2.id_from_link(h.get("appointment_type")) not in EXCL),
                       key=lambda h: h["starts_at"])
        nxt = later[0] if later else None
        if nxt and P(nxt["starts_at"]) - s <= timedelta(days=90):
            g = GROUPS[0]
        elif now - s < timedelta(days=21):
            g = GROUPS[3]
        elif nxt:
            g = GROUPS[2]
        else:
            g = GROUPS[1]
        nxt_txt = ""
        if nxt:
            nd = P(nxt["starts_at"])
            nxt_txt = (f"{types.get(phase2.id_from_link(nxt.get('appointment_type')), 'archived type')}"
                       f" — {nd.astimezone(LONDON):%d %b %Y} ({(nd - s).days} days later)")
        rows[g].append((f"{s.astimezone(LONDON):%d %b %Y}", names[pid], nxt_txt))

    doc = Document()
    doc.add_heading(f"Marty — Club Consultations {YEAR}: follow-up check", 1)
    doc.add_paragraph(f"Generated {datetime.now(LONDON):%d %b %Y} from Cliniko. "
                      f"{len(consults)} attended Club Consultations in {YEAR} to date. "
                      "Follow-up = next attended 1:1 appointment of any type (classes excluded) "
                      "within 90 days. Contains patient data — keep private.")
    for g in GROUPS:
        doc.add_heading(f"{g} ({len(rows[g])})", 2)
        if not rows[g]:
            continue
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        for c, h in zip(t.rows[0].cells, ("Consult date", "Patient", "Next appointment")):
            c.text = h
        for r in rows[g]:
            for c, v in zip(t.add_row().cells, r):
                c.text = v

    out = os.path.expanduser(f"~/Downloads/Club_Consultations_{YEAR}_Marty.docx")
    doc.save(out)
    print(f"Done: {len(consults)} consults — " + ", ".join(f"{g}: {len(rows[g])}" for g in GROUPS))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
