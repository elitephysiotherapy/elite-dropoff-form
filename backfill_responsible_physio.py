"""One-off backfill (2026-06-01): retro-apply two recent rule changes to every
W/C tab in the drop-off tracker.

Changes applied to existing rows:

  1. **Reattribute physio** for `iadnr` / `cancelled` / `did_not_attend` rows
     to the physio who last ATTENDED with the patient before the drop-off
     event (responsible-physio rule, Martin 2026-05-31). `iacna` / `iadna`
     rows stay with the scheduled physio — patient hadn't attended yet.

  2. **Delete rows** that are currently `iadnr` where the appointment type is
     a one-off wider-8 IA (Sports & MSK Consult, Mummy MOT, Pelvic Health
     Assessment, Club Consultation). Those aren't drop-offs under the rule
     that landed 2026-06-01: only strict-4 IAs (Initial Appt, Club Initial,
     PHI Initial, ACL Initial) generate an IADNR when the patient doesn't
     rebook. Wider-8-only types are one-and-done by design.

Idempotent: re-running does nothing because the physio attribution will
already match and the wider-8-only IADNR rows will already be gone.

Usage:
  ./venv/bin/python backfill_responsible_physio.py           # dry-run preview
  ./venv/bin/python backfill_responsible_physio.py --apply   # actually write
"""

import sys
from dotenv import load_dotenv
load_dotenv(override=True)

import phase1_fetch as p1
import phase2
import config

APPLY = "--apply" in sys.argv

STRICT4 = {str(x) for x in config.PHASE1_DROPOFF_IA_TYPE_IDS}
WIDER8 = {str(x) for x in config.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS}
WIDER8_ONE_OFF = WIDER8 - STRICT4   # 4 types: Sports & MSK, Mummy MOT, Pelvic Health, Club Consultation

print("Pre-fetching Cliniko reference data…", flush=True)
types_by_name = {(t.get("name") or "").strip(): str(t["id"])
                 for t in phase2.fetch_all("/appointment_types")}
pracs_by_id = {}
for prac in phase2.fetch_all("/practitioners"):
    pid = str(prac.get("id"))
    full = f"{prac.get('first_name','?')} {prac.get('last_name','')}".strip()
    pracs_by_id[pid] = full
print(f"  {len(types_by_name)} appt types, {len(pracs_by_id)} practitioners", flush=True)

sh = p1.open_spreadsheet()
history_cache = {}

deletes = []        # (tab_name, row_idx, patient, type_name)  — attended-with-no-rebook, no longer a drop-off
reclassify = []     # (tab_name, row_idx, patient, old_type, new_type)  — Hugh-style: was IADNR, really IACNA/IADNA
updates = []        # (tab_name, row_idx, old_physio, new_physio, patient)

for ws in sh.worksheets():
    if not ws.title.startswith("W/C "):
        continue
    print(f"\nScanning {ws.title}…", flush=True)
    all_vals = ws.get_all_values()
    if len(all_vals) < 2:
        continue
    hdr = all_vals[0]
    try:
        i_physio = hdr.index("Physio")
        i_patient = hdr.index("Patient Name")
        i_appt_type = hdr.index("Appointment Type")
        i_drop_type = hdr.index("Drop-off Type")
        i_appt_id = hdr.index("appointment_id")
        i_cancel_date = hdr.index("Cancellation Date")
    except ValueError as e:
        print(f"  WARN: missing column {e}, skipping tab")
        continue

    for sheet_row_idx, row in enumerate(all_vals[1:], start=2):
        if len(row) <= max(i_physio, i_appt_type, i_drop_type, i_appt_id):
            continue
        drop_type = (row[i_drop_type] or "").strip().lower()
        appt_type_name = (row[i_appt_type] or "").strip()
        appt_id = (row[i_appt_id] or "").strip()
        patient = (row[i_patient] or "").strip()
        current_physio = (row[i_physio] or "").strip()

        # Strip the HYPERLINK wrapper from patient name if present
        if patient.startswith("=HYPERLINK("):
            import re
            m = re.search(r'"([^"]+)"\s*\)\s*$', patient)
            if m:
                patient = m.group(1)

        type_id = types_by_name.get(appt_type_name, "")

        # CHECK 1: only act on iadnr / cancelled / did_not_attend rows
        if drop_type not in ("iadnr", "cancelled", "did_not_attend"):
            continue

        # Fetch the appointment; some rows reference appointments that have
        # been DELETED from Cliniko since (e.g. Hugh McGurk row 22, Johnnie
        # Wright row 7 — both 404). In that case we can still use sheet data
        # for the reclassification but skip physio reattribution.
        appt_data = None
        if appt_id:
            try:
                r = phase2.SESSION.get(f"{phase2.BASE}/individual_appointments/{appt_id}", timeout=30)
                if r.status_code == 200:
                    appt_data = r.json()
            except Exception:
                pass
        sheet_cancel_date = (row[i_cancel_date] or "").strip() if i_cancel_date < len(row) else ""

        # CHECK 2: IADNR on a wider-8-only IA type — split by actual status.
        # Hugh McGurk's case: was logged as IADNR but the appt was actually
        # cancelled → should be IACNA, not deleted. DNA → IADNA. Attended +
        # no rebook → no longer a drop-off under the new strict-4 rule → DELETE.
        if drop_type == "iadnr" and type_id in WIDER8_ONE_OFF:
            if appt_data is not None:
                was_cancelled = bool(appt_data.get("cancelled_at"))
                was_dna = bool(appt_data.get("did_not_arrive"))
            else:
                # Fallback: trust the sheet's Cancellation Date column
                was_cancelled = bool(sheet_cancel_date)
                was_dna = False
            if was_cancelled:
                reclassify.append((ws.title, sheet_row_idx, patient, "iadnr", "iacna"))
            elif was_dna:
                reclassify.append((ws.title, sheet_row_idx, patient, "iadnr", "iadna"))
            else:
                deletes.append((ws.title, sheet_row_idx, patient, appt_type_name))
                continue

        # Beyond this point we need a fetchable appt + patient history for
        # the responsible-physio computation. If Cliniko 404'd, skip — but
        # any reclassify above has already been queued.
        if appt_data is None:
            continue
        patient_id = phase2.id_from_link(appt_data.get("patient"))
        if not patient_id:
            continue

        if patient_id not in history_cache:
            try:
                history_cache[patient_id] = phase2.fetch_patient_full_history(patient_id)
            except Exception:
                history_cache[patient_id] = None
        history = history_cache.get(patient_id)
        if history is None:
            continue

        # Compute responsible physio
        resp_id = p1.responsible_physio_id(appt_data, history)
        new_physio = pracs_by_id.get(str(resp_id), "")
        if new_physio and new_physio != current_physio:
            updates.append((ws.title, sheet_row_idx, current_physio, new_physio, patient))

print(f"\n=== Summary ===")
print(f"  rows to RECLASSIFY (wider-8 IADNR → IACNA/IADNA) : {len(reclassify)}")
print(f"  rows to DELETE (attended wider-8 IA, no rebook)  : {len(deletes)}")
print(f"  rows to RE-ATTRIBUTE physio                       : {len(updates)}")

if reclassify:
    print("\nReclassifications:")
    for tab, row_idx, patient, old, new in reclassify[:30]:
        print(f"  {tab} row {row_idx}  {patient}: {old} → {new}")
    if len(reclassify) > 30:
        print(f"  … and {len(reclassify)-30} more")

if deletes:
    print("\nDeletes:")
    for tab, row_idx, patient, type_name in deletes[:30]:
        print(f"  {tab} row {row_idx}  {patient}  {type_name}")
    if len(deletes) > 30:
        print(f"  … and {len(deletes)-30} more")

if updates:
    print("\nRe-attributions (first 30):")
    for tab, row_idx, old, new, patient in updates[:30]:
        print(f"  {tab} row {row_idx}  {patient}: {old} -> {new}")
    if len(updates) > 30:
        print(f"  … and {len(updates)-30} more")

if not APPLY:
    print("\n(Dry-run only — re-run with --apply to write changes.)")
    sys.exit(0)

# Apply changes — reclassifies first (writes new dropoff_type into col G),
# then deletes (bottom-up per tab so indices stay stable), then physio updates.
print("\nApplying changes…", flush=True)

# Drop-off Type is column G (1-indexed 7). Update via batch_update per tab.
DROPTYPE_COL_LETTER = "G"
by_tab_reclassify = {}
for tab, row_idx, _, _, new in reclassify:
    by_tab_reclassify.setdefault(tab, []).append((row_idx, new))
for tab, items in by_tab_reclassify.items():
    ws = sh.worksheet(tab)
    batch = [{"range": f"{DROPTYPE_COL_LETTER}{row_idx}", "values": [[new]]}
             for row_idx, new in items]
    if batch:
        ws.batch_update(batch, value_input_option="USER_ENTERED")
        print(f"  Reclassified {len(batch)} row(s) in {tab}", flush=True)

by_tab_deletes = {}
for tab, row_idx, _, _ in deletes:
    by_tab_deletes.setdefault(tab, []).append(row_idx)
for tab, idxs in by_tab_deletes.items():
    ws = sh.worksheet(tab)
    for row_idx in sorted(idxs, reverse=True):
        ws.delete_rows(row_idx)
    print(f"  Deleted {len(idxs)} row(s) from {tab}", flush=True)

# Physio updates — batch per tab
by_tab_updates = {}
for tab, row_idx, _, new, _ in updates:
    by_tab_updates.setdefault(tab, []).append((row_idx, new))

# Physio is column D = 4. Build batchUpdate-friendly cell range list.
PHYSIO_COL_LETTER = "D"
for tab, items in by_tab_updates.items():
    ws = sh.worksheet(tab)
    batch = []
    for row_idx, new_physio in items:
        batch.append({"range": f"{PHYSIO_COL_LETTER}{row_idx}", "values": [[new_physio]]})
    if batch:
        ws.batch_update(batch, value_input_option="USER_ENTERED")
        print(f"  Updated {len(batch)} physio cell(s) in {tab}", flush=True)

print("\nRefreshing Performance Dashboard so it picks up the cleaned data…", flush=True)
p1.write_performance_dashboard_tab()
print("  ✓ Performance Dashboard refreshed")
print("\nDone.")
