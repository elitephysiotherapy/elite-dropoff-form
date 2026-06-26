"""One-off: re-judge the 'New / Past Patient' column on existing booking rows
after the reactivation window changed (30 → 90 days, 2026-06-26).

The 6x/day trawl only ever *appends* rows — it never re-judges ones already in
the sheet. So widening REACTIVATION_WINDOW_DAYS only affects bookings going
forward. This script re-runs the SAME classification logic (bookings_fetch.
collect_bookings, which now uses the 90-day window) over every IA created since
--since, then updates any weekly-tab cell whose stored classification differs.

Usage:
  python reclassify_bookings.py                 dry run — prints what would change
  python reclassify_bookings.py --apply         write the changes + refresh Dashboard
  python reclassify_bookings.py --since 2026-05-01   override the start date
"""

import sys
from dotenv import load_dotenv
load_dotenv(override=True)

import bookings_fetch as bf

DEFAULT_SINCE = "2026-05-01"   # safely before the first weekly tab
NEW_OR_PAST_COL = bf.COLUMNS.index("new_or_past") + 1      # E
APPT_ID_COL = bf.COLUMNS.index("appointment_id") + 1       # M


def main():
    apply = "--apply" in sys.argv
    since = DEFAULT_SINCE
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]

    print(f"Re-classifying with window = {bf.REACTIVATION_WINDOW_DAYS} days, "
          f"IAs created since {since}…\n", flush=True)

    # Fresh classification for every IA created since `since` (skip_ids empty so
    # nothing is skipped — we want a verdict for every booking).
    rows = bf.collect_bookings(since_date=since, skip_ids=set())
    new_by_id = {r["appointment_id"]: r["new_or_past"] for r in rows}
    print(f"Got fresh verdicts for {len(new_by_id)} IA bookings.\n")

    sh = bf.open_spreadsheet()
    changes = []          # (ws, row_idx, patient, old, new)
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        values = ws.get_all_values()
        if not values:
            continue
        for i, row in enumerate(values[1:], start=2):   # skip header; 1-based
            if len(row) < APPT_ID_COL:
                continue
            appt_id = row[APPT_ID_COL - 1].strip()
            old = row[NEW_OR_PAST_COL - 1].strip()
            new = new_by_id.get(appt_id)
            if new and new != old:
                patient = row[bf.COLUMNS.index("patient")]
                changes.append((ws, i, patient, old, new))

    if not changes:
        print("No rows need re-classifying. Sheet already matches the new window.")
        return

    print(f"{'Tab':16}  {'Row':>3}  {'Patient':<26}  {'Was':<12} → New")
    print("-" * 78)
    for ws, i, patient, old, new in changes:
        print(f"{ws.title:16}  {i:>3}  {patient[:26]:<26}  {old:<12} → {new}")
    print(f"\n{len(changes)} row(s) would change.")

    if not apply:
        print("\n(Dry run — re-run with --apply to write these + refresh the Dashboard.)")
        return

    print("\nApplying…")
    for ws, i, patient, old, new in changes:
        ws.update_cell(i, NEW_OR_PAST_COL, new)
        print(f"  {ws.title} row {i}: {old} → {new}")
    print("Refreshing Dashboard…")
    bf.write_dashboard(sh)
    print("Done.")


if __name__ == "__main__":
    main()
