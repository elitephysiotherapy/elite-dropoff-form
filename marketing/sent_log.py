"""Dedup ledger.

The poller runs every ~10 minutes, so it sees the same triggering appointment
many times. Before sending any touch it checks here; after sending it logs
here. One patient + one flow + one anchor = one send, ever.

Backed by the "Marketing - Sent Log" tab. Columns:
  A Timestamp  B Patient ID  C Patient Name  D Flow Name  E Channel
  F Episode Anchor  G Template Used  H Status

Defensive by design: if the sheet isn't configured yet (shadow mode before
nps_sheet_setup.gs is run) every call degrades gracefully — already_sent()
returns False and log_send() is a no-op — so the poller still dry-runs.
"""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
_TAB = "Marketing - Sent Log"
_rows_cache = None   # cached once per process (each poll is a fresh process)
_READ_FAILED = object()   # sentinel: ledger read errored (≠ legitimately empty)


def _now():
    return datetime.now(LONDON)


def _tab():
    """Return the Sent Log worksheet, or None if the sheet isn't configured."""
    try:
        from marketing.sheets import tab
        return tab(_TAB)
    except Exception as e:
        print(f"  WARN: Sent Log unavailable ({e}) — dedup/logging disabled")
        return None


def _rows():
    """Cached Sent Log rows for this process.

    Three outcomes, kept distinct on purpose:
      • ws is None  -> [] (sheet not configured / shadow mode — safe to "send",
                       nothing actually goes out in shadow)
      • read OK     -> the data rows
      • read errors -> _READ_FAILED sentinel, so already_sent() can FAIL SAFE
                       (skip this cycle) instead of re-sending the whole ledger.

    The read is retried a few times first, because a transient Google Sheets
    429 (write/read quota) used to blank the ledger and trigger a mass re-send.
    """
    global _rows_cache
    if _rows_cache is None:
        ws = _tab()
        if ws is None:
            _rows_cache = []
        else:
            _rows_cache = _READ_FAILED
            for attempt in range(4):
                try:
                    _rows_cache = ws.get_all_values()[1:]
                    break
                except Exception as e:
                    if attempt == 3:
                        print(f"  WARN: Sent Log read failed after retries ({e}) "
                              f"— skipping sends this cycle to avoid duplicates")
                        break
                    time.sleep(1.5 * (attempt + 1))
    return _rows_cache


def _parse_ts(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime((s or "").strip(), fmt).replace(tzinfo=LONDON)
        except (ValueError, AttributeError):
            continue
    return None


def already_sent(patient_id, flow_name, anchor, within_days=45):
    """True if this patient already received this flow for this anchor recently.
    Unparseable timestamps are treated as a match — fail safe, never double-send.
    """
    pid, anc = str(patient_id), str(anchor or "")
    cutoff = _now() - timedelta(days=within_days)
    rows = _rows()
    if rows is _READ_FAILED:
        # Could not verify the ledger this cycle — treat as already sent so we
        # never double-send. The next cycle (≈10 min) retries once the sheet is
        # readable again; a delayed touch is far better than a duplicate one.
        return True
    for r in rows:
        if len(r) < 6:
            continue
        if r[1] == pid and r[3] == flow_name and r[5] == anc:
            ts = _parse_ts(r[0])
            if ts is None or ts >= cutoff:
                return True
    return False


def log_send(patient_id, patient_name, flow_name, channel,
             anchor, template_used, status="sent"):
    """Append one row recording a send. No-op if the sheet isn't configured."""
    row = [
        _now().strftime("%Y-%m-%d %H:%M:%S"),
        str(patient_id), patient_name or "", flow_name, channel,
        str(anchor or ""), template_used or "", status,
    ]
    ws = _tab()
    if ws is None:
        return
    # Retry on transient Google Sheets 429s. A write that silently fails leaves
    # the touch SENT-but-UNLOGGED, which makes the next cycle re-send it — so we
    # try hard to record every send.
    for attempt in range(4):
        try:
            ws.append_row(row, value_input_option="USER_ENTERED")
            break
        except Exception as e:
            if attempt == 3:
                print(f"  WARN: sent_log write failed after retries ({e})")
                return
            time.sleep(1.5 * (attempt + 1))
    if isinstance(_rows_cache, list):   # keep in-process cache in sync
        _rows_cache.append(row)
