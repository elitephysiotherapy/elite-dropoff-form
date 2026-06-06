"""Patient Lifetime Value (LTV) — monthly recompute, writes to CFO Sheet.

Heavy computation (~2 hours of API calls). Runs once a month via Render
cron (elite-ltv-monthly), writes 4 cached values to the LTV_Cache tab on
the CFO Dashboard sheet. The Cockpit tab's regular build process reads
those cached values — Cockpit refresh stays fast.

Methodology (Martin 2026-06-06):
  Cohort eligibility: patient.created_at in [30 months ago, 6 months ago].
    → Everyone has had ≥ 6 months to mature; nobody is older than 30 months.

  Two cohort definitions:
    PHYSIO  — patient's FIRST individual appointment was type
              "1. Initial Appointment" (382563815654429852). Strictly that
              one type, NOT Club Initial / PHI Initial / ACL Initial / Mummy
              MOT.
    PILATES — patient's FIRST invoice (by issue_date) contains a line item
              whose name contains "pilates" (case-insensitive).

  Price normalisation:
    Any invoice line item with unit_price == £55 is bumped to £60 to reflect
    the price rise on Initial Assessment / follow-ups.

  Revenue:
    Sum of total_amount across every non-archived/non-deleted invoice for
    each cohort patient, with the £55→£60 bumps applied.

  Four headline figures written to LTV_Cache:
    physio_ltv_all       — mean LTV across all 829-ish physio cohort patients
    pilates_ltv_all      — mean LTV across all 88-ish pilates cohort patients
    physio_ltv_engaged   — mean LTV excluding patients with exactly 1 attended
                           appointment in their lifetime
    pilates_ltv_engaged  — mean LTV excluding patients with exactly 1
                           pilates invoice in their lifetime
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
import phase2  # noqa: E402

PHYSIO_IA_TYPE = "382563815654429852"  # ONLY "1. Initial Appointment"
TAB_NAME = "LTV_Cache"
SHEET_ID = os.environ.get("CFO_DASHBOARD_SHEET_ID", "")


def _months_ago(now: datetime, n: int) -> datetime:
    y, m = now.year, now.month - n
    while m <= 0:
        m += 12
        y -= 1
    return now.replace(year=y, month=m)


def _sheets_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        import json
        return gspread.authorize(
            Credentials.from_service_account_info(json.loads(raw), scopes=scopes))
    return gspread.authorize(
        Credentials.from_service_account_file(
            str(ROOT / "service_account.json"), scopes=scopes))


def _get_or_create_cache_tab():
    if not SHEET_ID:
        raise RuntimeError("CFO_DASHBOARD_SHEET_ID env var not set")
    ss = _sheets_client().open_by_key(SHEET_ID)
    try:
        return ss.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=TAB_NAME, rows=20, cols=5)
        ws.update("A1:E1", [["metric", "value_gbp", "cohort_size",
                              "computed_at", "notes"]])
        return ws


def compute() -> dict:
    """Run the heavy LTV computation. Returns dict of metric → (value, cohort)."""
    now = datetime.now(timezone.utc)
    start = _months_ago(now, 30)
    end = _months_ago(now, 6)
    iso_start = start.strftime("%Y-%m-%dT00:00:00Z")
    iso_end = end.strftime("%Y-%m-%dT00:00:00Z")
    print(f"[ltv] window patient.created_at: {start.date()} → {end.date()}",
          flush=True)

    # 1. New patients in window
    print("[ltv] [1/5] new patients…", flush=True)
    t0 = time.time()
    patients = list(phase2.fetch_all("/patients", [
        ("q[]", f"created_at:>={iso_start}"),
        ("q[]", f"created_at:<={iso_end}"),
    ]))
    print(f"[ltv]      {len(patients)} new patients ({time.time()-t0:.0f}s)",
          flush=True)
    new_ids = {str(p["id"]) for p in patients}

    # 2. All individual appointments starting since cohort window
    print("[ltv] [2/5] individual appointments…", flush=True)
    t0 = time.time()
    apps = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={iso_start}"),
    ]))
    print(f"[ltv]      {len(apps)} appointments ({time.time()-t0:.0f}s)",
          flush=True)

    first_indiv: dict[str, tuple[str, str | None]] = {}
    attended_count: dict[str, int] = defaultdict(int)
    for a in apps:
        pid = phase2.id_from_link(a.get("patient"))
        if pid not in new_ids:
            continue
        starts = a.get("starts_at", "")
        tid = phase2.id_from_link(a.get("appointment_type"))
        if pid not in first_indiv or starts < first_indiv[pid][0]:
            first_indiv[pid] = (starts, tid)
        if not a.get("cancelled_at"):
            attended_count[pid] += 1

    physio_cohort = {pid for pid, (_, t) in first_indiv.items()
                     if t == PHYSIO_IA_TYPE}
    print(f"[ltv] [3/5] physio cohort: {len(physio_cohort)}", flush=True)

    # 4. Invoices
    print("[ltv] [4/5] invoices…", flush=True)
    t0 = time.time()
    invs = list(phase2.fetch_all("/invoices", [
        ("q[]", f"issue_date:>={start.strftime('%Y-%m-%d')}"),
    ]))
    print(f"[ltv]      {len(invs)} invoices ({time.time()-t0:.0f}s)",
          flush=True)

    patient_invoices: dict[str, list[dict]] = defaultdict(list)
    for inv in invs:
        if inv.get("archived_at") or inv.get("deleted_at"):
            continue
        pid = phase2.id_from_link(inv.get("patient"))
        if not pid:
            continue
        patient_invoices[pid].append({
            "date": (inv.get("issue_date") or "")[:10],
            "id": str(inv["id"]),
            "total": float(inv.get("total_amount") or 0),
        })
    for pid in patient_invoices:
        patient_invoices[pid].sort(key=lambda x: x["date"])

    # 5. Line items per invoice — for pilates ID + £55→£60 bumps
    print("[ltv] [5/5] line items for cohort patients' invoices…", flush=True)
    t0 = time.time()
    pilates_cohort: set[str] = set()
    pilates_invoice_counts: dict[str, int] = defaultdict(int)
    bumps = 0
    cohort_with_invoices = new_ids & set(patient_invoices.keys())
    n_to_fetch = sum(len(patient_invoices[pid]) for pid in cohort_with_invoices)
    print(f"[ltv]      scanning {n_to_fetch} invoices…", flush=True)
    done = 0
    for pid in cohort_with_invoices:
        for idx, inv_rec in enumerate(patient_invoices[pid]):
            items = list(phase2.fetch_all(
                f"/invoices/{inv_rec['id']}/invoice_items"))
            has_pilates = any(
                "pilates" in (it.get("name", "") or "").lower() for it in items)
            if idx == 0 and has_pilates:
                pilates_cohort.add(pid)
            if has_pilates:
                pilates_invoice_counts[pid] += 1
            for it in items:
                unit = float(it.get("unit_price") or 0)
                qty = float(it.get("quantity") or 1)
                if unit == 55:
                    inv_rec["total"] += 5 * qty
                    bumps += 1
            done += 1
            if done % 500 == 0:
                print(f"[ltv]      …{done}/{n_to_fetch} ({time.time()-t0:.0f}s)",
                      flush=True)
    print(f"[ltv]      {bumps} £55→£60 bumps, "
          f"{len(pilates_cohort)} pilates cohort ({time.time()-t0:.0f}s)",
          flush=True)

    def mean_ltv(cohort: set[str], singleton_filter=None) -> tuple[float, int]:
        used = cohort
        if singleton_filter:
            used = {pid for pid in cohort if not singleton_filter(pid)}
        if not used:
            return 0.0, 0
        spend = [sum(i["total"] for i in patient_invoices.get(pid, []))
                 for pid in used]
        return sum(spend) / len(spend), len(spend)

    physio_all = mean_ltv(physio_cohort)
    pilates_all = mean_ltv(pilates_cohort)
    physio_eng = mean_ltv(physio_cohort,
                          lambda pid: attended_count.get(pid, 0) <= 1)
    pilates_eng = mean_ltv(pilates_cohort,
                           lambda pid: pilates_invoice_counts.get(pid, 0) <= 1)

    return {
        "physio_ltv_all":      physio_all,
        "pilates_ltv_all":     pilates_all,
        "physio_ltv_engaged":  physio_eng,
        "pilates_ltv_engaged": pilates_eng,
    }


def write_to_sheet(metrics: dict) -> None:
    ws = _get_or_create_cache_tab()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    notes_map = {
        "physio_ltv_all":      "first appt = Initial Appointment; cohort 30→6 mo ago; £55→£60 adjusted",
        "pilates_ltv_all":     "first invoice = pilates; cohort 30→6 mo ago",
        "physio_ltv_engaged":  "as physio_ltv_all but excl. patients with ≤1 attended appt lifetime",
        "pilates_ltv_engaged": "as pilates_ltv_all but excl. patients with ≤1 pilates invoice lifetime",
    }
    rows = [["metric", "value_gbp", "cohort_size", "computed_at", "notes"]]
    for key in ("physio_ltv_all", "pilates_ltv_all",
                "physio_ltv_engaged", "pilates_ltv_engaged"):
        value, cohort_size = metrics[key]
        rows.append([key, round(value, 2), cohort_size, stamp,
                     notes_map.get(key, "")])
    ws.update("A1:E5", rows, value_input_option="RAW")
    print(f"[ltv] LTV_Cache tab updated:")
    for r in rows[1:]:
        print(f"[ltv]   {r[0]:24s} = £{r[1]:>8.2f}  (n={r[2]})")


def main() -> int:
    metrics = compute()
    write_to_sheet(metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
