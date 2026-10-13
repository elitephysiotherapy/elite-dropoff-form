"""Head Physio Weekly tab — a copy of the Go-To Clinic Hub's "Head Physio
Dashboard" weekly table, filled from Cliniko so the Hub can be typed in from it.

Layout mirrors the Hub exactly (Martin 2026-10-13): one block per Hub period,
newest at the TOP, physios in the Hub's order, the same 12 columns:

    Metric | Appts | IAs | Rebooked | IA Rebook % | Packages | Package % |
    CDNR | DNAs | Drop Off % | Available Minutes | Treating Minutes | Utilisation

HUB PERIODS ≠ CALENDAR WEEKS. The Hub cuts Mon–Sun weeks at month ends, so
28 Sep – 4 Oct 2026 is TWO periods: "September - Week 5" (28–30 Sep) and
"October - Week 1" (1–4 Oct). Week 1 of a month runs from the 1st to the first
Sunday. hub_periods() reproduces that so every block maps 1:1 to a Hub page.

DEFINITIONS — the same as the rest of the drop-off sheet (Performance
Dashboard / Monday team email), so nothing disagrees:
  Appts     = attended 1-to-1 appointments (classes excluded)        total_apts
  IAs       = attended IAs (wider NP set, as the Performance Dashboard) nps
  Rebooked  = IAs − IADNRs   → Hub's IA Rebook % = Rebooked ÷ IAs, which is
              exactly the Dashboard's (NPs − IADNRs) ÷ NPs
  Packages  = new package-of-care sales posted in #packages, @-mentioned physio
              (send_packages_weekly rules)
  CDNR      = CNA drop-offs (cancelled, no rebook, deduped per patient)
  DNAs      = DNA drop-offs (deduped per patient)
  Available = contracted weekly hours (monthly ÷ 4.345) × 60 × days available
              ÷ working days per week, where days available = the physio's
              working days (config.TEAM "work_days", default Mon–Fri) minus annual
              leave (config.TEAM "leave", day-level) and clinic closures
              (config.CLINIC_CLOSED_DAYS)
  Treating  = non-cancelled appointment minutes incl. classes (the utilisation
              numerator everywhere else)
The Hub computes its four % columns itself (verified by test entry 2026-10-13):
  IA Rebook % = Rebooked ÷ IAs        Package % = Packages ÷ Rebooked
  Drop Off %  = (CDNR + DNAs) ÷ Appts Utilisation = Treating ÷ Available
The sheet uses the same formulas so the two read identically. NOTE the Hub's
Drop Off % is NOT the Monday email's rate (drops ÷ (drops + reviews), with
IADNRs in the drops) — the IADNRs show up in the Hub as IAs − Rebooked instead.

Blocks are frozen once written: the run only ADDS periods that have finished and
aren't on the tab yet, then re-sorts newest-first. Old blocks are carried over
verbatim from the sheet (episode allocation is retrospective — recomputing them
would drift away from what was typed into the Hub).

Column M of each block's title row tracks Hub entry:
  "Hub: not entered"  → the weekly Chrome task fills it, then runs --mark-entered

Usage:
  python head_physio_weekly.py                 add any finished, missing periods (last 14 days)
  python head_physio_weekly.py --since 2026-09-01   backfill from a date
  python head_physio_weekly.py --dry-run       compute + print, don't write
  python head_physio_weekly.py --since 2026-01-01 --rebuild   recompute not-yet-entered blocks
  python head_physio_weekly.py --pending-json  print blocks still to type into the Hub
  python head_physio_weekly.py --mark-entered "October - Week 2"
"""

import calendar
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv(override=True)

import config

TAB = "Head Physio Weekly"
LOOKBACK_DAYS = 14          # self-heal window: a missed run is caught next day

HEADERS = ["Metric", "Appts", "IAs", "Rebooked", "IA Rebook %", "Packages",
           "Package %", "CDNR", "DNAs", "Drop Off %", "Available Minutes",
           "Treating Minutes", "Utilisation"]
# 0-based columns typed into the Hub (the yellow cells) vs the Hub's own % cells.
INPUT_COLS = [1, 2, 3, 5, 7, 8, 10, 11]
PCT_COLS = [4, 6, 9, 12]

TITLE_RE = re.compile(r"^(January|February|March|April|May|June|July|August|"
                      r"September|October|November|December) - Week \d$")
HUB_NOT_ENTERED = "Hub: not entered"
STATUS_COL = 12            # column M of each block's title row

NAVY = {"red": 0.11, "green": 0.18, "blue": 0.33}
YELLOW = {"red": 1.0, "green": 0.97, "blue": 0.80}
GREY = {"red": 0.95, "green": 0.95, "blue": 0.95}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


# ---------------------------------------------------------------------------
# Hub periods
# ---------------------------------------------------------------------------

def _ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def hub_periods_in_month(year, month):
    """[(title, start_date, end_date_inclusive)] — Mon–Sun weeks clipped to the month."""
    last = calendar.monthrange(year, month)[1]
    out, d, n = [], date(year, month, 1), 1
    while d.month == month:
        end = min(d + timedelta(days=6 - d.weekday()), date(year, month, last))
        out.append((f"{calendar.month_name[month]} - Week {n}", d, end))
        d, n = end + timedelta(days=1), n + 1
    return out


def hub_period_for(day):
    for p in hub_periods_in_month(day.year, day.month):
        if p[1] <= day <= p[2]:
            return p


def hub_date_range(start, end):
    """The Hub's subtitle, e.g. '12th - 18th October 2026'."""
    return (f"{_ordinal(start.day)} - {_ordinal(end.day)} "
            f"{calendar.month_name[end.month]} {end.year}")


def finished_periods(since, today):
    """Hub periods that started on/after `since`'s period and ended before today."""
    out, d = [], hub_period_for(since)[1]
    while True:
        p = hub_period_for(d)
        if p[2] >= today:
            break
        out.append(p)
        d = p[2] + timedelta(days=1)
    return out


def _contracted_monthly_hours(name, day):
    """Contracted monthly hours on `day` (hours_periods-aware), ignoring leave —
    leave is taken off day by day via config.available_weekdays."""
    m = next((t for t in config.TEAM if t["display"] == name), None)
    if not m:
        return None
    for hp in m.get("hours_periods") or []:
        if config._as_date(hp["from"]) <= day <= config._as_date(hp["to"]):
            return hp["monthly_hours"]
    return m.get("monthly_hours")


# ---------------------------------------------------------------------------
# Compute one period
# ---------------------------------------------------------------------------

def _pct(num, den):
    return f"{round(num / den * 100)}%" if den else "0%"


def compute_period(period, packages_by_physio=None):
    """Rows (physios in Hub order + Clinic Total) for one Hub period."""
    import phase2 as p2
    from phase1_fetch import LONDON
    _title, start, end = period
    s_local = datetime(start.year, start.month, start.day, tzinfo=LONDON)
    e_local = datetime(end.year, end.month, end.day, tzinfo=LONDON) + timedelta(days=1)
    stats = p2._monthly_stats_per_physio_uncached(
        s_local.astimezone(timezone.utc), e_local.astimezone(timezone.utc))
    if packages_by_physio is None:
        packages_by_physio = count_packages(s_local, e_local)

    unknown = sorted(d for d, s in stats.items()
                     if d not in config.HUB_PHYSIO_ORDER and s.get("total_apts"))
    if unknown:
        print(f"  NOTE {period[0]}: not on the Hub list, left out: {', '.join(unknown)}")

    rows, tot = [], [0] * 13
    for name in config.HUB_PHYSIO_ORDER:
        s = stats.get(name) or {}
        ias = s.get("nps", 0)
        rebooked = max(ias - s.get("iadnrs", 0), 0)
        monthly = _contracted_monthly_hours(name, start)
        days = config.available_weekdays(name, start, end)
        vals = {
            1: s.get("total_apts", 0), 2: ias, 3: rebooked,
            5: packages_by_physio.get(name, 0),
            7: s.get("cnas_review", 0), 8: s.get("dnas_review", 0),
            10: round(monthly / 4.345 * 60 * days / len(config.work_days(name))) if monthly else 0,
            11: round(s.get("used_minutes", 0)),
        }
        rows.append(_row(name, vals))
        for c, v in vals.items():
            tot[c] += v
    rows.append(_row("Clinic Total", {c: tot[c] for c in INPUT_COLS}))
    return rows


def _row(name, v):
    return [name, v[1], v[2], v[3], _pct(v[3], v[2]), v[5], _pct(v[5], v[3]),
            v[7], v[8], _pct(v[7] + v[8], v[1]), v[10], v[11], _pct(v[11], v[10])]


def count_packages(s_local, e_local):
    """Per-physio new package sales posted in #packages during the window."""
    import send_packages_weekly as spw
    try:
        msgs = spw.fetch_week_messages(s_local, e_local)
        per, _total, _ex = spw.count_packages(msgs, spw._physio_by_userid())
    except Exception as e:
        print(f"  WARN packages count failed ({e}) — Packages left at 0")
        return {}
    if per.get("Unattributed"):
        print(f"  NOTE {per['Unattributed']} package post(s) had no physio @-mention")
    return dict(per)


# ---------------------------------------------------------------------------
# Sheet read / write
# ---------------------------------------------------------------------------

def parse_blocks(values):
    """{title: [title_row, header_row, *data_rows]} from the tab's current values."""
    blocks, cur = {}, None
    for r in values:
        a = (r[0] if r else "").strip()
        if TITLE_RE.match(a):
            cur = blocks.setdefault(a + "|" + (r[1] if len(r) > 1 else ""), [list(r)])
            continue
        if cur is None:
            continue
        if not a:
            cur = None
            continue
        cur.append(list(r))
    return blocks


def _block_start(key):
    title, rng = key.split("|", 1)
    m = re.match(r"(\d+)\w* - \d+\w* (\w+) (\d{4})", rng)
    month = list(calendar.month_name).index(title.split(" - ")[0])
    return date(int(m.group(3)), month, int(m.group(1)))


def build_grid(blocks, now):
    out = [["Head Physio Weekly"],
           [f"Mirrors the Go-To Clinic Hub ▸ Head Physio Dashboard ▸ Weekly Data. "
            f"Newest period on top. Last updated {now:%d %b %Y %H:%M}."],
           ["Yellow = typed into the Hub. Grey % = the Hub's own formulas: "
            "IA Rebook = Rebooked ÷ IAs · Package = Packages ÷ Rebooked · "
            "Drop Off = (CDNR + DNAs) ÷ Appts · Utilisation = Treating ÷ Available."],
           ["Rebooked = IAs − IADNRs · CDNR / DNAs = CNA / DNA drop-offs · "
            "Packages = #packages posts · Available = weekly hours × own working days available ÷ days/week "
            "(minus annual leave + bank-holiday closures)."],
           []]
    for key in sorted(blocks, key=_block_start, reverse=True):
        out.extend(blocks[key])
        out.append([])
    return out


def _format(ws, grid):
    from bookings_fetch import _gs_retry
    n = len(grid) + 5
    fmt = [{"range": f"A1:M{n}", "format": {
        "backgroundColor": WHITE, "horizontalAlignment": "CENTER",
        "textFormat": {"bold": False, "foregroundColor": {"red": 0, "green": 0, "blue": 0}}}},
        {"range": "A1:M5", "format": {"horizontalAlignment": "LEFT"}},
        {"range": "A1", "format": {"textFormat": {"bold": True, "fontSize": 14}}}]
    for i, r in enumerate(grid, start=1):
        a = r[0] if r else ""
        if TITLE_RE.match(a):
            fmt.append({"range": f"A{i}:M{i}", "format": {
                "horizontalAlignment": "LEFT", "textFormat": {"bold": True}}})
            fmt.append({"range": f"M{i}", "format": {"horizontalAlignment": "RIGHT"}})
        elif a == "Metric":
            fmt.append({"range": f"A{i}:M{i}", "format": {
                "backgroundColor": NAVY,
                "textFormat": {"bold": True, "foregroundColor": WHITE}}})
        elif a and i > 5:
            bold = a == "Clinic Total"
            fmt.append({"range": f"A{i}", "format": {
                "horizontalAlignment": "LEFT", "textFormat": {"bold": True}}})
            for c in INPUT_COLS:
                col = chr(ord("A") + c)
                fmt.append({"range": f"{col}{i}", "format": {
                    "backgroundColor": GREY if bold else YELLOW,
                    "textFormat": {"bold": bold}}})
            for c in PCT_COLS:
                col = chr(ord("A") + c)
                fmt.append({"range": f"{col}{i}", "format": {"textFormat": {"bold": True}}})
    _gs_retry(lambda: ws.batch_format(fmt), f"{TAB} format")
    widths = [(0, 1, 150), (1, 10, 90), (10, 12, 125), (12, 13, 90)]
    _gs_retry(lambda: ws.spreadsheet.batch_update({"requests": [
        {"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                      "startIndex": a, "endIndex": b},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}}
        for a, b, px in widths]}), f"{TAB} widths")


def _read_tab(sh):
    import gspread
    from bookings_fetch import _gs_retry
    try:
        ws = sh.worksheet(TAB)
    except gspread.exceptions.WorksheetNotFound:
        return None, []
    return ws, _gs_retry(ws.get_all_values, f"{TAB} read")


def write_head_physio_weekly_tab(since=None, dry_run=False, rebuild=False):
    """Add finished Hub periods missing from the tab. rebuild=True also
    recomputes blocks still "Hub: not entered" (entered ones stay frozen) —
    use after correcting leave/roster data."""
    from phase1_fetch import LONDON, open_spreadsheet, write_tab_atomically
    now = datetime.now(LONDON)
    today = now.date()
    since = since or (today - timedelta(days=LOOKBACK_DAYS))

    sh = None if dry_run else open_spreadsheet()
    _ws, values = (None, []) if dry_run else _read_tab(sh)
    blocks = parse_blocks(values)
    if rebuild:
        blocks = {k: v for k, v in blocks.items()
                  if not (len(v[0]) > STATUS_COL and v[0][STATUS_COL] == HUB_NOT_ENTERED)}

    added = []
    for p in finished_periods(since, today):
        key = p[0] + "|" + hub_date_range(p[1], p[2])
        if key in blocks:
            continue
        print(f"  computing {key.replace('|', ' — ')}…")
        rows = compute_period(p)
        blocks[key] = [[p[0], hub_date_range(p[1], p[2])] + [""] * 10 + [HUB_NOT_ENTERED],
                       HEADERS] + rows
        added.append(key)
        if dry_run:
            for r in blocks[key]:
                print("   ", r)

    if not added:
        print("  Head Physio Weekly: nothing new")
        return
    if dry_run:
        return
    grid = build_grid(blocks, now)
    ws = write_tab_atomically(sh, TAB, grid, rows=max(400, len(grid) + 50), cols=13)
    _format(ws, grid)
    print(f"  Head Physio Weekly: added {len(added)} period(s): "
          + ", ".join(k.split("|")[0] for k in added))


# ---------------------------------------------------------------------------
# Hand-off to the weekly Chrome task
# ---------------------------------------------------------------------------

def pending_blocks():
    from phase1_fetch import open_spreadsheet
    _ws, values = _read_tab(open_spreadsheet())
    out = []
    for key, rows in parse_blocks(values).items():
        if len(rows[0]) > STATUS_COL and rows[0][STATUS_COL] == HUB_NOT_ENTERED:
            title, rng = key.split("|", 1)
            out.append({"title": title, "date_range": rng,
                        "start": _block_start(key).isoformat(),
                        "columns": rows[1],
                        "rows": [r for r in rows[2:] if r[0] != "Clinic Total"],
                        "clinic_total": next((r for r in rows[2:] if r[0] == "Clinic Total"), None)})
    return sorted(out, key=lambda b: b["start"])


def mark_entered(title):
    from bookings_fetch import _gs_retry
    from phase1_fetch import LONDON, open_spreadsheet
    ws, values = _read_tab(open_spreadsheet())
    for i, r in enumerate(values, start=1):
        if r and r[0].strip() == title and len(r) > STATUS_COL and r[STATUS_COL] == HUB_NOT_ENTERED:
            stamp = f"Hub: entered {datetime.now(LONDON):%d %b %Y %H:%M}"
            _gs_retry(lambda: ws.update_acell(f"M{i}", stamp), f"{TAB} mark")
            print(f"  {title}: {stamp}")
            return True
    print(f"  {title}: no 'not entered' block found")
    return False


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--pending-json" in args:
        print(json.dumps(pending_blocks(), indent=1))
    elif "--mark-entered" in args:
        sys.exit(0 if mark_entered(args[args.index("--mark-entered") + 1]) else 1)
    else:
        since = None
        if "--since" in args:
            since = date.fromisoformat(args[args.index("--since") + 1])
        write_head_physio_weekly_tab(since=since, dry_run="--dry-run" in args,
                                     rebuild="--rebuild" in args)
