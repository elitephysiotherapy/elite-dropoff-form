"""Tests for sheets_retry.gs_retry — the shared Sheets 429/5xx backoff.

Covers the two Monday-morning cron failures of Mon 17 Aug 2026:
  - elite-weekly-team-email: 429 'Read requests per minute per user'
  - elite-nps-weekly: bare 503 on its first read

Run: ./venv/bin/python test_sheets_retry.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import gspread
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sheets_retry  # noqa: E402

# Don't actually sleep through the ladder — record what it would have waited.
slept: list[int] = []
sheets_retry.time = types.SimpleNamespace(sleep=slept.append)

failures = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"PASS  {label}" + (f"   ({detail})" if detail else ""))
    else:
        failures += 1
        print(f"FAIL  {label}" + (f"   ({detail})" if detail else ""))


def api_error(code: int) -> gspread.exceptions.APIError:
    """Build a real gspread APIError the way the Sheets API returns one."""
    r = requests.Response()
    r.status_code = code
    r._content = (
        '{"error":{"code":%d,"message":"quota","status":"RESOURCE_EXHAUSTED"}}'
        % code
    ).encode()
    return gspread.exceptions.APIError(r)


def flaky(fail_times: int, exc):
    """A call that fails `fail_times` times, then returns 'ok'."""
    state = {"n": 0}

    def call():
        state["n"] += 1
        if state["n"] <= fail_times:
            raise exc() if callable(exc) else exc
        return "ok"

    return call, state


# 1. The team-email failure: four consecutive 429s. The old 2/4/8/16s ladder
#    gave up 30s in, still inside the saturated quota minute.
slept.clear()
call, state = flaky(4, lambda: api_error(429))
check("recovers from 4 consecutive 429s",
      sheets_retry.gs_retry(call, "429 test") == "ok",
      f"{state['n']} calls, waited {sum(slept)}s")

# 2. The NPS failure: a single transient 503 on the first read.
slept.clear()
call, state = flaky(1, lambda: api_error(503))
check("recovers from a single 503",
      sheets_retry.gs_retry(call, "503 test") == "ok",
      f"{state['n']} calls, waited {sum(slept)}s")

# 3. The whole ladder must outlast a 60s Sheets quota window — that is the
#    entire point of the change, so pin it.
slept.clear()
try:
    sheets_retry.gs_retry(lambda: (_ for _ in ()).throw(api_error(429)),
                          "exhaust test")
    check("exhausted ladder re-raises", False, "returned instead of raising")
except gspread.exceptions.APIError:
    check("exhausted ladder re-raises the 429", True)
check("ladder outlasts a 60s quota window", sum(slept) > 60,
      f"total wait {sum(slept)}s across {len(slept)} sleeps")

# 4. A 403 is a permissions bug, not a quota brush — must fail fast so the
#    error reaches the cron log instead of being buried under 3 min of retries.
slept.clear()
call, state = flaky(99, lambda: api_error(403))
try:
    sheets_retry.gs_retry(call, "403 test")
    check("403 fails fast", False, "should have raised")
except gspread.exceptions.APIError:
    check("403 fails fast without retrying",
          state["n"] == 1 and not slept, f"{state['n']} call(s), {slept}")

# 5. Connection resets (the original Mon 8 Jun 2026 failure) still retry.
slept.clear()
call, state = flaky(2, lambda: ConnectionResetError("reset by peer"))
check("recovers from connection reset",
      sheets_retry.gs_retry(call, "reset test") == "ok",
      f"{state['n']} calls")

# 6. A plain requests.HTTPError (not wrapped by gspread) is handled too.
slept.clear()
resp = requests.Response()
resp.status_code = 502
http_err = requests.exceptions.HTTPError(response=resp)
call, state = flaky(1, http_err)
check("recovers from requests HTTPError 502",
      sheets_retry.gs_retry(call, "502 test") == "ok",
      f"{state['n']} calls")


# ─── send_referrers_monthly._read_week_tab ──────────────────────────────────
# Same hazard, different failure mode: this read used to swallow every error, so
# a quota brush dropped a week of bookings and the monthly DM went out
# undercounted. These pin that wrong numbers can no longer go out silently.

import send_referrers_monthly as ref  # noqa: E402


class FakeWS:
    """Minimal stand-in for a gspread worksheet."""

    def __init__(self, title, fail_times=0, exc=None):
        self.title = title
        self._left = fail_times
        self._exc = exc
        self.calls = 0

    def get_all_values(self):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise self._exc()
        return [["Referrer", "Appointment Date"], ["Online", "01/07/2026 09:00"]]


# 7. A transient 429 mid-read recovers and still returns the week's rows.
slept.clear()
ws = FakeWS("W/C 06 Jul 2026", fail_times=2, exc=lambda: api_error(429))
rows = ref._read_week_tab(ws)
check("referrers: transient 429 recovers with rows intact",
      len(rows) == 2 and ws.calls == 3, f"{ws.calls} calls, {len(rows)} rows")

# 8. The critical one: a 429 that survives the whole ladder must RAISE, not
#    return [] — an empty list here is a silently undercounted month.
slept.clear()
ws = FakeWS("W/C 13 Jul 2026", fail_times=99, exc=lambda: api_error(429))
try:
    ref._read_week_tab(ws)
    check("referrers: unrecoverable 429 fails loudly", False,
          "returned instead of raising — month would undercount")
except RuntimeError as e:
    check("referrers: unrecoverable 429 fails loudly",
          "undercounted" in str(e), "raises RuntimeError naming the risk")

# 9. A tab deleted mid-run is tolerated (nothing to recover, not a quota issue).
slept.clear()
ws = FakeWS("W/C 20 Jul 2026", fail_times=99, exc=lambda: api_error(404))
check("referrers: vanished tab is skipped, not fatal",
      ref._read_week_tab(ws) == [], "returns [] on 404")

print("-" * 60)
print("ALL PASS" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
