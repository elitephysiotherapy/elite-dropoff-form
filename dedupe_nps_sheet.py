#!/usr/bin/env python3
"""One-off cleanup: remove duplicate rows from the NPS - Raw Data sheet.

Some NPS responses were recorded twice (a Tally webhook retry / patient
double-submit with no idempotency guard), which skewed individual physios'
average scores up or down. This script de-duplicates the sheet so the
per-physio NPS aggregations are correct.

Dedup key (per row):
  - Tally Response ID (column N) when present — the stable per-submission id.
  - Otherwise a composite of Patient ID + Trigger Type + Score + Date Sent,
    which collapses a same-survey retry without merging genuinely different
    surveys for the same patient.

The FIRST occurrence of each key is kept; every later duplicate is deleted.

Usage:
    python dedupe_nps_sheet.py            # preview only — lists what it WOULD delete
    python dedupe_nps_sheet.py --apply    # actually delete the duplicate rows

Safe to re-run: once clean, a second run finds nothing to delete.
"""
import sys

from marketing.sheets import tab

_RAW = "NPS - Raw Data"
_RESPONSE_ID_COL = 14          # column N (1-based)
_HEADER = "Response ID"


def _row_key(row):
    """Return (kind, key) identifying this response. kind is just for the report."""
    rid = row[_RESPONSE_ID_COL - 1].strip() if len(row) >= _RESPONSE_ID_COL else ""
    if rid:
        return ("response_id", rid)
    date = row[0].strip() if len(row) > 0 else ""
    pid = row[1].strip() if len(row) > 1 else ""
    trig = row[5].strip() if len(row) > 5 else ""
    score = row[6].strip() if len(row) > 6 else ""
    return ("composite", f"{pid}|{trig}|{score}|{date}")


def _describe(row):
    name = row[2] if len(row) > 2 else ""
    physio = row[3] if len(row) > 3 else ""
    score = row[6] if len(row) > 6 else ""
    date = row[0] if len(row) > 0 else ""
    return f"{date}  {name:<24}  physio={physio:<10}  score={score}"


def main():
    apply = "--apply" in sys.argv
    ws = tab(_RAW)
    rows = ws.get_all_values()
    if not rows:
        print("NPS - Raw Data is empty — nothing to do.")
        return

    header = rows[0]
    # Make sure the Response ID header exists (older sheets predate column N).
    if len(header) < _RESPONSE_ID_COL or header[_RESPONSE_ID_COL - 1].strip() != _HEADER:
        print(f"Note: column N header is not '{_HEADER}'.")
        if apply:
            ws.update_cell(1, _RESPONSE_ID_COL, _HEADER)
            print(f"  → set column N header to '{_HEADER}'.")
        else:
            print(f"  → would set column N header to '{_HEADER}' (with --apply).")

    seen = {}
    dupes = []                                  # list of (sheet_row_number, row, key)
    for i, row in enumerate(rows[1:], start=2):  # sheet rows are 1-based; row 1 = header
        if not any(c.strip() for c in row):     # skip wholly blank rows
            continue
        kind, key = _row_key(row)
        if key in seen:
            dupes.append((i, row, key))
        else:
            seen[key] = i

    total = len([r for r in rows[1:] if any(c.strip() for c in r)])
    print(f"\nScanned {total} data rows — {len(seen)} unique responses, "
          f"{len(dupes)} duplicate row(s).\n")

    if not dupes:
        print("No duplicates found. Sheet is clean. ✅")
        return

    print("Duplicate rows (these would be DELETED, keeping the first occurrence):")
    for sheet_row, row, key in dupes:
        print(f"  row {sheet_row:>4}: {_describe(row)}   [key {key}]")

    if not apply:
        print(f"\nPreview only — re-run with --apply to delete these "
              f"{len(dupes)} duplicate row(s).")
        return

    # Delete from the bottom up so earlier row numbers stay valid as we go.
    print(f"\nDeleting {len(dupes)} duplicate row(s)…")
    for sheet_row, _row, _key in sorted(dupes, key=lambda d: d[0], reverse=True):
        ws.delete_rows(sheet_row)
    print("Done. ✅  Physio NPS averages will now reflect de-duplicated scores.")


if __name__ == "__main__":
    main()
