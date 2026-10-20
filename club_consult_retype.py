"""Retype Marty's 2026 Club Consultations so "Club Consultation" = a true one-off
opinion only (Martin 2026-10-19).

Run it yourself — it reads patient names from Cliniko, so it is not run by Claude:
    ~/cliniko-dropoffs/venv/bin/python3 ~/cliniko-dropoffs/club_consult_retype.py           # PREVIEW (changes nothing)
    ~/cliniko-dropoffs/venv/bin/python3 ~/cliniko-dropoffs/club_consult_retype.py --apply   # make the changes
    ~/cliniko-dropoffs/venv/bin/python3 ~/cliniko-dropoffs/club_consult_retype.py --undo ~/Downloads/club_retype_backup_XXXX.csv

Rules
  A. Marty's 2026 Club Consultations whose patient had a follow-up (next attended
     1:1, any type, within 90 days): the first of that run of care -> Club IA.
     Every later Club Consultation in the same run (any physio; each visit within
     90 days of the previous one) -> Club Follow Up.
  B. Named patients (named_patients.txt, one per line, "First Last" or
     "First Last | YYYY-MM-DD" to start from that date): their first 2026
     appointment with Marty (on/after the date) -> Club IA; later appointments
     with Marty that are Club Consultation or a Review type -> Club Follow Up.
     Classes, ultrasound and other speciality types are left alone.
     Named patients follow rule B only.
  C. Marty's one-off Club Consultations where the patient saw ANOTHER physio for
     a treatment-type appointment within 28 days -> Club IA.
  Left alone: true one-offs, and consults where the patient came back >90 days
  later (a new issue). Nothing outside 2026 / other physios' consults (except
  the rule-A run of care) is touched. Durations and invoices are not changed.

Terminal output is counts only; names go in the Word doc in ~/Downloads.
"""
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"), override=True)

import config
import phase2
from docx import Document

YEAR = 2026
CLUB_CONSULT = "1396206071189608060"
CLUB_IA = "392015278608749674"
CLUB_FU = "382589431795684515"
REVIEW_TYPES = {"382563815511823515",    # 2. Review Appointment
                "1558531409491006559"}   # 6. Private Health Insurance Review
TREATMENT_TYPES = REVIEW_TYPES | {CLUB_FU, CLUB_CONSULT, CLUB_IA} | set(config.PHASE1_DROPOFF_IA_TYPE_IDS)
MARTY = {"382563813490168854", "1521620301232739358"}
LONDON = ZoneInfo("Europe/London")
EXCL = set(config.EXCLUDED_FROM_TOTAL_APPTS)
NAMED_FILE = os.path.join(HERE, "named_patients.txt")
DL = os.path.expanduser("~/Downloads")


def P(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def live(a):
    return not (a.get("cancelled_at") or a.get("deleted_at") or a.get("did_not_arrive"))


tid = lambda a: phase2.id_from_link(a.get("appointment_type"))
prac = lambda a: phase2.id_from_link(a.get("practitioner"))


def read_named():
    out = []
    if not os.path.exists(NAMED_FILE):
        return out
    for line in open(NAMED_FILE):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, start = (x.strip() for x in line.partition("|"))
        out.append((name, datetime.strptime(start, "%Y-%m-%d").date() if start else None))
    return out


NICKNAMES = {   # name in named_patients.txt -> first names it may be under in Cliniko
    "paddy": ("patrick", "padraig", "pádraig", "padraic", "paddy"),
    "pat": ("patrick", "patricia", "pat"),
    "katy": ("katy", "katie", "kate", "katherine", "catherine", "kathryn"),
    "peter": ("peter", "pete"),
}


def _norm(x):
    import unicodedata
    x = unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in x if ch.isalpha())


def find_patient(name):
    """(exact matches, near misses). Surname compared ignoring case, spaces,
    apostrophes and accents; first name allows common nicknames."""
    first, _, last = name.strip().partition(" ")
    want_first = {_norm(f) for f in NICKNAMES.get(first.lower(), (first,))} | {_norm(first)}
    want_last = _norm(last)
    # Search the full surname as typed, unspaced, and spaced after Mc/Mac/O'
    # ("McLarnon" / "Mc Larnon" / "O Neill"), then de-dupe by id.
    import re
    base = last.replace(" ", "")
    variants = {last, base, re.sub(r"^(Mc|Mac|O')(?=[A-Z])", r"\1 ", base, flags=re.I)}
    pool = {}
    for v in variants:
        for p in phase2.fetch_all("/patients", [("q[]", f"last_name:~{v}")]):
            pool[str(p["id"])] = p
    pool = list(pool.values())
    same_last = [p for p in pool if _norm(p.get("last_name")) == want_last]
    exact = [p for p in same_last if _norm(p.get("first_name")) == _norm(first)]
    hits = exact or [p for p in same_last
                     if any(_norm(p.get("first_name")).startswith(f) for f in want_first)]
    return hits, same_last


def build():
    now = datetime.now(timezone.utc)
    types = {str(t["id"]): t["name"] for t in phase2.fetch_all("/appointment_types")}
    pracs = {str(p["id"]): f"{p.get('first_name','')} {p.get('last_name','')}".strip()
             for p in phase2.all_practitioners().values()}   # includes leavers

    hist, names = {}, {}

    def history(pid):
        if pid not in hist:
            hist[pid] = sorted((h for h in phase2.fetch_all(f"/patients/{pid}/appointments", [])
                                if h.get("starts_at") and live(h)), key=lambda h: h["starts_at"])
        return hist[pid]

    def pname(pid):
        if pid not in names:
            p = next(iter(phase2.fetch_all("/patients", [("q[]", f"id:={pid}")])), {})
            names[pid] = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or f"Patient {pid}"
        return names[pid]

    changes = {}      # appt_id -> dict
    notes = []        # problems to show in the doc

    def change(a, new, rule):
        if tid(a) == new or str(a["id"]) in changes:
            return
        pid = phase2.id_from_link(a.get("patient"))
        changes[str(a["id"])] = {
            "id": str(a["id"]), "pid": pid, "date": P(a["starts_at"]).astimezone(LONDON),
            "physio": pracs.get(prac(a), "?"), "old_id": tid(a),
            "old": types.get(tid(a), "?"), "new_id": new, "new": types.get(new, "?"), "rule": rule}

    # ---- Rule B: named patients ----
    named_pids = set()
    for name, start in read_named():
        hits, same_last = find_patient(name)
        if len(hits) > 1:
            # Same name, several records (duplicates or different people): keep
            # only those Marty actually saw in YEAR. Still >1 -> flag, never guess.
            def seen_by_marty(p):
                return any(prac(h) in MARTY and P(h["starts_at"]).astimezone(LONDON).year == YEAR
                           for h in history(str(p["id"])))
            n_same = len(hits)
            hits = [p for p in hits if seen_by_marty(p)] or hits
            if len(hits) == 1:
                seen = [h for h in history(str(hits[0]["id"])) if prac(h) in MARTY
                        and P(h["starts_at"]).astimezone(LONDON).year == YEAR]
                notes.append(f"Named patient '{name}': {n_same} Cliniko records with this name — used the only one "
                             f"you saw in {YEAR} ({len(seen)} appointments, first {P(seen[0]['starts_at']).astimezone(LONDON):%d %b}). "
                             "Confirm this is the right person before --apply.")
        if len(hits) != 1:
            near = ", ".join(sorted({f"{p.get('first_name','')} {p.get('last_name','')}".strip()
                                     for p in (hits or same_last)})) or "none"
            notes.append(f"Named patient '{name}': {len(hits)} matching patients in Cliniko — skipped. "
                         f"Patients with that surname: {near}. Put the exact Cliniko name in named_patients.txt.")
            continue
        pid = str(hits[0]["id"]); named_pids.add(pid)
        from_d = start or datetime(YEAR, 1, 1).date()
        # A given start date may be before YEAR (e.g. a 2025 run of care that
        # carries on into YEAR) — then it, not the year, sets the window.
        mine = [h for h in history(pid) if prac(h) in MARTY and tid(h) not in EXCL
                and (start or P(h["starts_at"]).astimezone(LONDON).year == YEAR)
                and P(h["starts_at"]).astimezone(LONDON).date() >= from_d]
        if not mine:
            notes.append(f"Named patient '{name}': no {YEAR} appointment with Marty from {from_d:%d %b} — skipped.")
            continue
        if start and P(mine[0]["starts_at"]).astimezone(LONDON).date() != start:
            notes.append(f"Named patient '{name}': no appointment ON {start:%d %b}; first after it is "
                         f"{P(mine[0]['starts_at']).astimezone(LONDON):%d %b} — used that.")
        change(mine[0], CLUB_IA, "B: named — first appointment")
        for h in mine[1:]:
            if tid(h) == CLUB_CONSULT or tid(h) in REVIEW_TYPES:
                change(h, CLUB_FU, "B: named — later Club Consult / Review")

    # ---- Rules A and C: Marty's 2026 Club Consultations ----
    consults = sorted(
        (a for a in phase2.fetch_all("/individual_appointments",
                                     [("q[]", f"appointment_type_id:={CLUB_CONSULT}")])
         if live(a) and prac(a) in MARTY and P(a["starts_at"]) <= now
         and P(a["starts_at"]).astimezone(LONDON).year == YEAR),
        key=lambda a: a["starts_at"])
    counts = {"A": 0, "C": 0, "one-off kept": 0, ">90d kept": 0, "too recent": 0, "named (rule B)": 0}
    for c in consults:
        pid = phase2.id_from_link(c.get("patient"))
        if pid in named_pids:
            counts["named (rule B)"] += 1
            continue
        if str(c["id"]) in changes:
            continue                       # already a follow-up in an earlier run of care
        s = P(c["starts_at"])
        later = [h for h in history(pid) if P(h["starts_at"]) > s and tid(h) not in EXCL]
        past_later = [h for h in later if P(h["starts_at"]) <= now]
        nxt = past_later[0] if past_later else (later[0] if later else None)
        if nxt and P(nxt["starts_at"]) - s <= timedelta(days=90):
            counts["A"] += 1
            change(c, CLUB_IA, "A: first of run with follow-up")
            prev = s
            for h in later:
                if P(h["starts_at"]) - prev > timedelta(days=90):
                    break
                prev = P(h["starts_at"])
                if tid(h) == CLUB_CONSULT:
                    change(h, CLUB_FU, "A: later Club Consult in same run")
        elif now - s < timedelta(days=21):
            counts["too recent"] += 1
        elif nxt:
            counts[">90d kept"] += 1
        else:
            other = [h for h in later if prac(h) not in MARTY and tid(h) in TREATMENT_TYPES
                     and P(h["starts_at"]) - s <= timedelta(days=28)]
            if other:
                counts["C"] += 1
                change(c, CLUB_IA, "C: one-off, then another physio ≤28d")
            else:
                counts["one-off kept"] += 1

    rows = sorted(changes.values(), key=lambda r: (pname(r["pid"]), r["date"]))
    for r in rows:
        r["patient"] = pname(r["pid"])
    return rows, counts, notes, len(consults)


def write_doc(rows, counts, notes, n, applied):
    doc = Document()
    title = "APPLIED" if applied else "PREVIEW — nothing changed yet"
    doc.add_heading(f"Club Consultation retype {YEAR} — {title}", 1)
    doc.add_paragraph(f"Generated {datetime.now(LONDON):%d %b %Y %H:%M}. {n} of Marty's {YEAR} Club Consultations checked. "
                      f"{len(rows)} appointment(s) to change. Contains patient data — keep private.")
    doc.add_paragraph("Marty's consults: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    if notes:
        doc.add_heading("Check these", 2)
        for x in notes:
            doc.add_paragraph(x, style="List Bullet")
    doc.add_heading("Changes", 2)
    t = doc.add_table(rows=1, cols=6)
    t.style = "Light Grid Accent 1"
    for c, h in zip(t.rows[0].cells, ("Patient", "Date", "Physio", "From", "To", "Rule")):
        c.text = h
    for r in rows:
        for c, v in zip(t.add_row().cells, (r["patient"], f"{r['date']:%d %b %Y}", r["physio"],
                                             r["old"], r["new"], r["rule"])):
            c.text = v
    out = os.path.join(DL, f"Club_Consult_Retype_{'APPLIED' if applied else 'PREVIEW'}.docx")
    doc.save(out)
    return out


def patch(appt_id, type_id):
    r = phase2.SESSION.patch(f"{phase2.BASE}/individual_appointments/{appt_id}",
                             json={"appointment_type_id": type_id})
    if r.status_code == 429:
        time.sleep(30)
        return patch(appt_id, type_id)
    return r


def undo(path):
    rows = list(csv.DictReader(open(os.path.expanduser(path))))
    ok = bad = 0
    for r in rows:
        resp = patch(r["appointment_id"], r["old_type_id"])
        ok, bad = (ok + 1, bad) if resp.ok else (ok, bad + 1)
        if not resp.ok:
            print(f"  FAILED appointment {r['appointment_id']}: HTTP {resp.status_code}")
        time.sleep(0.4)
    print(f"Undo done: {ok} restored, {bad} failed.")


def main():
    if "--undo" in sys.argv:
        return undo(sys.argv[sys.argv.index("--undo") + 1])
    apply = "--apply" in sys.argv
    print("Checking Cliniko… (2–5 minutes)", flush=True)
    rows, counts, notes, n = build()
    to_ia = sum(r["new_id"] == CLUB_IA for r in rows)
    to_fu = sum(r["new_id"] == CLUB_FU for r in rows)
    print(f"{n} of Marty's {YEAR} Club Consultations checked: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"{len(rows)} appointment(s) to change: {to_ia} -> Club IA, {to_fu} -> Club Follow Up")
    if notes:
        print(f"{len(notes)} item(s) to check — see 'Check these' in the doc.")
    if not apply:
        print(f"PREVIEW saved: {write_doc(rows, counts, notes, n, False)}")
        print("Nothing changed. Check the doc, then re-run with --apply.")
        return
    backup = os.path.join(DL, f"club_retype_backup_{datetime.now(LONDON):%Y%m%d_%H%M}.csv")
    with open(backup, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["appointment_id", "old_type_id", "new_type_id", "date"])
        for r in rows:
            w.writerow([r["id"], r["old_id"], r["new_id"], f"{r['date']:%Y-%m-%d}"])
    print(f"Backup saved (for --undo): {backup}")
    ok = bad = 0
    for r in rows:
        resp = patch(r["id"], r["new_id"])
        if resp.ok:
            ok += 1
        else:
            bad += 1
            r["rule"] += f"  ⚠ FAILED HTTP {resp.status_code}"
            print(f"  FAILED appointment {r['id']}: HTTP {resp.status_code} {resp.text[:150]}")
        time.sleep(0.4)
    print(f"Done: {ok} changed, {bad} failed. Record: {write_doc(rows, counts, notes, n, True)}")


if __name__ == "__main__":
    main()
