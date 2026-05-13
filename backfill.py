"""One-off backfill: walks a date range, collects drop-offs each day,
enriches with Phase 2, writes to Sheet, then refreshes summary tabs.

Usage:
  ./venv/bin/python backfill.py START_DATE END_DATE
  e.g. ./venv/bin/python backfill.py 2026-04-13 2026-05-09

Both bounds inclusive, Europe/London calendar days.
"""
import sys
from datetime import date, timedelta
import phase1_fetch as p1


def main():
    if len(sys.argv) != 3:
        print("Usage: python backfill.py YYYY-MM-DD YYYY-MM-DD")
        sys.exit(1)

    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])
    n_days = (end - start).days + 1
    print(f"Backfill {start} → {end} ({n_days} days)")

    all_rows = []
    all_excluded = []
    day = start
    while day <= end:
        day_str = day.strftime("%Y-%m-%d")
        rows, excluded = p1.collect_dropoffs(date_override=day_str)
        if rows:
            print(f"  {day_str}: {len(rows)} drop-offs — enriching…")
            p1.enrich_phase2(rows)
        else:
            print(f"  {day_str}: 0 drop-offs")
        all_rows.extend(rows)
        all_excluded.extend(excluded)
        day += timedelta(days=1)

    print(f"\nTotal drop-offs: {len(all_rows)}  |  Excluded as reschedules: {len(all_excluded)}")

    # Group counts
    by_type = {}
    for r in all_rows:
        by_type[r["dropoff_type"]] = by_type.get(r["dropoff_type"], 0) + 1
    print("By type: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    if not all_rows:
        print("Nothing to write. Exiting.")
        return

    print("\nWriting drop-off rows to Google Sheet…")
    p1.write_to_sheet(all_rows)
    print("Refreshing IA Rebook Rate tab…")
    p1.write_ia_rebook_rate_tab()
    print("Refreshing Monthly Summary tab…")
    p1.write_monthly_summary_tab()
    print("Backfill complete.")


if __name__ == "__main__":
    main()
