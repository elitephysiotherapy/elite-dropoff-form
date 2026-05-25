"""Google Sheets access for the marketing module.

Opens the "Elite Physio — NPS & Marketing" spreadsheet (separate from the
drop-off sheet). Works both locally (poller — uses service_account.json) and
on Render (webhook — uses the SERVICE_ACCOUNT_JSON env var).
"""

import json
import os

import gspread
from google.oauth2.service_account import Credentials

import config

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_marketing_ss = None


def _credentials():
    """Service-account creds — env var first (Render), local file fallback (poller)."""
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)
    path = os.path.join(_ROOT, "service_account.json")
    if not os.path.exists(path):
        raise RuntimeError(
            "No Google credentials — set SERVICE_ACCOUNT_JSON or place "
            "service_account.json in the project root.")
    return Credentials.from_service_account_file(path, scopes=_SCOPES)


def marketing_sheet():
    """Return the opened NPS & Marketing spreadsheet (cached)."""
    global _marketing_ss
    if _marketing_ss is None:
        if not config.MARKETING_SPREADSHEET_ID:
            raise RuntimeError(
                "config.MARKETING_SPREADSHEET_ID is not set — run "
                "nps_sheet_setup.gs and paste the sheet ID into config.py")
        client = gspread.authorize(_credentials())
        _marketing_ss = client.open_by_key(config.MARKETING_SPREADSHEET_ID)
    return _marketing_ss


def tab(name):
    """Return a worksheet by tab name from the NPS & Marketing sheet."""
    return marketing_sheet().worksheet(name)
