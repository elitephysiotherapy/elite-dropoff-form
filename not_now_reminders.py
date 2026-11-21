"""Day-30 "not now" reminder → Slack DM to Sinéad Rocks (Martin, 21 Nov 2026).

A lead who says "not now / not yet / I'll think about it / ring me after
Christmas" gets one human call about 30 days later. Reception already writes
that on the Leads tab (Reason Not Booked / Notes, date in Last Contacted); this
job reads it, waits NOT_NOW_DAYS, and DMs Sinéad Rocks a short list each
weekday morning of the leads now due a call.

Rules
  - Leads tab rows (any source) whose Status is not booked / not appropriate,
    and whose Reason Not Booked, Notes or Callback Needed reads like "not now"
    (NOT_NOW below) and not like a hard no (HARD_NO below).
  - "Said on" = Last Contacted (dd/mm/yy), else the lead's Date, else the day
    the bot first saw it. Remind on said + 30 days (next working day).
  - One reminder per "not now". If reception rings again and records a newer
    Last Contacted with another "not now", a fresh 30 days starts.
  - Backlog on the first run: anyone whose 30 days have already passed, as
    long as they said it within NOT_NOW_MAX_AGE_DAYS, is in the first DM.
  - Booked leads drop off once the Leads tab says booked (the DM asks Sinéad
    to check Cliniko before ringing).

State: "Not Now Reminders" tab on the bookings sheet (no names stored).

Run:  python not_now_reminders.py               dry run: counts only
      python not_now_reminders.py --once-if-due  what bot_worker.py runs
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
import speed_to_lead as stl
from speed_to_lead import _iso, _parse, esc, norm_phone

LONDON = ZoneInfo("Europe/London")
TAB = "Not Now Reminders"
HEADERS = ["Key", "First Seen", "Said On", "Remind On", "Reminded At", "Matched"]
COL = {h: i for i, h in enumerate(HEADERS)}
MARKER = "__dm__"

NOT_NOW = re.compile(
    r"\bnot (now|yet|at the moment|right now|ready|this week|just now)\b|maybe later|"
    r"later (date|on)|\b(have a |will |going to |need to |wants? to )?think(ing)? "
    r"(about|it over|on it)|have a think|will (call|ring|get) (us )?back|"
    r"(call|ring) (us |me )?back|get back to (us|me)|in a (few|couple)( of)? weeks|"
    r"after (christmas|xmas|new year|the holidays?)|new year|"
    r"wait(ing)? (and see|a bit|until|till|for)|hold(ing)? off|see how (it|they|he|she) "
    r"(goes|gets on|is)|too busy|busy (at the moment|just now|with)|next month|"
    r"check with (her|his|their|my) (partner|husband|wife)|money (is )?tight|pay ?day",
    re.I)
HARD_NO = re.compile(r"not interested|wrong number|do not contact|don'?t contact|"
                     r"no longer (needed|required)|deceased|opted out|\bstop\b", re.I)
CLOSED_STATUSES = {"booked", "not appropriate"}


# ---------------------------------------------------------------------------
# Pure rules (tested in test_not_now_reminders.py)
# ---------------------------------------------------------------------------
def working(d: date) -> bool:
    return d.weekday() < 5 and d not in config.CLINIC_CLOSED_DAYS


def next_working_day_on_or_after(d: date) -> date:
    while not working(d):
        d += timedelta(days=1)
    return d


def parse_ddmmyy(s: str) -> date | None:
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", str(s or ""))
    if not m:
        return None
    y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
    try:
        return date(y, int(m[2]), int(m[1]))
    except ValueError:
        return None


def says_not_now(text: str) -> bool:
    t = str(text or "")
    return bool(NOT_NOW.search(t)) and not HARD_NO.search(t)


def lead_key(row: dict) -> str:
    if str(row.get("Meta Lead ID") or "").strip():
        return str(row["Meta Lead ID"]).strip()
    p = norm_phone(row.get("Phone"))
    if p:
        return f"phone:{p}"
    raw = f"{row.get('Date')}|{row.get('Patient')}".lower()
    return "row:" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def not_now_text(row: dict) -> str:
    return " · ".join(str(row.get(c) or "").strip() for c in
                      ("Reason Not Booked", "Notes", "Callback Needed (by who)")
                      if str(row.get(c) or "").strip())


def said_on(row: dict, first_seen: date) -> date:
    return parse_ddmmyy(row.get("Last Contacted")) or parse_ddmmyy(row.get("Date")) or first_seen


def plan(rows: list[dict], state: dict, today: date) -> tuple[list, list]:
    """(state writes [(key, values)], due [(row, state values)]). Pure."""
    writes, due = [], []
    for row in rows:
        if str(row.get("Status") or "").strip().lower() in CLOSED_STATUSES:
            continue
        text = not_now_text(row)
        if not says_not_now(text):
            continue
        key = lead_key(row)
        st = state.get(key) or {}
        first = datetime.strptime(st["First Seen"], "%Y-%m-%d").date() if st.get("First Seen") \
            else today
        said = said_on(row, first)
        if said < today - timedelta(days=config.NOT_NOW_MAX_AGE_DAYS):
            continue
        prev = parse_ddmmyy(st.get("Said On")) if st else None
        if not st or (prev and said > prev):            # new "not now" (or a repeat)
            vals = {"First Seen": first.isoformat(), "Said On": said.strftime("%d/%m/%y"),
                    "Remind On": next_working_day_on_or_after(
                        said + timedelta(days=config.NOT_NOW_DAYS)).isoformat(),
                    "Reminded At": "", "Matched": NOT_NOW.search(text).group(0)[:40]}
            writes.append((key, vals))
            st = {**st, **vals}
        if not st.get("Reminded At") and st.get("Remind On") \
                and date.fromisoformat(st["Remind On"]) <= today:
            due.append((row, st, key))
    return writes, due


def build_dm(due: list, today: date) -> str:
    n = len(due)
    lines = [f":telephone_receiver: *{n} lead{'s' if n != 1 else ''} said “not now” about "
             f"30 days ago — worth one call today*",
             "_Check Cliniko first in case they've booked. Mark the outcome on the Leads tab "
             "(booked / declined), and if it's \"not now\" again, update Last Contacted and "
             "I'll remind you in another 30 days._", ""]
    for row, st, _ in sorted(due, key=lambda x: x[1].get("Remind On", "")):
        said = st.get("Said On", "")
        overdue = date.fromisoformat(st["Remind On"]) < today - timedelta(days=3)
        clinic = row.get("Preferred Clinic") or ""
        src = row.get("Lead Source") or ""
        what = esc(not_now_text(row))[:160]
        phone = row.get("Phone") or ""
        lines.append(f"• *{esc(row.get('Patient') or '?')}* · <tel:{phone}|{phone}>"
                     + (f" · {clinic}" if clinic else "") + (f" · {src}" if src else "")
                     + (f" · {esc(row['Body Area'])}" if row.get("Body Area") else "")
                     + f"\n    said not now {said}" + (" _(overdue)_" if overdue else "")
                     + f": “{what}”")
    return "\n".join(lines)


def dm_due_now(now: datetime, last_sent: datetime | None) -> bool:
    t = now.astimezone(LONDON)
    if not working(t.date()):
        return False
    slot = t.replace(hour=config.NOT_NOW_DM_TIME[0], minute=config.NOT_NOW_DM_TIME[1],
                     second=0, microsecond=0)
    if t < slot or t - slot > timedelta(hours=3):
        return False
    return not (last_sent and last_sent >= slot)


# ---------------------------------------------------------------------------
# Sheets + run
# ---------------------------------------------------------------------------
_ws = {"ws": None, "sh": None}


def _sheet():
    if _ws["sh"] is None:
        _ws["sh"] = stl._retry(lambda: stl._gspread().open_by_key(config.LEADS_SPREADSHEET_ID),
                               "open bookings sheet")
    return _ws["sh"]


def state_ws():
    if _ws["ws"] is None:
        sh = _sheet()
        try:
            ws = stl._retry(lambda: sh.worksheet(TAB), "open not-now tab")
        except Exception as exc:
            if type(exc).__name__ != "WorksheetNotFound":
                raise
            ws = stl._retry(lambda: sh.add_worksheet(title=TAB, rows=1000, cols=len(HEADERS)),
                            "create not-now tab")
            stl._retry(lambda: ws.update(values=[HEADERS], range_name="A1"), "not-now headers")
            stl._retry(lambda: ws.freeze(rows=1), "freeze")
        _ws["ws"] = ws
    return _ws["ws"]


def read_state(ws) -> dict:
    vals = stl._retry(lambda: ws.get_all_values(), "read not-now tab")
    if not vals or vals[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError(f"'{TAB}' headers changed")
    out = {}
    for n, r in enumerate(vals[1:], start=2):
        r = r + [""] * (len(HEADERS) - len(r))
        if r[0]:
            d = {h: r[i] for h, i in COL.items()}
            d["_row"] = n
            out[r[0]] = d
    return out


def put(ws, state, key, vals):
    st = state.get(key)
    if st and st.get("_row"):
        data = [{"range": f"{stl._col_letter(COL[h])}{st['_row']}", "values": [[v]]}
                for h, v in vals.items()]
        stl._retry(lambda: ws.batch_update(data, value_input_option="RAW"), "update not-now")
        st.update(vals)
    else:
        resp = stl._retry(lambda: ws.append_row([key] + [vals.get(h, "") for h in HEADERS[1:]],
                                                value_input_option="RAW", table_range="A1"),
                          "append not-now")
        m = re.search(r"![A-Z]+(\d+)", (resp or {}).get("updates", {}).get("updatedRange", ""))
        state[key] = {**(state.get(key) or {}), "Key": key, **vals,
                      "_row": int(m.group(1)) if m else 0}


def leads_rows() -> list[dict]:
    vals = stl._retry(lambda: _sheet().worksheet("Leads").get_all_values(), "read Leads tab")
    if not vals:
        return []
    head = [h.strip() for h in vals[0]]
    return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in vals[1:] if any(r)]


def run(now: datetime | None = None, dry_run: bool = False, force: bool = False,
        log=print) -> dict:
    now = (now or datetime.now(LONDON)).astimezone(LONDON)
    today = now.date()
    if dry_run:
        try:
            ws, state = None, read_state(_sheet().worksheet(TAB))
        except Exception:
            ws, state = None, {}
    else:
        ws = state_ws()
        state = read_state(ws)
    writes, due = plan(leads_rows(), state, today)
    stats = {"tracked_new": len(writes), "due": len(due)}
    if not dry_run:
        for key, vals in writes:
            put(ws, state, key, vals)
        # re-read due entries' state after writes (row numbers for marking)
        due = [(row, state.get(key, st), key) for row, st, key in due]
    marker = state.get(MARKER)
    send = force or dm_due_now(now, _parse(marker["First Seen"]) if marker else None)
    if not send:
        return stats
    if due and not dry_run:
        stl.slack().chat_postMessage(channel=config.NOT_NOW_SLACK_USER_ID,
                                     text=build_dm(due, today), unfurl_links=False)
        for _, _, key in due:
            put(ws, state, key, {"Reminded At": _iso(now)})
        log(f"  not-now: DM'd {len(due)} lead(s)")
    if not dry_run:
        put(ws, state, MARKER, {"First Seen": _iso(now)})
    return stats


def run_once() -> int:
    import bot_state
    import slack_notifier
    try:
        stats = run(log=lambda m: print(m, flush=True))
        if stats.get("tracked_new") or stats.get("due"):
            print(f"not-now reminders: {stats}", flush=True)
        bot_state.record_result("not_now_reminders", True)
        return 0
    except Exception as exc:
        fails = bot_state.record_result("not_now_reminders", False)
        print(f"not-now reminders failed ({fails}): {exc}", flush=True)
        if fails in (3, 36):
            try:
                slack_notifier._send_dm(
                    config.CEO_SLACK_EMAIL,
                    f":warning: The day-30 \"not now\" reminders have failed {fails} times "
                    f"in a row. Last error: `{exc}`", target_label="not-now failure")
            except Exception as alert_exc:
                print(f"  (could not raise the alert: {alert_exc})", flush=True)
        return stl.HANDLED_ERROR


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if "--once-if-due" in sys.argv:
        sys.exit(run_once())
    print(run(dry_run=True))
