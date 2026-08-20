"""Write the phone -> patient index the inbound SMS webhook reads.

Cliniko cannot look a patient up by phone number (the API answers
"phone_number is not filterable" and there is no phone endpoint), so when a
patient replies to the launch text the server needs a prepared index to turn
+447… into a name. This builds it from the campaign list into the
"Omagh - Reply Index" tab, keyed on the last 9 digits so +447/07/00447 all
resolve to the same person.

Run this AFTER omagh_list.py and BEFORE the first send. Preview by default;
pass --write to touch the sheet.
"""

import csv
import glob
import os
import sys
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

def _latest(pattern):
    """Newest matching export. The pull and the send often happen on
    different days, so never assume today's date is in the filename."""
    hits = sorted(glob.glob(os.path.expanduser(pattern)))
    if not hits:
        raise SystemExit(f"no file matching {pattern} — run omagh_list.py first")
    return hits[-1]


SRC = _latest("~/Downloads/omagh_catchment_*.csv")
SPREADSHEET_ID = "1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI"
SERVICE_ACCOUNT_FILE = "service_account.json"
TAB = "Omagh - Reply Index"
HEADERS = ["phone", "name", "patient_id", "town"]


def norm_phone(raw):
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else ""


def build():
    rows, seen = [], set()
    for r in csv.DictReader(open(SRC)):
        key = norm_phone(r.get("Mobile"))
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append([key,
                     " ".join(x for x in (r.get("First name"),
                                          (r.get("Name") or "").split(" ")[-1])
                              if x).strip() or r.get("Name", ""),
                     r.get("Patient ID", ""),
                     r.get("Town", "")])
    rows.sort(key=lambda x: x[1])
    return rows


def main():
    rows = build()
    print(f"{len(rows)} unique numbers indexed from {SRC}")
    for r in rows[:5]:
        print("  ", r)

    if "--write" not in sys.argv:
        print("\npreview only — pass --write to update the sheet")
        return

    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sh = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(TAB)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB, rows=len(rows) + 10, cols=len(HEADERS))
    ws.update(range_name="A1", values=[HEADERS] + rows)
    print(f"wrote {len(rows)} rows to '{TAB}'")


if __name__ == "__main__":
    main()
