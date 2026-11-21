"""Save-call alert: a first visit that is cancelled or marked did-not-arrive is
posted in #leads straight away, so the front desk rings within the hour.

Martin, 21 Nov 2026. 9 of the 11 Meta first visits we lost (Sep–Nov) were
cancellations, 4 of them on the day, and none of the 11 came back. The Front
Desk Call Playbook's Script 3 says ring within an hour and rebook before the
call ends; until now nothing told anyone a first visit had just gone.

What counts
  - appointment type in config.FORM_CHASE_TYPE_IDS (the 5 first-consultation
    types), cancelled — or marked did-not-arrive — in the last
    SAVE_CALL_LOOKBACK_HOURS and after SAVE_CALL_START_AT (no backlog on go-live)
  - NOT if the patient already has another future appointment (they rebooked
    while cancelling — recorded as "already rebooked", no post)

The card: who, when it was, physio, clinic, Meta lead / new patient, the
Cliniko cancellation reason, the script in one line, and three buttons —
Rebooked / Declined / No answer (No answer can be tapped again). Outside desk
hours the card says to ring when the desk opens.

State: "Save Calls" tab on the bookings sheet, one row per appointment, so an
alert goes once even across restarts and redeploys. No patient names stored.

Safety: config.SAVE_CALL_LIVE false (default) = posts go to Martin's DM only.

Run:  python save_call_alert.py            dry run: what would be posted now
      python save_call_alert.py --once     one real run (bot_worker.py, every 5 min)
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import phase2
import speed_to_lead as stl
from speed_to_lead import _iso, _parse, desk_open_at, clock_start, esc, norm_phone

try:   # needs the Cliniko API key just to import
    from marketing import cliniko
except Exception:  # noqa: BLE001
    cliniko = None

# Imported here, never inside a function: the button handler runs in a
# server.py request thread (see the 2026-11-08 half-imported module bug).

LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

HEADERS = ["Appt ID", "Event", "Event At", "Alerted At", "Patient ID", "Clinic",
           "Appt Start", "Type", "Reasons", "Slack Channel", "Slack TS",
           "Outcome", "Outcome By", "Outcome At", "Attempts"]
COL = {h: i for i, h in enumerate(HEADERS)}

# Cliniko cancellation_reason codes (as shown in the Cliniko cancel dialog).
CANCEL_REASONS = {10: "Feeling better", 20: "Condition worse", 30: "Sick",
                  40: "Away", 50: "Other", 60: "Work"}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in test_save_call_alert.py)
# ---------------------------------------------------------------------------
def event_of(appt: dict) -> tuple[str, datetime] | None:
    """("cancelled" | "did not arrive", when) or None."""
    if appt.get("cancelled_at"):
        t = phase2.parse_iso(appt["cancelled_at"])
        return ("cancelled", t) if t else None
    if appt.get("did_not_arrive"):
        t = phase2.parse_iso(appt.get("updated_at") or appt.get("starts_at"))
        return ("did not arrive", t) if t else None
    return None


def qualifies(appt: dict, now: datetime, start_at: datetime, type_ids) -> bool:
    if str(phase2.id_from_link(appt.get("appointment_type"))) not in type_ids:
        return False
    ev = event_of(appt)
    if not ev:
        return False
    when = ev[1]
    floor = max(start_at, now - timedelta(hours=config.SAVE_CALL_LOOKBACK_HOURS))
    return floor <= when <= now + timedelta(minutes=5)


def reason_text(appt: dict) -> str:
    if not appt.get("cancelled_at"):
        return "no-show"
    code = appt.get("cancellation_reason")
    try:
        label = CANCEL_REASONS.get(int(code), "") if code not in (None, "") else ""
    except (TypeError, ValueError):
        label = ""
    label = appt.get("cancellation_reason_description") or label or "none given"
    note = (appt.get("cancellation_note") or "").strip()
    if note:
        note = note if len(note) <= 140 else note[:140] + "…"
        return f"{label}: \"{esc(note)}\""
    return label


def hours_before(appt: dict, when: datetime) -> str:
    start = phase2.parse_iso(appt.get("starts_at"))
    if not start:
        return ""
    h = (start - when).total_seconds() / 3600
    if h < 0:
        return "after the appointment time"
    if h < 1:
        return "under an hour before"
    if h < 24:
        return f"{int(h)}h before"
    return f"{int(h // 24)} day{'s' if h >= 48 else ''} before"


def ring_line(now: datetime) -> str:
    if desk_open_at(now):
        return ":sos: *Save call — please ring within the hour*"
    opens = clock_start(now)
    return (":sos: *Save call — ring when the desk opens* "
            f"({opens.strftime('%a %H:%M')})")


def card_blocks(info: dict, now: datetime, appt_id: str, live: bool) -> list:
    """info: name, phone, clinic, start (datetime), physio, type, event,
    reasons (list), booked (datetime|None), reason, before."""
    start = info["start"].astimezone(LONDON)
    what = "First visit cancelled" if info["event"] == "cancelled" else "First visit missed (no-show)"
    tags = " · ".join(info["reasons"]) if info["reasons"] else "Returning patient"
    booked = (f"booked {info['booked'].astimezone(LONDON).strftime('%a %d %b')} · "
              if info.get("booked") else "")
    head = ring_line(now) + ("" if live else "  _[SAFE MODE]_")
    body = (f"*{what}* · *{esc(info['name'])}* · <tel:{info['phone']}|{info['phone']}>\n"
            f"{info['clinic']} · was {start.strftime('%a %d %b %H:%M')} with {esc(info['physio'])}"
            f" · {info['type']}\n"
            f"{tags} · {booked}{info['event']} {info['before']} · reason: {info['reason']}\n"
            "_Script 3: check they're okay, find the real reason (once), clear it, "
            "offer two times this week and book before you hang up._")
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{head}\n{body}"}},
        actions_block(appt_id),
    ]


def actions_block(appt_id: str) -> dict:
    els = [
        {"type": "button", "style": "primary", "action_id": f"save|rebooked|{appt_id}",
         "text": {"type": "plain_text", "text": "✅ Rebooked"}, "value": appt_id},
        {"type": "button", "action_id": f"save|declined|{appt_id}",
         "text": {"type": "plain_text", "text": "✋ Declined"}, "value": appt_id},
        {"type": "button", "action_id": f"save|noanswer|{appt_id}",
         "text": {"type": "plain_text", "text": "📵 No answer"}, "value": appt_id},
    ]
    return {"type": "actions", "block_id": f"save_actions|{appt_id}", "elements": els}


def outcome_note(outcome: str, user_id: str, now: datetime, attempts: int) -> str:
    t = now.strftime("%H:%M")
    if outcome == "rebooked":
        return f"✅ *Rebooked* by <@{user_id}> at {t}."
    if outcome == "declined":
        return (f"✋ *Declined* — logged by <@{user_id}> at {t}. Put their real reason "
                "in the cancellation note in Cliniko (not \"Other\"). Don't ring again "
                "about this booking.")
    return (f"📵 *No answer* — try {attempts} by <@{user_id}> at {t}. Try once more "
            "later today at a different time. No voicemail with a question.")


# ---------------------------------------------------------------------------
# Sheet state
# ---------------------------------------------------------------------------
_ws = {"ws": None}


def state_ws():
    if _ws["ws"] is None:
        sh = stl._retry(lambda: stl._gspread().open_by_key(config.LEADS_SPREADSHEET_ID),
                        "open bookings sheet")
        try:
            ws = stl._retry(lambda: sh.worksheet(config.SAVE_CALL_STATE_TAB),
                            "open save-call tab")
        except Exception as exc:
            if type(exc).__name__ != "WorksheetNotFound":
                raise
            ws = stl._retry(lambda: sh.add_worksheet(title=config.SAVE_CALL_STATE_TAB,
                                                     rows=1000, cols=len(HEADERS)),
                            "create save-call tab")
            stl._retry(lambda: ws.update(values=[HEADERS], range_name="A1"),
                       "write save-call headers")
            stl._retry(lambda: ws.freeze(rows=1), "freeze header")
        _ws["ws"] = ws
    return _ws["ws"]


def read_state(ws) -> dict[str, dict]:
    vals = stl._retry(lambda: ws.get_all_values(), "read save-call tab")
    if not vals or vals[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError(f"'{config.SAVE_CALL_STATE_TAB}' headers changed")
    out = {}
    for n, row in enumerate(vals[1:], start=2):
        row = row + [""] * (len(HEADERS) - len(row))
        if row[0]:
            d = {h: row[i] for h, i in COL.items()}
            d["_row"] = n
            out[row[0]] = d
    return out


def update_cells(ws, row: int, values: dict):
    data = [{"range": f"{stl._col_letter(COL[h])}{row}", "values": [[v]]}
            for h, v in values.items()]
    stl._retry(lambda: ws.batch_update(data, value_input_option="RAW"),
               f"update save-call row {row}")


def append_row(ws, values: dict) -> int:
    row = [values.get(h, "") for h in HEADERS]
    resp = stl._retry(lambda: ws.append_row(row, value_input_option="RAW",
                                            table_range="A1"), "append save-call row")
    m = re.search(r"![A-Z]+(\d+)", (resp or {}).get("updates", {}).get("updatedRange", ""))
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def _type_name(appt: dict) -> str:
    names = {"382563815654429852": "Initial Appointment",
             "392015278608749674": "Club IA",
             "945551547020874765": "ACL IA",
             "1521627460095973060": "Sports & MSK Consultation",
             "1558530673046721630": "PHI IA"}
    return names.get(str(phase2.id_from_link(appt.get("appointment_type"))), "First visit")


def _reasons(appt: dict, pid: str, mobile: str, now: datetime) -> list[str]:
    """Meta lead / Brand new patient / Previous poor attender — the same rules
    as the 9am day-before list."""
    import send_day_before_calls as dbc
    try:
        meta_map, _ = dbc._meta_lead_times()
        history = list(phase2.fetch_patient_full_history(pid))
        reasons, _ = dbc.classify(appt, history,
                                  meta_map.get(dbc._last10(mobile), []), now)
        return reasons
    except Exception as exc:  # noqa: BLE001
        print(f"  save-call: could not tag patient ({exc})")
        return []


def run(now: datetime | None = None, dry_run: bool = False, log=print) -> dict:
    if cliniko is None:
        raise RuntimeError("Cliniko client unavailable")
    now = (now or datetime.now(LONDON)).astimezone(LONDON)
    start_at = datetime.fromisoformat(config.SAVE_CALL_START_AT).astimezone(LONDON)
    live = config.SAVE_CALL_LIVE and config.STL_LIVE
    ws = None if dry_run else state_ws()
    state = {} if dry_run else read_state(ws)
    stats = {"posted": 0, "already_rebooked": 0}

    appts = {a["id"]: a for a in cliniko.recently_cancelled_appointments(days_back=2)}
    for a in cliniko.recently_started_appointments(days_back=2):
        if a.get("did_not_arrive") and not a.get("cancelled_at"):
            appts.setdefault(a["id"], a)
    for appt_id, a in appts.items():
        appt_id = str(appt_id)
        if appt_id in state or not qualifies(a, now, start_at, config.FORM_CHASE_TYPE_IDS):
            continue
        event, when = event_of(a)
        pid = cliniko.patient_id_of(a)
        if not pid:
            continue
        rec = {"Appt ID": appt_id, "Event": event, "Event At": _iso(when.astimezone(LONDON)),
               "Patient ID": str(pid), "Clinic": cliniko.clinic_for(a),
               "Appt Start": _iso(phase2.parse_iso(a["starts_at"]).astimezone(LONDON)),
               "Type": _type_name(a), "Attempts": "0"}
        if cliniko.has_future_booking(pid):
            rec.update({"Outcome": "already rebooked", "Alerted At": ""})
            stats["already_rebooked"] += 1
            log(f"  save-call {appt_id}: {event}, already has a future booking - no alert")
            if not dry_run:
                append_row(ws, rec)
            continue
        patient = cliniko.get_patient(pid) or {}
        mobile = patient.get("mobile") or ""
        reasons = _reasons(a, pid, mobile, now)
        info = {"name": patient.get("full_name") or "(name missing)",
                "phone": mobile or "no mobile on file", "clinic": rec["Clinic"],
                "start": phase2.parse_iso(a["starts_at"]),
                "physio": cliniko.practitioner_name(a) or "physio TBC",
                "type": rec["Type"], "event": event, "reasons": reasons,
                "booked": phase2.parse_iso(a.get("created_at")),
                "reason": reason_text(a), "before": hours_before(a, when)}
        rec["Reasons"] = " + ".join(reasons)
        log(f"  save-call {appt_id}: {event} {info['before']} ({rec['Clinic']}, "
            f"{rec['Type']}, {rec['Reasons'] or 'returning'})")
        if dry_run:
            continue
        # Claim the row first, so a crash after posting can't post twice.
        row = append_row(ws, {**rec, "Alerted At": _iso(now)})
        channel = stl.post_channel() if live else stl._uid(config.CEO_SLACK_EMAIL)
        blocks = card_blocks(info, now, appt_id, live)
        if live and desk_open_at(now):
            blocks[0]["text"]["text"] += f"\n{stl.reception_mention()}"
        resp = stl.slack().chat_postMessage(
            channel=channel, unfurl_links=False, blocks=blocks,
            text=f"Save call: first visit {event} - {rec['Clinic']}")
        if row:
            update_cells(ws, row, {"Slack Channel": resp["channel"], "Slack TS": resp["ts"]})
        stats["posted"] += 1
    return stats


# ---------------------------------------------------------------------------
# Buttons (server.py routes action ids starting "save|" here)
# ---------------------------------------------------------------------------
def handle_button(action_id: str, user_id: str, response_url: str,
                  original_blocks: list, fallback_text: str):
    try:
        _, outcome, appt_id = action_id.split("|", 2)
        outcome = {"noanswer": "no answer"}.get(outcome, outcome)
        now = datetime.now(LONDON)
        ws = state_ws()
        s = read_state(ws).get(appt_id)
        if not s:
            raise RuntimeError(f"save-call row {appt_id} not found")
        n = int(s.get("Attempts") or 0) + 1
        update_cells(ws, s["_row"], {"Outcome": outcome, "Outcome By": f"<@{user_id}>",
                                     "Outcome At": _iso(now), "Attempts": str(n)})
        blocks = [b for b in original_blocks
                  if not str(b.get("block_id", "")).startswith(
                      ("save_actions|", "save_done|", "save_warn"))]
        blocks.append({"type": "context", "block_id": f"save_done|{appt_id}",
                       "elements": [{"type": "mrkdwn",
                                     "text": outcome_note(outcome, user_id, now, n)}]})
        if outcome == "no answer":
            blocks.append(actions_block(appt_id))
        requests.post(response_url, json={"replace_original": True,
                                          "text": fallback_text, "blocks": blocks},
                      timeout=10)
    except Exception:
        warn = {"type": "context", "block_id": "save_warn", "elements": [{
            "type": "mrkdwn", "text": ":warning: Couldn't save that just now "
                                      "- please tap again in a minute."}]}
        blocks = [b for b in original_blocks if b.get("block_id") != "save_warn"]
        try:
            requests.post(response_url, json={"replace_original": True,
                          "text": fallback_text, "blocks": blocks + [warn]}, timeout=10)
        finally:
            raise


def run_once(dry_run: bool = False) -> int:
    import bot_state
    import slack_notifier
    try:
        stats = run(dry_run=dry_run, log=lambda m: print(m, flush=True))
        if any(stats.values()):
            print(f"save-call: {stats}", flush=True)
        if not dry_run:
            bot_state.record_result("save_calls", True)
        return 0
    except Exception as exc:
        fails = 0 if dry_run else bot_state.record_result("save_calls", False)
        print(f"save-call run failed ({fails}): {exc}", flush=True)
        if fails in (3, 36):                      # ~15 min, then ~3 h
            try:
                slack_notifier._send_dm(
                    config.CEO_SLACK_EMAIL,
                    f":warning: The save-call alert has failed {fails} times in a row "
                    f"— cancelled first visits are *not* being flagged. Last error: `{exc}`",
                    target_label="save-call failure")
            except Exception as alert_exc:
                print(f"  (could not raise the alert: {alert_exc})", flush=True)
        return stl.HANDLED_ERROR


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if "--once" in sys.argv:
        sys.exit(run_once())
    print(run(dry_run=True))
