"""SMS URL shortener — write side (called from the marketing poller).

Writes the long Tally URL into the sms_shortener tab on the marketing Sheet
and returns a https://elite-sms-shortener.onrender.com/r/<token> URL that
the standalone elite-sms-shortener Render web service knows how to resolve.

Why split: keeps redirect responsibility on a tiny isolated web service
(see ../shortener/) so its uptime can't take down /slack/interactive or
/tally/webhook on elite-dropoff-form.

Public API:
  make_short_url(long_url, label="")  -> str

On any failure (Sheets outage, missing env var, etc.) returns the original
long_url so SMS sending never breaks because of shortener infra.
"""

from __future__ import annotations

import datetime as _dt
import os
import secrets

import config
from marketing import sheets

TAB_NAME = "sms_shortener"
_TOKEN_BYTES = 4  # → 6-char URL-safe token


def _now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _get_tab():
    ss = sheets.marketing_sheet()
    try:
        return ss.worksheet(TAB_NAME)
    except Exception:  # noqa: BLE001 (gspread.WorksheetNotFound)
        ws = ss.add_worksheet(title=TAB_NAME, rows=2000, cols=6)
        ws.update("A1:F1", [["token", "long_url", "created_at",
                              "clicks", "last_click_at", "label"]])
        return ws


def _base_url() -> str:
    # Prefer env var; fall back to config constant (set in config.py).
    return (os.environ.get("SHORTENER_BASE_URL")
            or getattr(config, "SHORTENER_BASE_URL", "")).rstrip("/")


def make_short_url(long_url: str, label: str = "") -> str:
    if not long_url:
        return long_url
    base = _base_url()
    if not base:
        print("[url_shortener] SHORTENER_BASE_URL not set; returning long URL.",
              flush=True)
        return long_url
    try:
        token = secrets.token_urlsafe(_TOKEN_BYTES)[:6]
        _get_tab().append_row(
            [token, long_url, _now_iso(), 0, "", label],
            value_input_option="RAW",
        )
        return f"{base}/r/{token}"
    except Exception as e:  # noqa: BLE001
        print(f"[url_shortener] shorten FAILED, falling back to long URL: {e}",
              flush=True)
        return long_url
