"""Reactivations by CALENDAR MONTH — true monthly totals using the canonical
engine (reactivations.py), the same definition behind the weekly Sinead DM and
the EOD number. Writes the 'Reactivations by Month' tab on the Performance sheet.

WHY THIS EXISTS
===============
The Bookings sheet Dashboard has a "Reactivations" column, but it only counts
reactivations whose RETURN booking was a new IA (because that sheet only holds
IA bookings). Most reactivations rebook a follow-up/review, so that figure is a
big undercount (June 2026: 9 there vs 40 true). This tab is the correct total.

A reactivation is counted ONCE, in the month its rebooking was CREATED. Because
created_at is fixed in the past, a settled month's total does not change — so the
daily refresh only recomputes the CURRENT and PREVIOUS month and preserves the
older rows already on the sheet. Use --backfill N to (re)compute N months.

Run:
    python reactivations_by_month.py --refresh        # current + previous month
    python reactivations_by_month.py --backfill 6     # last 6 months from scratch
    python reactivations_by_month.py                  # same as --refresh (preview)
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import gspread

import config
import eod_stats
import phase1_fetch as pf
import phase2
import reactivations

LONDON = ZoneInfo("Europe/London")
TAB_NAME = "Reactivations by Month"


def month_key(dt):
    return dt.strftime("%Y-%m")


def month_bounds(year, month):
    """(start_local, end_local) spanning the whole calendar month in London time."""
    start = datetime(year, month, 1, tzinfo=LONDON)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=LONDON)
    else:
        end = datetime(year, month + 1, 1, tzinfo=LONDON)
    return start, end


def last_n_month_keys(now, n):
    """['2026-07', '2026-06', ...] most-recent first, length n."""
    y, m = now.year, now.month
    keys = []
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return keys


def collect_month(start_local, end_local):
    """Return {'total', 'ia', 'nonia'} for reactivations whose rebooking was
    created in [start, end). 'ia' = return booking is a real IA (≈ the Bookings
    dashboard figure); 'nonia' = follow-up / review / one-off return bookings."""
    s_iso, e_iso = eod_stats._iso(start_local), eod_stats._iso(end_local)
    now = datetime.now(LONDON)

    # Candidates = everyone who created an appointment in the window (include
    # cancelled — Cliniko's default list hides them — so a later-cancelled
    # reactivation booking isn't silently missed).
    created = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={s_iso}"), ("q[]", f"created_at:<{e_iso}")]))
    created += list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={s_iso}"), ("q[]", f"created_at:<{e_iso}"),
        ("q[]", "cancelled_at:?")]))
    cand_pids = {phase2.id_from_link(a.get("patient")) for a in created}
    cand_pids.discard(None)

    total = ia = nonia = 0
    for pid in cand_pids:
        hist = phase2.fetch_patient_full_history(pid)
        for r in reactivations.reactivations_in_window(hist, start_local, end_local, now):
            total += 1
            if reactivations._is_ia(r["rebook"]):
                ia += 1
            else:
                nonia += 1
    return {"total": total, "ia": ia, "nonia": nonia}


def read_existing(sh):
    """{'YYYY-MM': {'total','ia','nonia'}} from the current tab, if it exists."""
    out = {}
    try:
        ws = sh.worksheet(TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return out
    for row in ws.get_all_values():
        label = (row[0] if row else "").strip()
        try:
            key = month_key(datetime.strptime(label, "%B %Y"))
        except ValueError:
            continue  # header / title / blank rows
        try:
            out[key] = {"total": int(row[1]), "ia": int(row[2]), "nonia": int(row[3])}
        except (IndexError, ValueError):
            continue
    return out


def write_tab(sh, data):
    """data = {'YYYY-MM': {...}}. Writes newest-first."""
    header = ["Month", "Reactivations", "of which IA rebooks",
              "of which Non-IA rebooks"]
    rows = []
    for key in sorted(data, reverse=True):
        d = data[key]
        label = datetime.strptime(key + "-01", "%Y-%m-%d").strftime("%B %Y")
        rows.append([label, d["total"], d["ia"], d["nonia"]])

    try:
        ws = sh.worksheet(TAB_NAME)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_NAME, rows=max(60, len(rows) + 10),
                              cols=len(header))

    stamp = datetime.now(LONDON).strftime("%Y-%m-%d %H:%M %Z")
    title = ("Reactivations performed per calendar month — canonical definition "
             "(a drop-off who rebooked any appointment, counted once in the month "
             "they rebooked). Same engine as the weekly Sinead DM. "
             f"Updated {stamp}.")
    ws.update([[title]], "A1")
    ws.update([header] + rows, "A3")
    ws.format("A3:D3", {"textFormat": {"bold": True}})
    return ws


def main():
    args = sys.argv[1:]
    backfill_n = None
    if "--backfill" in args:
        i = args.index("--backfill")
        backfill_n = int(args[i + 1])

    now = datetime.now(LONDON)
    sh = pf.open_spreadsheet()
    data = read_existing(sh)

    if backfill_n:
        keys = last_n_month_keys(now, backfill_n)
        print(f"Backfilling {backfill_n} months: {', '.join(reversed(keys))}")
    else:
        # Daily refresh: current month + previous month only.
        keys = last_n_month_keys(now, 2)
        print(f"Refreshing current + previous month: {', '.join(reversed(keys))}")

    for key in keys:
        y, m = int(key[:4]), int(key[5:])
        start, end = month_bounds(y, m)
        print(f"  computing {key} …", flush=True)
        data[key] = collect_month(start, end)
        d = data[key]
        print(f"    {key}: {d['total']} reactivations "
              f"({d['ia']} IA, {d['nonia']} non-IA)")

    write_tab(sh, data)
    print(f"\nWrote '{TAB_NAME}' ({len(data)} months). Sheet: {sh.url}")


if __name__ == "__main__":
    main()
