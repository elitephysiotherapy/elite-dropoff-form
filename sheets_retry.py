"""Canonical backoff for transient Google Sheets failures.

Every Render cron in this repo talks to Sheets through one service account, so
they all share a single "60 read requests per minute per user" quota. Several
fire on the same minute (bookings-poll at :00, dropoff-daily at 06:00, the
weeklies on Monday morning), so a 429 or a 5xx on any individual call is
routine and self-heals within a minute or two.

Two Monday-morning crons died on exactly that on Mon 17 Aug 2026:
  - elite-weekly-team-email: 429 'Read requests per minute per user' — it did
    retry, but its 2/4/8/16s ladder gave up 30s in, still inside the saturated
    minute.
  - elite-nps-weekly: bare 503 on its first read, with no retry at all.
Both are once-a-week schedules, so each failure silently cost a whole week of
output. Hence one shared implementation with a ladder long enough to outlast a
per-minute quota window, kept dependency-light (gspread + requests only) so any
cron can import it without dragging in the Cliniko stack.

Deliberately does NOT retry 401/403/404 — those are auth or missing-tab bugs
that need a human, and retrying just delays the error.
"""

from __future__ import annotations

import socket
import time

import gspread
import requests

# 2, 4, 8, 16, 32, 60, 60 → up to ~3 min of waiting across 7 attempts. A Sheets
# per-minute quota window clears in 60s, so anything transient recovers well
# before the ladder runs out.
_MAX_DELAY = 60
DEFAULT_ATTEMPTS = 7

RETRY_STATUS = (429, 500, 502, 503, 504)

_TRANSIENT_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    ConnectionResetError,
    socket.timeout,
)


def _status_of(err) -> int | None:
    """Pull the HTTP status out of a gspread APIError / requests HTTPError."""
    resp = getattr(err, "response", None)
    return getattr(resp, "status_code", None)


def gs_retry(call, label: str, attempts: int = DEFAULT_ATTEMPTS,
             prefix: str = ""):
    """Call a gspread operation, retrying transient failures with backoff.

    Retries connection resets/timeouts and HTTP 429/500/502/503/504, raising
    the original error once the attempts are spent. `label` and `prefix` only
    affect the progress lines written to the cron log.
    """
    delay = 2
    for i in range(attempts):
        try:
            return call()
        except _TRANSIENT_EXC as e:
            if i == attempts - 1:
                raise
            reason = f"transient {type(e).__name__}"
        except (gspread.exceptions.APIError, requests.exceptions.HTTPError) as e:
            code = _status_of(e)
            if code not in RETRY_STATUS or i == attempts - 1:
                raise
            reason = f"Sheets API {code}"
        print(f"{prefix}{label}: {reason}, retry {i + 1}/{attempts - 1} "
              f"in {delay}s", flush=True)
        time.sleep(delay)
        delay = min(delay * 2, _MAX_DELAY)
