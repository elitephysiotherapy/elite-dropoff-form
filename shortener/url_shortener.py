"""SMS URL shortener — turns a 400-char Tally URL into a 40-char redirect.

Storage: Google Sheets tab "sms_shortener" on the marketing spreadsheet
(MARKETING_SPREADSHEET_ID env var). Columns:
  A=token, B=long_url, C=created_at, D=clicks, E=last_click_at, F=label

In-memory cache: tokens loaded lazily on first redirect lookup and kept
hot in process memory. The Render starter plan runs a single instance, so
cache coherence isn't a concern.

Public API:
  make_short_url(long_url, label="")  -> str   # marketing poller calls this
  lookup(token)                       -> str|None  # /r/<token> calls this

No threads are spawned anywhere in the request path — earlier prototype
used a background thread for click-tracking and that blocked the single
sync gunicorn worker. Analytics live in Twilio + Tally instead.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

TAB_NAME = "sms_shortener"
_TOKEN_BYTES = 4  # → 6-char URL-safe string, ~68B possible tokens
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_ROOT = Path(__file__).resolve().parent

# In-process cache.
_cache: dict[str, str] = {}
_cache_loaded = False
_cache_lock = threading.Lock()

# Cached gspread Spreadsheet handle.
_ss = None
_ss_lock = threading.Lock()


def _credentials():
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)
    path = _ROOT / "service_account.json"
    if not path.exists():
        # Try parent dir (handy when running locally from cliniko-dropoffs)
        path = _ROOT.parent / "service_account.json"
    if not path.exists():
        raise RuntimeError("No Google credentials — set SERVICE_ACCOUNT_JSON "
                           "env var or place service_account.json in the project root.")
    return Credentials.from_service_account_file(str(path), scopes=_SCOPES)


def _spreadsheet():
    global _ss
    if _ss is None:
        with _ss_lock:
            if _ss is None:
                sheet_id = os.environ.get("MARKETING_SPREADSHEET_ID")
                if not sheet_id:
                    raise RuntimeError("MARKETING_SPREADSHEET_ID env var not set.")
                client = gspread.authorize(_credentials())
                _ss = client.open_by_key(sheet_id)
    return _ss


def _get_tab():
    ss = _spreadsheet()
    try:
        return ss.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=TAB_NAME, rows=2000, cols=6)
        ws.update("A1:F1", [["token", "long_url", "created_at",
                              "clicks", "last_click_at", "label"]])
        return ws


def _now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load_cache_once() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    with _cache_lock:
        if _cache_loaded:
            return
        try:
            ws = _get_tab()
            rows = ws.get_all_values()
        except Exception as e:  # noqa: BLE001
            print(f"[url_shortener] cache load failed: {e}", flush=True)
            _cache_loaded = True
            return
        for r in rows[1:]:
            if len(r) >= 2 and r[0] and r[1]:
                _cache[r[0]] = r[1]
        _cache_loaded = True
        print(f"[url_shortener] cache loaded with {len(_cache)} tokens", flush=True)


def _generate_token() -> str:
    for _ in range(8):
        t = secrets.token_urlsafe(_TOKEN_BYTES)[:6]
        if t not in _cache:
            return t
    raise RuntimeError("token-generation collision storm — cache likely huge")


def make_short_url(long_url: str, label: str = "") -> str:
    """Persist a token for long_url and return https://<host>/r/<token>.

    On any error, returns the original long_url so SMS sending never breaks.
    Reads SHORTENER_BASE_URL from the env at call time — caller (marketing
    poller) must have that set to the elite-sms-shortener service URL.
    """
    if not long_url:
        return long_url
    base = os.environ.get("SHORTENER_BASE_URL", "").rstrip("/")
    if not base:
        print("[url_shortener] SHORTENER_BASE_URL not set; returning long URL.",
              flush=True)
        return long_url
    _load_cache_once()
    try:
        token = _generate_token()
        _get_tab().append_row(
            [token, long_url, _now_iso(), 0, "", label],
            value_input_option="RAW",
        )
        _cache[token] = long_url
        return f"{base}/r/{token}"
    except Exception as e:  # noqa: BLE001
        print(f"[url_shortener] shorten FAILED, falling back to long URL: {e}",
              flush=True)
        return long_url


def lookup(token: str) -> Optional[str]:
    if not token:
        return None
    _load_cache_once()
    hit = _cache.get(token)
    if hit:
        return hit
    # Cache miss — token may have been written by a different process.
    try:
        ws = _get_tab()
        for r in ws.get_all_values()[1:]:
            if len(r) >= 2 and r[0] == token:
                _cache[token] = r[1]
                return r[1]
    except Exception as e:  # noqa: BLE001
        print(f"[url_shortener] miss-time reload failed: {e}", flush=True)
    return None
