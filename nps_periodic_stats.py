"""Add monthly + previous-week NPS stats to the NPS/marketing sheet.

Reads from the existing "NPS - Raw Data" tab and writes two new tabs:

  NPS - Monthly    Historical per-month NPS by physio + clinic total.
                   One row per calendar month, sorted most-recent first.
                   Built fresh from raw data each run — safe to re-run any time.

  NPS - Last Week  Single-snapshot view of the previous Mon→Sun NPS, per
                   physio + clinic total. Replaced each run (no history).

NPS formula: 100 × (Promoters − Detractors) / Responses
  Promoter:  score 9 or 10
  Passive:   score 7 or 8
  Detractor: score 0–6
NPS shown as integer (rounded). If responses == 0, shown as "—".

Designed to run weekly via Render cron `elite-nps-weekly` (Monday morning).
Idempotent: re-running just rebuilds both tabs.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

LONDON = ZoneInfo("Europe/London")

RAW_TAB = "NPS - Raw Data"
MONTHLY_TAB = "NPS - Monthly"
WEEKLY_TAB = "NPS - Last Week"

# Physio display order matches config.PRACTITIONER_DISPLAY_ORDER
PHYSIOS = list(config.PRACTITIONER_DISPLAY_ORDER)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _sheets_client():
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if raw:
        import json
        return gspread.authorize(
            Credentials.from_service_account_info(json.loads(raw), scopes=scopes))
    return gspread.authorize(
        Credentials.from_service_account_file(
            str(ROOT / "service_account.json"), scopes=scopes))


def _open_marketing_sheet():
    if not config.MARKETING_SPREADSHEET_ID:
        raise RuntimeError("config.MARKETING_SPREADSHEET_ID not set")
    return _sheets_client().open_by_key(config.MARKETING_SPREADSHEET_ID)


def _parse_date_sent(s: str) -> datetime | None:
    """Raw Data uses '27 May 2026' format."""
    if not s:
        return None
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=LONDON)
        except ValueError:
            continue
    return None


def _nps(promoters: int, passives: int, detractors: int) -> str:
    total = promoters + passives + detractors
    if total == 0:
        return "—"
    return str(round(100 * (promoters - detractors) / total))


def _classify(score) -> str | None:
    """Score → 'Promoter' / 'Passive' / 'Detractor' / None (if not parseable)."""
    try:
        s = int(str(score).strip())
    except (ValueError, AttributeError):
        return None
    if s >= 9:
        return "Promoter"
    if s >= 7:
        return "Passive"
    if s >= 0:
        return "Detractor"
    return None


# ─── Raw data load ─────────────────────────────────────────────────────────

def _load_responses(sh) -> list[dict]:
    """Read raw data, keep only rows where Score is set (i.e. response received).

    Returns list of dicts: date (datetime), physio, clinic, score, category.
    Skips rows where Date Sent or Score can't be parsed.
    """
    ws = sh.worksheet(RAW_TAB)
    rows = ws.get_all_values()
    if not rows or len(rows) < 2:
        return []
    out: list[dict] = []
    for r in rows[1:]:
        if len(r) < 8:
            continue
        date_sent = _parse_date_sent(r[0])
        if date_sent is None:
            continue
        score_str = (r[6] or "").strip()
        if not score_str:
            continue
        cat = _classify(score_str) or (r[7] or "").strip().title()
        if cat not in ("Promoter", "Passive", "Detractor"):
            continue
        out.append({
            "date": date_sent,
            "physio": (r[3] or "").strip(),
            "clinic": (r[4] or "").strip(),
            "score": int(score_str),
            "category": cat,
        })
    return out


# ─── Aggregations ──────────────────────────────────────────────────────────

def _nps_block(responses: list[dict]) -> tuple[int, int, int, int, str]:
    """(responses, promoters, passives, detractors, nps) for a slice of data."""
    n = len(responses)
    p = sum(1 for x in responses if x["category"] == "Promoter")
    ps = sum(1 for x in responses if x["category"] == "Passive")
    d = sum(1 for x in responses if x["category"] == "Detractor")
    return n, p, ps, d, _nps(p, ps, d)


def _build_monthly(all_responses: list[dict]) -> list[list]:
    """Build the NPS - Monthly tab rows. One row per month with data."""
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in all_responses:
        key = r["date"].strftime("%Y-%m")
        by_month[key].append(r)

    months = sorted(by_month.keys(), reverse=True)
    header = ["Month", "Responses", "Promoters", "Passives", "Detractors",
              "Clinic NPS"] + [f"{p} NPS" for p in PHYSIOS]
    out: list[list] = [header]

    for ym in months:
        responses = by_month[ym]
        n, p, ps, d, clinic_nps = _nps_block(responses)
        row = [datetime.strptime(ym + "-01", "%Y-%m-%d").strftime("%b %Y"),
               n, p, ps, d, clinic_nps]
        for physio in PHYSIOS:
            physio_rs = [x for x in responses if x["physio"] == physio]
            _, pp, ppa, pd_, p_nps = _nps_block(physio_rs)
            row.append(p_nps if (pp + ppa + pd_) else "—")
        out.append(row)
    return out


def _build_last_week(all_responses: list[dict], now: datetime | None = None) -> list[list]:
    """Build the NPS - Last Week tab — single snapshot of previous Mon→Sun."""
    if now is None:
        now = datetime.now(LONDON)
    days_since_mon = now.weekday()  # Mon = 0
    this_mon = (now - timedelta(days=days_since_mon)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    last_mon = this_mon - timedelta(days=7)
    last_sun_end = this_mon - timedelta(seconds=1)
    window = [r for r in all_responses
              if last_mon <= r["date"] <= last_sun_end]

    n, p, ps, d, clinic_nps = _nps_block(window)
    label = f"W/C {last_mon.strftime('%d %b %Y')}"
    span = (f"{last_mon.strftime('%a %d %b')} – "
            f"{last_sun_end.strftime('%a %d %b')}")

    out: list[list] = [
        ["NPS — Last Week"],
        [f"{label}  ({span})"],
        [f"Last refreshed: {now.strftime('%Y-%m-%d %H:%M %Z')}"],
        [],
        ["", "Responses", "Promoters", "Passives", "Detractors", "NPS"],
        ["Clinic total", n, p, ps, d, clinic_nps],
        [],
        ["Physio", "Responses", "Promoters", "Passives", "Detractors", "NPS"],
    ]
    for physio in PHYSIOS:
        physio_rs = [x for x in window if x["physio"] == physio]
        pn, pp, ppa, pd_, p_nps = _nps_block(physio_rs)
        out.append([physio, pn, pp, ppa, pd_, p_nps if pn else "—"])

    out.append([])
    out.append(["Note: NPS = 100 × (Promoters − Detractors) / Responses. "
                "Promoter 9–10 · Passive 7–8 · Detractor 0–6."])
    out.append(["This tab is a snapshot only — replaced each Monday. "
                "Historical monthly data lives on the 'NPS - Monthly' tab."])
    return out


# ─── Writing ───────────────────────────────────────────────────────────────

def _get_or_create_tab(sh, title: str, rows: int, cols: int):
    try:
        ws = sh.worksheet(title)
        ws.clear()
        return ws
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def _write_grid(ws, grid: list[list]) -> None:
    if not grid:
        return
    n_rows = len(grid)
    n_cols = max(len(r) for r in grid)
    # Pad rows to equal width so gspread accepts the rectangular range
    padded = [list(r) + [""] * (n_cols - len(r)) for r in grid]
    range_name = f"A1:{chr(ord('A') + n_cols - 1)}{n_rows}"
    ws.update(range_name, padded, value_input_option="RAW")


def main() -> int:
    print("[nps] opening marketing sheet…", flush=True)
    sh = _open_marketing_sheet()

    print(f"[nps] reading {RAW_TAB}…", flush=True)
    responses = _load_responses(sh)
    print(f"[nps]   {len(responses)} survey responses with scores")

    print(f"[nps] building {MONTHLY_TAB}…", flush=True)
    monthly = _build_monthly(responses)
    ws_m = _get_or_create_tab(sh, MONTHLY_TAB,
                              rows=max(len(monthly) + 5, 50),
                              cols=max(len(monthly[0]) if monthly else 14, 14))
    _write_grid(ws_m, monthly)
    print(f"[nps]   {len(monthly) - 1} month rows written")

    print(f"[nps] building {WEEKLY_TAB}…", flush=True)
    weekly = _build_last_week(responses)
    ws_w = _get_or_create_tab(sh, WEEKLY_TAB,
                              rows=max(len(weekly) + 5, 30),
                              cols=max(len(weekly[0]) if weekly else 6, 6))
    _write_grid(ws_w, weekly)
    print(f"[nps]   last-week snapshot written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
