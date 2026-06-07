"""Appointments_Chart tab + line chart on the CFO sheet.

Recreates Martin's "Gen Pop and Sport" 13-month rolling chart automatically.
Pulls last 16 months from Cliniko (13 to display + 3 extra for the rolling AOV
calculation on the earliest displayed month), then writes the data table and
embeds/refreshes a line chart on the "Appointments_Chart" tab of the CFO sheet.

Series on the chart (matches Martin's screenshot):
  • Gen Pop (blue)   — sum of completed Gen Pop appointment types
  • Club (orange)    — sum of completed Club appointment types
  • Total (black)    — Gen Pop + Club + Pilates classes
  • Budget (green)   — revenue budget ÷ rolling 3-month AOV

Pilates count = number of pilates Class group_appointments instances in the
month (NOT attendee count — per Martin 2026-06-07 — class-arrival data on
Cliniko's group endpoint is too sparse to use).

Revenue & AOV definitions:
  • Revenue (Cliniko) = sum of total_amount across non-archived/non-deleted
    invoices issued in the month.
  • Total appts in month = Gen Pop + Club + Pilates (matches the chart's
    Total line so the AOV→Budget conversion is self-consistent).
  • Rolling 3-mo AOV for month M = (rev[M-1]+rev[M-2]+rev[M-3]) ÷
                                   (appts[M-1]+appts[M-2]+appts[M-3])
  • Appointment Budget for month M = revenue_budget[M] ÷ rolling_3mo_AOV[M]

Revenue budget is read from the Budget_2026 tab on the CFO sheet (row labelled
"GROUP", columns Jan…Dec, currency strings like £41,635).

Schedule: monthly via Render cron `elite-appts-chart-monthly` (kept separate
from the heavier LTV cron so a failure on one doesn't block the other).
"""

from __future__ import annotations

import json
import os
import re
import sys
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

TAB_NAME = "Appointments_Chart"
PILATES_CLASS_ID = "843440687104923293"

CLUB_IDS = {
    "392015278608749674", "382589431795684515", "1396206071189608060",
    "945551547020874765", "1031259844406941435",
    "1882529999999735591", "752219543803270402", "1820239945827096402",
    "765760828145145406", "765761537334842944",
    "998334021563847947", "998332399567770890", "1810765504990680283",
    "1796352479944775353", "1796356817727526589",
    "1796354967016052411", "1796358853206480576",
}
GEN_IDS = {
    "382563815654429852", "382563815511823515",
    "1558530673046721630", "1558531409491006559",
    "1118674052857206233", "1118674366867969498",
    "1194028405859816854",
    "1521627460095973060", "1206575759565526893",
    "1192928323588592985", "980228540505003527",
}

MONTHS_DISPLAY = 13      # how many months to show on the chart
MONTHS_AOV_PRIOR = 3     # extra months of history just for rolling AOV calc
SHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ─── Google Sheets helpers ─────────────────────────────────────────────────

def _gc():
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        return gspread.authorize(
            Credentials.from_service_account_info(json.loads(raw), scopes=SHEET_SCOPES))
    return gspread.authorize(
        Credentials.from_service_account_file(
            str(ROOT / "service_account.json"), scopes=SHEET_SCOPES))


def _cfo_sheet():
    sid = os.environ.get("CFO_DASHBOARD_SHEET_ID")
    if not sid:
        raise RuntimeError("CFO_DASHBOARD_SHEET_ID env var not set")
    return _gc().open_by_key(sid)


def _money_to_float(s):
    if not s:
        return 0.0
    s = re.sub(r"[£,\s]", "", str(s))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _read_revenue_budget(year_months):
    """Read the GROUP row from Budget_2026 → {(year, month): £}.

    Budget_2026 has yearly tabs only; for now we assume Martin keeps the same
    Budget_2026 tab updated. Tab layout: row 4 has month headers Jan..Dec,
    row 7 (GROUP) has the figures.
    """
    sh = _cfo_sheet()
    try:
        ws = sh.worksheet("Budget_2026")
    except gspread.WorksheetNotFound:
        print("  WARN no Budget_2026 tab — budget line will be blank")
        return {}
    rows = ws.get_all_values()
    if len(rows) < 7:
        return {}
    header = rows[3]  # 4th row → Clinic | Jan | Feb | …
    group = rows[6]   # 7th row → GROUP | £41,635 | …
    month_name_to_col = {}
    for i, h in enumerate(header):
        if h and h.strip() in {"Jan","Feb","Mar","Apr","May","Jun",
                                "Jul","Aug","Sep","Oct","Nov","Dec"}:
            month_name_to_col[h.strip()] = i
    name_to_num = {n: i+1 for i, n in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"])}
    out = {}
    # Budget tab is a single calendar year (2026 in the current tab name).
    # Apply to year 2026; anything outside that — same budget reused (best effort).
    budget_year = 2026
    for mname, col in month_name_to_col.items():
        if col < len(group):
            out[(budget_year, name_to_num[mname])] = _money_to_float(group[col])
    # Cross-year fallback: if a requested month is in a different year, reuse the
    # same calendar-month value from the budget year.
    backfilled = {}
    for (y, m) in year_months:
        if (y, m) in out:
            backfilled[(y, m)] = out[(y, m)]
        elif (budget_year, m) in out:
            backfilled[(y, m)] = out[(budget_year, m)]
        else:
            backfilled[(y, m)] = 0.0
    return backfilled


# ─── Cliniko monthly counts ────────────────────────────────────────────────

def _iso_month_window(year, month):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT00:00:00Z")


def _stats_for_month(year, month):
    """Return dict with gen_pop, club, pilates, revenue for the month."""
    iso_start, iso_end = _iso_month_window(year, month)
    iso_date_start = f"{year:04d}-{month:02d}-01"

    active = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={iso_start}"),
        ("q[]", f"starts_at:<{iso_end}"),
    ]))
    completed = [a for a in active if not a.get("did_not_arrive")]

    def in_bucket(apps, ids):
        return sum(1 for a in apps
                   if phase2.id_from_link(a.get("appointment_type")) in ids)

    gen = in_bucket(completed, GEN_IDS)
    club = in_bucket(completed, CLUB_IDS)

    groups = list(phase2.fetch_all("/group_appointments", [
        ("q[]", f"starts_at:>={iso_start}"),
        ("q[]", f"starts_at:<{iso_end}"),
    ]))
    pilates_classes = sum(1 for g in groups
                          if phase2.id_from_link(g.get("appointment_type")) == PILATES_CLASS_ID)

    iso_date_end = _iso_month_window(year, month)[1][:10]
    invs = list(phase2.fetch_all("/invoices", [
        ("q[]", f"issue_date:>={iso_date_start}"),
        ("q[]", f"issue_date:<{iso_date_end}"),
    ]))
    revenue = 0.0
    for inv in invs:
        if inv.get("archived_at") or inv.get("deleted_at"):
            continue
        revenue += float(inv.get("total_amount") or 0)

    return {"gen": gen, "club": club, "pilates": pilates_classes,
            "total": gen + club + pilates_classes, "revenue": revenue}


def _months_back(n):
    """Return list of (year, month) for the n most recent COMPLETED months,
    earliest first. The current calendar month (which is partial) is excluded —
    monthly meetings always reference last-completed-month as the latest data
    point so AOV / totals aren't dragged down by mid-month emptiness.
    """
    now = datetime.now(timezone.utc)
    out = []
    # Start from the previous completed month
    y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


# ─── Chart writer ──────────────────────────────────────────────────────────

def _write_table_and_chart(rows, sheet):
    """rows = list of [Month, GenPop, Club, Pilates, Total, Revenue,
                       RevenueBudget, RollingAOV, AppointmentBudget]."""
    headers = ["Month", "Gen Pop", "Club", "Pilates (classes)", "Total",
               "Revenue (Cliniko)", "Revenue Budget", "Rolling 3-mo AOV",
               "Appointment Budget"]
    try:
        ws = sheet.worksheet(TAB_NAME)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=TAB_NAME, rows=max(len(rows) + 10, 30),
                                 cols=len(headers) + 2)
    body = [headers] + rows
    ws.update("A1", body, value_input_option="RAW")

    # Build a line chart referencing the data range
    ws_id = ws.id
    last_data_row = len(rows) + 1  # 1-based incl. header
    requests = [
        # Remove all existing charts
        {"deleteEmbeddedObject": {"objectId": cid}}
        for cid in [c["chartId"] for c in (ws._properties.get("charts") or [])]
        if cid
    ]
    chart_spec = {
        "title": "Gen Pop and Sport — rolling 13 months",
        "basicChart": {
            "chartType": "LINE",
            "legendPosition": "BOTTOM_LEGEND",
            "axis": [
                {"position": "BOTTOM_AXIS"},
                {"position": "LEFT_AXIS"},
            ],
            "domains": [{
                "domain": {"sourceRange": {"sources": [{
                    "sheetId": ws_id, "startRowIndex": 1, "endRowIndex": last_data_row,
                    "startColumnIndex": 0, "endColumnIndex": 1,
                }]}}
            }],
            "series": [
                {"series": {"sourceRange": {"sources": [{
                    "sheetId": ws_id, "startRowIndex": 1, "endRowIndex": last_data_row,
                    "startColumnIndex": 1, "endColumnIndex": 2,  # Gen Pop
                }]}}, "targetAxis": "LEFT_AXIS",
                 "color": {"red": 0.26, "green": 0.52, "blue": 0.96}},
                {"series": {"sourceRange": {"sources": [{
                    "sheetId": ws_id, "startRowIndex": 1, "endRowIndex": last_data_row,
                    "startColumnIndex": 2, "endColumnIndex": 3,  # Club
                }]}}, "targetAxis": "LEFT_AXIS",
                 "color": {"red": 0.95, "green": 0.51, "blue": 0.19}},
                {"series": {"sourceRange": {"sources": [{
                    "sheetId": ws_id, "startRowIndex": 1, "endRowIndex": last_data_row,
                    "startColumnIndex": 4, "endColumnIndex": 5,  # Total
                }]}}, "targetAxis": "LEFT_AXIS",
                 "color": {"red": 0.0, "green": 0.0, "blue": 0.0}},
                {"series": {"sourceRange": {"sources": [{
                    "sheetId": ws_id, "startRowIndex": 1, "endRowIndex": last_data_row,
                    "startColumnIndex": 8, "endColumnIndex": 9,  # Appt Budget
                }]}}, "targetAxis": "LEFT_AXIS",
                 "color": {"red": 0.42, "green": 0.66, "blue": 0.31}},
            ],
            "headerCount": 0,
        }
    }
    requests.append({
        "addChart": {
            "chart": {
                "spec": chart_spec,
                "position": {
                    "overlayPosition": {
                        "anchorCell": {"sheetId": ws_id,
                                       "rowIndex": 1, "columnIndex": 10},
                        "widthPixels": 900, "heightPixels": 420,
                    }
                }
            }
        }
    })
    sheet.batch_update({"requests": requests})


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"[appts_chart] computing last {MONTHS_DISPLAY + MONTHS_AOV_PRIOR} months "
          f"({MONTHS_DISPLAY} display + {MONTHS_AOV_PRIOR} prior for rolling AOV)", flush=True)
    months = _months_back(MONTHS_DISPLAY + MONTHS_AOV_PRIOR)

    # Compute monthly stats
    data = {}
    for (y, m) in months:
        print(f"[appts_chart]   fetching {y:04d}-{m:02d}…", flush=True)
        data[(y, m)] = _stats_for_month(y, m)

    # Revenue budgets for the displayed months
    display_months = months[MONTHS_AOV_PRIOR:]
    budgets = _read_revenue_budget(display_months)

    rows = []
    for i, (y, m) in enumerate(display_months):
        d = data[(y, m)]
        # Rolling 3-month AOV = sum of revenues / sum of totals from PRIOR 3 months
        prior = months[i:i + MONTHS_AOV_PRIOR]  # i corresponds to position in months[]
        prior_rev = sum(data[(yp, mp)]["revenue"] for (yp, mp) in prior)
        prior_total = sum(data[(yp, mp)]["total"] for (yp, mp) in prior)
        aov = (prior_rev / prior_total) if prior_total else 0.0
        rev_budget = budgets.get((y, m), 0.0)
        appt_budget = (rev_budget / aov) if aov else 0.0
        label = datetime(y, m, 1).strftime("%b %Y")
        rows.append([
            label, d["gen"], d["club"], d["pilates"], d["total"],
            round(d["revenue"]), round(rev_budget),
            round(aov, 2) if aov else "—",
            round(appt_budget) if appt_budget else "—",
        ])

    sh = _cfo_sheet()
    _write_table_and_chart(rows, sh)
    print(f"[appts_chart] {TAB_NAME} tab + chart updated ({len(rows)} months)")
    for r in rows:
        print(f"[appts_chart]   {r[0]:>10s} | gen={r[1]:>4d} club={r[2]:>4d} "
              f"pilates={r[3]:>3d} total={r[4]:>4d} "
              f"rev=£{r[5]:>6d} budget=£{r[6]:>6d} "
              f"aov=£{r[7]} appt_bgt={r[8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
