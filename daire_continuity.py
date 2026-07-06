"""Every patient Daire has an appointment with at Cookstown/Maghera from
8 June 2026 through the week ahead — attended, DNA and CNA included.

One row per unique patient, with attendance breakdown and a "dropped off"
flag (a DNA/CNA with no future live booking).

Pushes to the 'Daire Continuity' tab of the main Performance sheet.

Run:  python3 daire_continuity.py
"""
from datetime import datetime, timedelta, timezone

import gspread

import phase2
import config
import phase1_fetch as pf

DAIRE_ID = "1501275397424158535"
BUSINESSES = {
    "382563815931253999": "Cookstown",
    "1751489684669732550": "Maghera",
}
TAB_NAME = "Daire Continuity"

UTC = timezone.utc
now = datetime.now(UTC)
since = datetime(2026, 6, 8, tzinfo=UTC)        # 8 June 2026
future_end = now + timedelta(days=7)            # week ahead


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def bid(appt):
    return str(phase2.id_from_link(appt.get("business")))


def pid(appt):
    return str(phase2.id_from_link(appt.get("patient")))


def status_of(appt):
    if appt.get("cancelled_at"):
        return "CNA"
    if appt.get("did_not_arrive"):
        return "DNA"
    return "Attended"


# All of Daire's appointments from 8 June through the week ahead (any status).
params = [
    ("q[]", f"practitioner_id:={DAIRE_ID}"),
    ("q[]", f"starts_at:>={iso(since)}"),
    ("q[]", f"starts_at:<={iso(future_end)}"),
]
live = list(phase2.fetch_all("/individual_appointments", params))
cancelled = list(phase2.fetch_all("/individual_appointments",
                                  params + [("q[]", "cancelled_at:?")]))
by_id = {a["id"]: a for a in live}
for a in cancelled:
    by_id[a["id"]] = a
appts = list(by_id.values())

# Per-patient record of every appointment in the window.
patients = {}   # pid -> dict
other_clinic_appts = 0

for a in appts:
    b = bid(a)
    if b not in BUSINESSES:
        other_clinic_appts += 1
        continue
    start = a.get("starts_at") or ""
    start_dt = phase2.parse_iso(start)
    if not start_dt:
        continue
    p = pid(a)
    rec = patients.setdefault(p, {"appts": []})
    rec["appts"].append({
        "dt": start_dt,
        "ts": start,
        "clinic": BUSINESSES[b],
        "status": status_of(a),
        "future": start_dt > now,
    })

names_cache = {}


def name(pid_):
    if pid_ in names_cache:
        return names_cache[pid_]
    nm = pid_
    try:
        r = phase2.SESSION.get(f"{phase2.BASE}/patients/{pid_}", timeout=30)
        if r.status_code == 200:
            j = r.json()
            nm = f"{j.get('first_name','')} {j.get('last_name','')}".strip()
    except Exception:
        pass
    names_cache[pid_] = nm
    return nm


def fmt(ts):
    dt = phase2.parse_iso(ts)
    return dt.strftime("%a %d %b %H:%M") if dt else ts


# --- practitioner id -> display name (for the "Rebooked with" column) ---
prac_name = {}
for pr in phase2.fetch_all("/practitioners"):
    full = f"{pr.get('first_name','')} {pr.get('last_name','')}".strip()
    prac_name[str(pr["id"])] = config.PRACTITIONER_DISPLAY_NAME.get(full, full)


def practitioner_of(appt):
    return prac_name.get(str(phase2.id_from_link(appt.get("practitioner"))), "?")


def future_booking_any(pid_):
    """Earliest future LIVE appointment for this patient with ANY practitioner.
    fetch_all excludes cancelled by default, so this is genuine rebookings only.
    Returns (formatted_time, physio_display, clinic) or None if nothing booked."""
    appts = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"patient_id:={pid_}"),
        ("q[]", f"starts_at:>={iso(now)}"),
    ]))
    fut = []
    for a in appts:
        dt = phase2.parse_iso(a.get("starts_at") or "")
        if dt and dt > now:
            fut.append((dt, a))
    if not fut:
        return None
    fut.sort(key=lambda x: x[0])
    dt, a = fut[0]
    clinic = BUSINESSES.get(bid(a), "Other")
    return (dt.strftime("%a %d %b %H:%M"), practitioner_of(a), clinic)


rows = []
needs_call = 0
for p, rec in patients.items():
    aps = sorted(rec["appts"], key=lambda x: x["dt"])
    past = [x for x in aps if not x["future"]]
    fut = [x for x in aps if x["future"]]

    attended = sum(1 for x in aps if x["status"] == "Attended")
    dna = sum(1 for x in aps if x["status"] == "DNA")
    cna = sum(1 for x in aps if x["status"] == "CNA")

    last = past[-1] if past else (fut[0] if fut else None)

    # THE genuine rebooking check: any future live appt with ANY physio.
    booking = future_booking_any(p)
    if booking:
        next_time, next_with, next_clinic = booking
        rebooked, action = "Yes", "OK — booked ahead"
    else:
        next_time, next_with, next_clinic = "—", "—", "—"
        rebooked, action = "No", "CALL — no future booking"
        needs_call += 1

    rows.append([
        name(p),
        fmt(aps[0]["ts"]),
        fmt(last["ts"]) if last else "",
        last["status"] if last else "",
        last["clinic"] if last else "",
        attended, dna, cna,
        rebooked, next_time, next_with, next_clinic,
        action,
    ])

# NEEDS-CALL patients first (rebooked "No" sorts before "Yes"), then A–Z.
rows.sort(key=lambda r: (r[8] == "Yes", r[0].lower()))

header = ["Patient", "First appt w/ Daire (since 8 Jun)", "Last appt w/ Daire",
          "Last status", "Last clinic", "Attended", "DNA", "CNA",
          "Rebooked?", "Next appt (any physio)", "Rebooked with", "Next clinic",
          "Action"]

# ---- console preview ----
print(f"\nDaire handover — {len(rows)} patients seen 8 Jun → 2 Jul 2026 "
      f"(Cookstown & Maghera, incl. DNA/CNA)")
print(f"‼️  {needs_call} NEED A CALL (no future booking with ANY physio)   |   "
      f"{len(rows) - needs_call} already rebooked")
if other_clinic_appts:
    print(f"(note: {other_clinic_appts} of Daire's appts were at other locations "
          f"and excluded by the Cookstown/Maghera filter)")
print()
for r in rows:
    tag = "‼️ CALL " if r[8] == "No" else "  ok    "
    nxt = f"{r[9]} w/ {r[10]}" if r[8] == "Yes" else "—"
    print(f"{tag} {r[0]:28} last {r[2]} [{r[3]}]  A{r[5]}/D{r[6]}/C{r[7]}  next {nxt}")

# ---- push to the Performance sheet tab ----
sh = pf.open_spreadsheet()
try:
    ws = sh.worksheet(TAB_NAME)
    ws.clear()
except gspread.exceptions.WorksheetNotFound:
    ws = sh.add_worksheet(title=TAB_NAME, rows=max(80, len(rows) + 10),
                          cols=len(header))

stamp = now.strftime("%Y-%m-%d %H:%M UTC")
ws.update([[f"Daire handover — every patient seen 8 Jun–2 Jul 2026 "
            f"(Cookstown & Maghera, incl. DNA & CNA). "
            f"{len(rows)} patients, {needs_call} NEED A CALL (no future booking "
            f"with any physio). Sorted call-first. Generated {stamp}."]],
          "A1")
ws.update([header] + rows, "A3")
ws.format("A3:M3", {"textFormat": {"bold": True}})

print(f"\nWrote {len(rows)} rows to tab '{TAB_NAME}'.")
print(f"Sheet URL: {sh.url}")
