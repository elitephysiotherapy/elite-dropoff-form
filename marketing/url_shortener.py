"""SMS URL shortener — turns a 400-char Tally URL into a 40-char redirect.

Why this exists: Twilio bills per 160-char SMS segment. Including the full
Tally URL with all the hidden-field params (patient_name, email, phone,
physio_name, clinic_name, google_review_url, etc.) pushes NPS messages to
~480 chars = 4 segments. Routing through this shortener brings them under
160 chars = 1 segment, cutting SMS spend ~70%.

Storage: Google Sheets tab "sms_shortener" on the marketing spreadsheet.
  Columns: A=token, B=long_url, C=created_at, D=clicks, E=last_click_at, F=label

In-memory cache: tokens loaded lazily on first redirect lookup and kept hot
in process memory. The Render starter plan runs a single instance so cache
coherence isn't a concern.

Public API:
  make_short_url(long_url, label="")  -> str   # called when SMS is composed
  lookup(token)                       -> str|None  # called on /r/<token>
  record_click(token)                 -> None      # fire-and-forget
"""

from __future__ import annotations

import datetime as _dt
import secrets
import threading
from typing import Optional

import config
from marketing import sheets

TAB_NAME = "sms_shortener"
_TOKEN_BYTES = 4  # → 6-char URL-safe string, ~68B possible tokens

# In-process cache. Single instance on Render starter, so this is the
# source of truth between Sheets reads.
_cache: dict[str, str] = {}
_cache_loaded = False
_cache_lock = threading.Lock()


def _now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _get_tab():
    """Return the sms_shortener worksheet, creating it on first use."""
    ss = sheets.marketing_sheet()
    try:
        return ss.worksheet(TAB_NAME)
    except Exception:  # noqa: BLE001 — gspread raises WorksheetNotFound
        ws = ss.add_worksheet(title=TAB_NAME, rows=2000, cols=6)
        ws.update("A1:F1", [["token", "long_url", "created_at",
                              "clicks", "last_click_at", "label"]])
        return ws


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
            print(f"[url_shortener] cache load failed: {e}")
            _cache_loaded = True  # don't retry on every request
            return
        for r in rows[1:]:  # skip header
            if len(r) >= 2 and r[0] and r[1]:
                _cache[r[0]] = r[1]
        _cache_loaded = True
        print(f"[url_shortener] cache loaded with {len(_cache)} tokens")


def _generate_token() -> str:
    """6-char URL-safe random token; retry if it collides with cache."""
    for _ in range(8):
        t = secrets.token_urlsafe(_TOKEN_BYTES)[:6]
        if t not in _cache:
            return t
    raise RuntimeError("token-generation collision storm — cache likely huge")


def make_short_url(long_url: str, label: str = "") -> str:
    """Reserve a token for long_url, persist to Sheets, return the short URL.

    On Sheets failure, returns the original long_url (no shortening) so SMS
    sending never breaks because of an infra hiccup. The caller writes
    whatever it gets back as `survey_link`.
    """
    if not long_url:
        return long_url
    _load_cache_once()
    try:
        token = _generate_token()
        _get_tab().append_row([token, long_url, _now_iso(), 0, "", label],
                              value_input_option="RAW")
        _cache[token] = long_url
        return f"{config.SHORTENER_BASE_URL}/r/{token}"
    except Exception as e:  # noqa: BLE001
        print(f"[url_shortener] shorten FAILED, falling back to long URL: {e}")
        return long_url


def lookup(token: str) -> Optional[str]:
    """Resolve a token to its long URL. None if unknown."""
    if not token:
        return None
    _load_cache_once()
    hit = _cache.get(token)
    if hit:
        return hit
    # Cache miss — token may have been written by an earlier instance/restart.
    # Reload once before giving up.
    try:
        ws = _get_tab()
        for r in ws.get_all_values()[1:]:
            if len(r) >= 2 and r[0] == token:
                _cache[token] = r[1]
                return r[1]
    except Exception as e:  # noqa: BLE001
        print(f"[url_shortener] miss-time reload failed: {e}")
    return None


def record_click(token: str) -> None:
    """No-op (was: increment click counter in Sheets via a background thread).

    DISABLED 2026-06-04 — the threaded Sheets write was blocking the single
    sync gunicorn worker for ~5s per click, queueing subsequent requests
    behind it. The 302 redirect itself is fast and that's all the patient
    cares about; click analytics would be a nice-to-have we can re-add later
    via a periodic batch job that reads Twilio's own delivery + click data.
    """
    return  # intentionally no I/O
