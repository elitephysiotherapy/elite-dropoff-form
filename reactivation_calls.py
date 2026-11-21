"""Reactivation call cards in #reactivationcalls (Martin, 21 Nov 2026).

Same idea as the #leads cards: every drop-off reception should ring becomes a
Slack card, every call is a tap, and the next call is timed for them.

Who is on it (rows on the W/C drop-off tabs, from config.REACT_START_DATE):
  - Reactivation Status blank / pending / contact_attempted
  - NOT marked Clinical, and Next Step not "not appropriate" / physio
    contacting directly / tricky patient form (the physio owns those)
  - unclassified rows wait REACT_UNCLASSIFIED_WAIT_DAYS for the physio first
  - NOT if the patient already has a future booking (the morning drop-off run
    flips those rows to "rebooked" by itself)
  - NOT if they're on a physio's Christmas campaign call list (no double calls)

Cadence (Front Desk Call Playbook): call 1; call 2 one day later; call 3 about
12 days after the first; then let it go (status "leave"). "Call back next
week" puts them back on the list in REACT_CALLBACK_DAYS.

Every tap writes the row's Reactivation Status and a dated line in "Reception
Reactivation Notes" on the drop-off sheet, which stays the master:
  Rebooked        note only (the morning run confirms it in Cliniko -> rebooked)
  No answer       contact_attempted; 3rd no answer -> leave
  Call back       contact_attempted, back on the list in a week
  Not interested  leave (and the card asks for the real reason in the notes)

Posting: once a day at REACT_LIST_TIME (Mon–Fri, not closure days) a header
message tags reception with today's count and links any retries to their
original card; new people get their own card below it.

State: "Reactivation Calls" tab on the drop-off sheet, one row per drop-off
(appointment id), so nobody is posted twice across restarts.

Run:  python reactivation_calls.py               dry run: counts only, no names
      python reactivation_calls.py --once-if-due  what bot_worker.py runs
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import phase2
import speed_to_lead as stl
import campaign
from speed_to_lead import _iso, _parse, esc, norm_phone

try:   # needs the Cliniko API key just to import
    from marketing import cliniko
except Exception:  # noqa: BLE001
    cliniko = None

# All imports at module level: the button handler runs in a server.py request
# thread, and a lazy import there can see a half-loaded module (2026-11-08).

LONDON = ZoneInfo("Europe/London")

HEADERS = ["Appt ID", "Tab", "Patient ID", "Posted At", "Slack Channel", "Slack TS",
           "Attempts", "Last Attempt At", "Next Due", "Outcome", "Attempt Log"]
COL = {h: i for i, h in enumerate(HEADERS)}
LIST_KEY = "__list__"

OPEN_STATUSES = {"", "pending", "contact_attempted"}
PHYSIO_OWNS = {"not appropriate", "physio contacting patient directly",
               "tricky patient form"}
DONE_OUTCOMES = {"rebooked", "not interested", "leave", "skipped"}

DROP_TYPE_LABELS = {"cancelled": "Cancelled, nothing rebooked",
                    "did_not_attend": "No-show",
                    "iadnr": "First visit, didn't rebook",
                    "iacna": "First visit cancelled",
                    "iadna": "First visit no-show"}


# ---------------------------------------------------------------------------
# Pure rules (tested in test_reactivation_calls.py)
# ---------------------------------------------------------------------------
def _day(s: str) -> date | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s or "").strip())
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def add_working_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in config.CLINIC_CLOSED_DAYS:
            n -= 1
    return d


def next_working_day_on_or_after(d: date) -> date:
    while d.weekday() >= 5 or d in config.CLINIC_CLOSED_DAYS:
        d += timedelta(days=1)
    return d


def row_eligible(row: dict) -> tuple[bool, str]:
    """Is this drop-off row one reception should be ringing at all?"""
    if not str(row.get("appointment_id") or "").strip():
        return False, "no appointment id"
    status = str(row.get("Reactivation Status") or "").strip().lower()
    if status not in OPEN_STATUSES:
        return False, f"status {status}"
    if str(row.get("Clinical / Non-Clinical") or "").strip().lower() == "clinical":
        return False, "clinical (physio)"
    if str(row.get("Next Step (Physio)") or "").strip().lower() in PHYSIO_OWNS:
        return False, "physio owns it"
    return True, ""


def ready_date(row: dict) -> date | None:
    """First day reception should ring: the day after the row landed, or a
    couple of working days later if the physio hasn't classified it yet."""
    landed = _day(row.get("pulled_at")) or _day(row.get("Cancellation Date")) \
        or _day(row.get("Appointment Date"))
    if not landed:
        return None
    classified = str(row.get("Clinical / Non-Clinical") or "").strip()
    if classified:
        return next_working_day_on_or_after(landed)
    return add_working_days(landed, config.REACT_UNCLASSIFIED_WAIT_DAYS)


def attempts_of(row: dict, st: dict | None) -> int:
    if st and str(st.get("Attempts") or "").isdigit():
        return int(st["Attempts"])
    # Rows reception had already rung before the cards existed.
    status = str(row.get("Reactivation Status") or "").strip().lower()
    return 1 if status == "contact_attempted" else 0


def next_due(row: dict, st: dict | None) -> date | None:
    """The day this person should next be rung, or None (finished)."""
    ok, _ = row_eligible(row)
    if not ok:
        return None
    if st and st.get("Outcome") in DONE_OUTCOMES:
        return None
    ready = ready_date(row)
    if not ready:
        return None
    if st and st.get("Outcome") == "call back" and _day(st.get("Next Due")):
        return _day(st["Next Due"])
    n = attempts_of(row, st)
    if n >= config.REACT_MAX_ATTEMPTS:
        return None
    if n == 0:
        return ready
    last = _day((st or {}).get("Last Attempt At")) or ready
    gap = config.REACT_RETRY_GAPS_DAYS[min(n, len(config.REACT_RETRY_GAPS_DAYS)) - 1]
    return next_working_day_on_or_after(max(last + timedelta(days=gap), ready))


def apply_tap(choice: str, st: dict, today: date, who: str) -> tuple[dict, str, str]:
    """(state cells, new sheet status or "", note line) for a button tap."""
    n = int(st.get("Attempts") or 0) + 1
    stamp = today.strftime("%d/%m")
    cells = {"Attempts": str(n), "Last Attempt At": today.isoformat()}
    if choice == "rebooked":
        cells["Outcome"] = "rebooked"
        return cells, "", f"{stamp} {who}: rebooked (call {n})"
    if choice == "notinterested":
        cells["Outcome"] = "not interested"
        return cells, "leave", f"{stamp} {who}: not interested (call {n})"
    if choice == "callback":
        due = next_working_day_on_or_after(today + timedelta(days=config.REACT_CALLBACK_DAYS))
        cells.update({"Outcome": "call back", "Next Due": due.isoformat()})
        return cells, "contact_attempted", f"{stamp} {who}: call back w/c {due:%d/%m} (call {n})"
    # no answer
    if n >= config.REACT_MAX_ATTEMPTS:
        cells["Outcome"] = "leave"
        return cells, "leave", (f"{stamp} {who}: no answer (call {n}) - "
                                f"{n} calls, letting it go")
    cells["Outcome"] = "no answer"
    return cells, "contact_attempted", f"{stamp} {who}: no answer (call {n})"


def tap_note(choice: str, cells: dict, user_id: str, now: datetime) -> str:
    t = now.strftime("%H:%M")
    n = cells["Attempts"]
    if choice == "rebooked":
        return (f"✅ *Rebooked* by <@{user_id}> at {t}. Post the win in #reactivation "
                "as usual; the sheet confirms it from Cliniko tomorrow morning.")
    if choice == "notinterested":
        return (f"✋ *Not interested* — <@{user_id}> at {t}. Marked *leave*. Please add "
                "their real reason in Reception Reactivation Notes (not \"Other\").")
    if choice == "callback":
        due = date.fromisoformat(cells["Next Due"])
        return (f"📅 *Call back* — <@{user_id}> at {t}. Back on the list "
                f"{due.strftime('%a %d %b')}.")
    if cells.get("Outcome") == "leave":
        return (f"📵 *No answer* — call {n} of {config.REACT_MAX_ATTEMPTS} by <@{user_id}> "
                f"at {t}. That's the last call: marked *leave*.")
    gap = config.REACT_RETRY_GAPS_DAYS[min(int(n), len(config.REACT_RETRY_GAPS_DAYS)) - 1]
    return (f"📵 *No answer* — call {n} of {config.REACT_MAX_ATTEMPTS} by <@{user_id}> "
            f"at {t}. Back on the list in {gap} day{'s' if gap != 1 else ''}. "
            "No voicemail with a question.")


def card_text(row: dict, phone: str, n_next: int) -> str:
    clinic = str(row.get("Clinic") or "").replace("Elite Physiotherapy ", "")
    drop = DROP_TYPE_LABELS.get(str(row.get("Drop-off Type") or "").lower(),
                                row.get("Drop-off Type") or "")
    when = _day(row.get("Cancellation Date")) or _day(row.get("Appointment Date"))
    reason = row.get("Cancellation Reason") or ""
    bits = [f"*{esc(row.get('Patient Name') or '?')}* · <tel:{phone}|{phone}> · {clinic}"
            f" · {esc(row.get('Physio') or '')}"
            + (f" · {esc(row['Body Area'])}" if row.get("Body Area") else ""),
            f"{drop}"
            + (f" · {when.strftime('%a %d %b')}" if when else "")
            + (f" · session {row['Session #']}" if row.get("Session #") else "")
            + (f" · reason: {esc(reason)}" if reason else "")]
    physio = [x for x in (row.get("Next Step (Physio)"), row.get("Physio Action"),
                          row.get("Physio Reactivation Notes")) if str(x or "").strip()]
    if physio:
        txt = esc(" · ".join(str(x).strip() for x in physio))
        bits.append(f"_Physio: {txt[:300]}_")
    if str(row.get("Reception Reactivation Notes") or "").strip():
        bits.append(f"_Notes so far: {esc(str(row['Reception Reactivation Notes'])[-200:])}_")
    bits.append(f"Call {n_next} of {config.REACT_MAX_ATTEMPTS}. Ask where they are now vs "
                "where they wanted to be, why it's worth coming back, what we'd do, then "
                "book a time before you hang up.")
    return "\n".join(bits)


def actions_block(appt_id: str) -> dict:
    def b(choice, label, primary=False):
        el = {"type": "button", "action_id": f"react|{choice}|{appt_id}",
              "text": {"type": "plain_text", "text": label}, "value": appt_id}
        if primary:
            el["style"] = "primary"
        return el
    return {"type": "actions", "block_id": f"react_actions|{appt_id}", "elements": [
        b("rebooked", "✅ Rebooked", True), b("noanswer", "📵 No answer"),
        b("callback", "📅 Call back next week"), b("notinterested", "✋ Not interested")]}


def list_due_now(now: datetime, last_sent: datetime | None) -> bool:
    t = now.astimezone(LONDON)
    if t.weekday() >= 5 or t.date() in config.CLINIC_CLOSED_DAYS:
        return False
    slot = t.replace(hour=config.REACT_LIST_TIME[0], minute=config.REACT_LIST_TIME[1],
                     second=0, microsecond=0)
    if t < slot or t - slot > timedelta(minutes=config.REACT_LIST_GRACE_MIN):
        return False
    return not (last_sent and last_sent >= slot)


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
_sh = {"sh": None, "ws": None}


def sheet():
    if _sh["sh"] is None:
        _sh["sh"] = stl._retry(lambda: stl._gspread().open_by_key(config.REACT_DROPOFF_SHEET_ID),
                               "open drop-off sheet")
    return _sh["sh"]


def state_ws():
    if _sh["ws"] is None:
        sh = sheet()
        try:
            ws = stl._retry(lambda: sh.worksheet(config.REACT_STATE_TAB), "open react tab")
        except Exception as exc:
            if type(exc).__name__ != "WorksheetNotFound":
                raise
            ws = stl._retry(lambda: sh.add_worksheet(title=config.REACT_STATE_TAB,
                                                     rows=2000, cols=len(HEADERS)),
                            "create react tab")
            stl._retry(lambda: ws.update(values=[HEADERS], range_name="A1"), "react headers")
            stl._retry(lambda: ws.freeze(rows=1), "freeze react header")
        _sh["ws"] = ws
    return _sh["ws"]


def read_state(ws) -> dict[str, dict]:
    vals = stl._retry(lambda: ws.get_all_values(), "read react tab")
    if not vals or vals[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError(f"'{config.REACT_STATE_TAB}' headers changed")
    out = {}
    for n, row in enumerate(vals[1:], start=2):
        row = row + [""] * (len(HEADERS) - len(row))
        if row[0]:
            d = {h: row[i] for h, i in COL.items()}
            d["_row"] = n
            out[row[0]] = d
    return out


def update_state(ws, row: int, values: dict):
    data = [{"range": f"{stl._col_letter(COL[h])}{row}", "values": [[v]]}
            for h, v in values.items()]
    stl._retry(lambda: ws.batch_update(data, value_input_option="RAW"),
               f"update react row {row}")


def append_state(ws, values: dict) -> int:
    row = [values.get(h, "") for h in HEADERS]
    resp = stl._retry(lambda: ws.append_row(row, value_input_option="RAW",
                                            table_range="A1"), "append react row")
    m = re.search(r"![A-Z]+(\d+)", (resp or {}).get("updates", {}).get("updatedRange", ""))
    return int(m.group(1)) if m else 0


def _week_start(title: str) -> date | None:
    try:
        return datetime.strptime(title.replace("W/C ", ""), "%d %b %Y").date()
    except ValueError:
        return None


def _state_or_empty() -> dict:
    """Read-only state for dry runs: never creates the tab."""
    try:
        ws = stl._retry(lambda: sheet().worksheet(config.REACT_STATE_TAB), "open react tab")
        return read_state(ws)
    except Exception:
        return {}


def dropoff_rows(today: date) -> list[dict]:
    """Rows from the W/C tabs in range, as {header: value} + '_tab', '_row'."""
    floor = max(config.REACT_START_DATE, today - timedelta(days=config.REACT_LOOKBACK_DAYS))
    floor -= timedelta(days=floor.weekday())
    tabs = [w for w in stl._retry(lambda: sheet().worksheets(), "list tabs")
            if w.title.startswith("W/C ") and (_week_start(w.title) or date.min) >= floor]
    if not tabs:
        return []
    resp = stl._retry(lambda: sheet().values_batch_get([f"'{w.title}'" for w in tabs]),
                      "read W/C tabs")
    out = []
    for w, vr in zip(tabs, resp.get("valueRanges", [])):
        vals = vr.get("values", [])
        if not vals:
            continue
        head = vals[0]
        for n, r in enumerate(vals[1:], start=2):
            r = r + [""] * (len(head) - len(r))
            d = dict(zip(head, r))
            d["_tab"], d["_row"] = w.title, n
            out.append(d)
    return out


def find_sheet_row(appt_id: str, tab_hint: str = "") -> tuple:
    """(worksheet, row number, header list) for this appointment id."""
    sh = sheet()
    tabs = [w for w in sh.worksheets() if w.title.startswith("W/C ")]
    tabs.sort(key=lambda w: (w.title != tab_hint, -((_week_start(w.title) or date.min).toordinal())))
    for w in tabs[:12]:
        vals = stl._retry(lambda: w.get_all_values(), f"read {w.title}")
        if not vals:
            continue
        head = vals[0]
        if "appointment_id" not in head:
            continue
        i = head.index("appointment_id")
        for n, r in enumerate(vals[1:], start=2):
            if len(r) > i and r[i] == appt_id:
                return w, n, head, dict(zip(head, r + [""] * (len(head) - len(r))))
    return None, None, None, None


def write_sheet(w, n: int, head: list, current: dict, status: str, note: str):
    data = []
    if status:
        c = head.index("Reactivation Status") + 1
        data.append({"range": f"{stl._col_letter(c - 1)}{n}", "values": [[status]]})
    if note:
        c = head.index("Reception Reactivation Notes") + 1
        old = str(current.get("Reception Reactivation Notes") or "").strip()
        data.append({"range": f"{stl._col_letter(c - 1)}{n}",
                     "values": [[f"{old} | {note}" if old else note]]})
    if data:
        stl._retry(lambda: w.batch_update(data, value_input_option="RAW"),
                   f"write drop-off row {n}")


# ---------------------------------------------------------------------------
# The daily run
# ---------------------------------------------------------------------------
def _patient_for(appt_id: str) -> tuple[str, str]:
    """(patient id, mobile) for a drop-off's appointment."""
    # A plain GET 404s for cancelled appointments; the filtered search (live
    # + cancelled) finds both.
    found = cliniko._merge_live_cancelled([("q[]", f"id:={appt_id}")])
    if not found:
        return "", ""
    pid = str(cliniko.patient_id_of(found[0]) or "")
    if not pid:
        return "", ""
    p = cliniko.get_patient(pid) or {}
    return pid, p.get("mobile") or ""


def _campaign_pids() -> set:
    try:
        return {str(r.get("Patient ID")) for r in campaign.read_rows()
                if r.get("Patient ID") and r.get("Physio") and not campaign.is_closed(r)}
    except Exception as exc:  # noqa: BLE001
        print(f"  react: campaign list unreadable ({exc}) - not de-duplicating")
        return set()


def build_today(now: datetime, log=print, dry_run: bool = False) -> tuple[list, list, dict]:
    """(new [(row, pid, phone)], retries [(row, st)], skips Counter-ish dict)."""
    today = now.astimezone(LONDON).date()
    ws = None if dry_run else state_ws()
    state = _state_or_empty() if dry_run else read_state(ws)
    rows = dropoff_rows(today)
    camp = None
    new, retries, skips = [], [], {}
    for row in rows:
        aid = str(row.get("appointment_id") or "").strip()
        st = state.get(aid)
        due = next_due(row, st)
        if not due or due > today:
            continue
        if st and st.get("Slack TS"):
            retries.append((row, st))
            continue
        if st and st.get("Outcome") == "skipped":
            continue
        pid, phone = _patient_for(aid)
        reason = ""
        if not pid:
            reason = "patient not found"
        elif cliniko.has_future_booking(pid):
            reason = "already booked"
        else:
            camp = _campaign_pids() if camp is None else camp
            if pid in camp:
                reason = "on campaign call list"
        if reason:
            skips[reason] = skips.get(reason, 0) + 1
            # Already-booked rows flip to "rebooked" on the morning run; campaign
            # people may come back once the campaign closes, so don't store those.
            if reason == "patient not found" and not dry_run:
                append_state(ws, {"Appt ID": aid, "Tab": row["_tab"], "Outcome": "skipped",
                                  "Attempt Log": reason})
            continue
        new.append((row, pid, phone or "no mobile on file", st))
    return new, retries, skips


def run(now: datetime | None = None, dry_run: bool = False, force: bool = False,
        log=print) -> dict:
    if cliniko is None:
        raise RuntimeError("Cliniko client unavailable")
    now = (now or datetime.now(LONDON)).astimezone(LONDON)
    ws = None if dry_run else state_ws()
    state = _state_or_empty() if dry_run else read_state(ws)
    marker = state.get(LIST_KEY)
    if not force and not list_due_now(now, _parse(marker["Posted At"]) if marker else None):
        return {}
    new, retries, skips = build_today(now, log, dry_run=dry_run)
    stats = {"new": len(new), "retries": len(retries), **{f"skip {k}": v for k, v in skips.items()}}
    log(f"  react: {stats}")
    if dry_run:
        return stats
    if not config.REACT_CHANNEL:
        raise RuntimeError("REACT_CHANNEL not set")
    slack = stl.slack()
    total = len(new) + len(retries)
    if total:
        lines = [f":telephone_receiver: <@{config.STL_RECEPTION_USER_ID}> "
                 f"*{total} reactivation call{'s' if total != 1 else ''} today*"
                 + (f" — {len(new)} new card{'s' if len(new) != 1 else ''} below" if new else "")]
        if retries:
            lines.append("*Ringing again (tap the card after each call):*")
            for row, st in retries:
                try:
                    link = slack.chat_getPermalink(channel=st["Slack Channel"],
                                                   message_ts=st["Slack TS"])["permalink"]
                    name = f"<{link}|{esc(row.get('Patient Name') or '?')}>"
                except Exception:
                    name = esc(row.get("Patient Name") or "?")
                n = attempts_of(row, st)
                why = "call back booked" if st.get("Outcome") == "call back" else \
                    f"call {n + 1} of {config.REACT_MAX_ATTEMPTS}"
                lines.append(f"• {name} · {esc(row.get('Physio') or '')} · {why}")
        slack.chat_postMessage(channel=config.REACT_CHANNEL, text="\n".join(lines),
                               unfurl_links=False)
    for row, pid, phone, st in new:
        aid = str(row["appointment_id"]).strip()
        n_next = attempts_of(row, st) + 1
        resp = slack.chat_postMessage(
            channel=config.REACT_CHANNEL, unfurl_links=False,
            text=f"Reactivation call: {row.get('Patient Name')}",
            blocks=[{"type": "section", "text": {"type": "mrkdwn",
                                                 "text": card_text(row, phone, n_next)}},
                    actions_block(aid)])
        vals = {"Appt ID": aid, "Tab": row["_tab"], "Patient ID": pid,
                "Posted At": _iso(now), "Slack Channel": resp["channel"],
                "Slack TS": resp["ts"], "Attempts": str(n_next - 1)}
        if st:
            update_state(ws, st["_row"], vals)
        else:
            append_state(ws, vals)
    if marker:
        update_state(ws, marker["_row"], {"Posted At": _iso(now)})
    else:
        append_state(ws, {"Appt ID": LIST_KEY, "Posted At": _iso(now), "Outcome": "meta"})
    return stats


# ---------------------------------------------------------------------------
# Buttons (server.py routes action ids starting "react|" here)
# ---------------------------------------------------------------------------
def _who(user_id: str) -> str:
    try:
        p = stl.slack().users_info(user=user_id)["user"]["profile"]
        return (p.get("display_name") or p.get("real_name") or "reception").split()[0]
    except Exception:
        return "reception"


def handle_button(action_id: str, user_id: str, response_url: str,
                  original_blocks: list, fallback_text: str):
    try:
        _, choice, aid = action_id.split("|", 2)
        now = datetime.now(LONDON)
        ws = state_ws()
        st = read_state(ws).get(aid)
        if not st:
            raise RuntimeError(f"react row {aid} not found")
        cells, status, note = apply_tap(choice, st, now.date(), _who(user_id))
        cells["Attempt Log"] = (f"{st.get('Attempt Log')}; {note}"
                                if st.get("Attempt Log") else note)
        w, n, head, current = find_sheet_row(aid, st.get("Tab", ""))
        if w is None:
            raise RuntimeError(f"drop-off row for {aid} not found")
        write_sheet(w, n, head, current, status, note)
        update_state(ws, st["_row"], cells)
        blocks = [b for b in original_blocks
                  if not str(b.get("block_id", "")).startswith(
                      ("react_actions|", "react_done|", "react_warn"))]
        blocks.append({"type": "context", "block_id": f"react_done|{aid}",
                       "elements": [{"type": "mrkdwn",
                                     "text": tap_note(choice, cells, user_id, now)}]})
        if cells.get("Outcome") in ("no answer", "call back"):
            blocks.append(actions_block(aid))
        requests.post(response_url, json={"replace_original": True,
                                          "text": fallback_text, "blocks": blocks},
                      timeout=10)
    except Exception:
        warn = {"type": "context", "block_id": "react_warn", "elements": [{
            "type": "mrkdwn", "text": ":warning: Couldn't save that just now "
                                      "- please tap again in a minute."}]}
        blocks = [b for b in original_blocks if b.get("block_id") != "react_warn"]
        try:
            requests.post(response_url, json={"replace_original": True,
                          "text": fallback_text, "blocks": blocks + [warn]}, timeout=10)
        finally:
            raise


def run_once() -> int:
    import bot_state
    import slack_notifier
    try:
        stats = run(log=lambda m: print(m, flush=True))
        if stats:
            print(f"reactivation calls: {stats}", flush=True)
        bot_state.record_result("reactivation_calls", True)
        return 0
    except Exception as exc:
        fails = bot_state.record_result("reactivation_calls", False)
        print(f"reactivation calls failed ({fails}): {exc}", flush=True)
        if fails in (3, 12):
            try:
                slack_notifier._send_dm(
                    config.CEO_SLACK_EMAIL,
                    f":warning: The 10am reactivation call list has failed {fails} times "
                    f"in a row — reception has *not* had today's cards. Last error: `{exc}`",
                    target_label="reactivation calls failure")
            except Exception as alert_exc:
                print(f"  (could not raise the alert: {alert_exc})", flush=True)
        return stl.HANDLED_ERROR


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if "--once-if-due" in sys.argv:
        sys.exit(run_once())
    print(run(dry_run=True, force=True))
