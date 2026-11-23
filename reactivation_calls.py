"""Reactivation call cards in #reactivationcalls (Martin, 21 Nov 2026).

Every drop-off someone should ring becomes a Slack card, every call is a tap,
and the next call is timed for them. The W/C drop-off tabs stay the master.

The flow
  1. DROP-OFF WEEK — new drop-offs (this week's W/C tab, Next Action Step
     blank) appear at 10:00 for reception: call 1, and call 2 the next working
     day if no answer. Rows the physio marked Clinical / not appropriate /
     contacting directly stay off.
  2. MONDAY REVIEW — 08:30 on the first working day of the week, Sinead Rocks
     and Marty are tagged with last week's drop-offs that need a "Next Action
     Step" (plus anyone still unreached and any note whose date couldn't be
     read). Setting the column sends a card to that owner within ~5 minutes:
        Reception to call / Sinead Rocks to call / Marty to call
                                   -> card in #reactivationcalls, owner tagged
        Physio to call             -> card DM'd to the row's physio
        Physio To Complete Tricky Patient Form -> DM to the physio + form link
        Leave / Rebooked           -> finished, never posted again
     Each owner gets 2 calls (retry next working day), then it waits for a
     new decision at the next review.
  3. DATED NOTES — a new note in Reception Reactivation Notes or Marty's
     Comments like "contact patient tuesday evening", "w/b 30th nov", "ring
     2/12", "tomorrow", "next week" brings the card back on that day (week
     only = that Monday). The card quotes the note. Notes the bot writes
     itself ("24/11 Kerry: no answer (call 1)") are ignored. Notes that look
     dated but can't be read are flagged in the Monday review.

Every tap writes Reactivation Status, Next Action Step (Rebooked / Leave) and a
dated line in Reception Reactivation Notes. Rebooked also posts the win in
#reactivation ("Name @physio", or "Name IA" for a first visit never attended).

State: "Reactivation Calls" tab on the drop-off sheet, one row per drop-off.

Run:  python reactivation_calls.py               dry run: counts only, no names
      python reactivation_calls.py --once-if-due  what bot_worker.py runs (5 min)
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import phase2
import speed_to_lead as stl
import campaign
from speed_to_lead import _iso, _parse, esc

try:   # needs the Cliniko API key just to import
    from marketing import cliniko
except Exception:  # noqa: BLE001
    cliniko = None

# All imports at module level: the button handler runs in a server.py request
# thread, and a lazy import there can see a half-loaded module (2026-11-08).

LONDON = ZoneInfo("Europe/London")

HEADERS = ["Appt ID", "Tab", "Patient ID", "Posted At", "Slack Channel", "Slack TS",
           "Attempts", "Last Attempt At", "Next Due", "Outcome", "Attempt Log",
           # 2026-11-21 review flow + dated notes:
           "Owner", "Notes Seen", "Scheduled For", "Scheduled Note", "Sched Posted",
           "Note Flag",
           # 2026-11-23: dated notes already on the sheet hold a card (once per note).
           "Holds Checked"]
COL = {h: i for i, h in enumerate(HEADERS)}
LIST_KEY, REVIEW_KEY, BASELINE_KEY = "__list__", "__review__", "__baseline__"

NA_RECEPTION, NA_SINEAD, NA_MARTY = "Reception to call", "Sinead Rocks to call", "Marty to call"
NA_PHYSIO, NA_TRICKY = "Physio to call", "Physio To Complete Tricky Patient Form"
NA_LEAVE, NA_REBOOKED = "Leave", "Rebooked"
CALL_OWNERS = {NA_RECEPTION, NA_SINEAD, NA_MARTY, NA_PHYSIO}
WEEK_OWNER = "drop-off week"          # Owner value for reception's first-week calls

OPEN_STATUSES = {"", "pending", "contact_attempted"}
BOOKED_STATUSES = {"reactivated", "rebooked"}
PHYSIO_OWNS = {"not appropriate", "physio contacting patient directly",
               "tricky patient form"}
DONE_OUTCOMES = {"rebooked", "not interested", "leave", "skipped"}

DROP_TYPE_LABELS = {"cancelled": "Cancelled, nothing rebooked",
                    "did_not_attend": "No-show",
                    "iadnr": "First visit, didn't rebook",
                    "iacna": "First visit cancelled",
                    "iadna": "First visit no-show"}
IA_DROP_TYPES = {"iacna", "iadna"}   # never attended this episode -> "IA"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def _day(s) -> date | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s or "").strip())
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def working(d: date) -> bool:
    return d.weekday() < 5 and d not in config.CLINIC_CLOSED_DAYS


def add_working_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if working(d):
            n -= 1
    return d


def next_working_day_on_or_after(d: date) -> date:
    while not working(d):
        d += timedelta(days=1)
    return d


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_of_tab(title: str) -> date | None:
    try:
        return datetime.strptime(str(title).replace("W/C ", ""), "%d %b %Y").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Reading dates out of free-text notes (pure; tested)
# ---------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_RE = (r"\b(mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)"
           r"(?:day|sday|nesday|rsday|urday)?\b")
_INTENT = re.compile(r"\b(call|ring|rang|contact|phone|try|follow|f/u|f/up|fu|chase|text|"
                     r"back|again|reach|speak|book|check|catch|touch base|in touch)\w*", re.I)
# "19/11 - ...", "18/11/26 - ..." at the start of a note = when it was written.
_LEAD_DATE = re.compile(r"^\s*(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\s*[-\u2013:,.]?\s*")
_TIMEISH = re.compile(_DAY_RE + r"|\b" + _MON_RE + r"\b|\bweeks?\b|\bmonth\b|\bafter\b|"
                      r"\bwhen\b|\bchristmas\b|\bxmas\b|\bnew year\b|\bholiday\b|\bhols\b|"
                      r"\d{1,2}[/.]\d{1,2}", re.I)
# Lines the bot writes itself: "24/11 Kerry: no answer (call 1)".
_BOT_LINE = re.compile(r"^\d{1,2}/\d{1,2} [^:]{1,40}: (no answer|rebooked|call back|"
                       r"not interested)", re.I)
_NUM_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "couple": 2}


def note_segments(*texts) -> list[str]:
    out = []
    for t in texts:
        for seg in re.split(r"\s*\|\s*|\n+", str(t or "")):
            seg = seg.strip()
            if seg and not _BOT_LINE.match(seg):
                out.append(seg)
    return out


def seg_hash(seg: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", seg.lower()).encode()).hexdigest()[:8]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _with_year(m: int, d: int, base: date) -> date | None:
    x = _mk(base.year, m, d)
    if x and x < base - timedelta(days=60):          # "2/1" written in December
        x = _mk(base.year + 1, m, d)
    return x


def parse_note_date(text: str, base: date) -> date | None:
    """The day a note asks for a call on, or None. Week-only phrases give that
    week's Monday. Past dates are records ("rang 24/11"), not plans -> None."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    t = re.sub(r"\s+", " ", t.lower())
    if _BOT_LINE.match(t):
        return None
    m0 = _LEAD_DATE.match(t)
    if m0:                                       # the note's own date = its "today"
        y = m0.group(3)
        yy = (2000 + int(y) if y and len(y) == 2 else int(y)) if y else None
        written = (_mk(yy, int(m0.group(2)), int(m0.group(1))) if yy
                   else _with_year(int(m0.group(2)), int(m0.group(1)), base))
        if written and written <= base:
            base = written
        t = t[m0.end():]
    found = None
    # 0. "away / off for a week" plans a call on its own
    m = re.search(r"\b(?:away|off|holiday|hols|abroad)\b[^.|]{0,30}?\bfor (a|one|1|two|2|three|3)"
                  r" weeks?\b", t)
    if m:
        n = {"a": 1, "one": 1, "1": 1, "two": 2, "2": 2, "three": 3, "3": 3}[m.group(1)]
        return base + timedelta(days=7 * n)
    # 1. week beginning/commencing: "w/b 30th nov", "wc 30/11", "week of 30 Nov"
    m = re.search(r"\b(?:w/?b|w/?c|week (?:beginning|commencing|starting|of))\s*(?:the\s*)?"
                  r"(\d{1,2})(?:st|nd|rd|th)?(?:\s*(?:of\s*)?" + _MON_RE + r"|[/.](\d{1,2}))", t)
    if m:
        mon = _MONTHS[m.group(2)[:3]] if m.group(2) else int(m.group(3))
        d = _with_year(mon, int(m.group(1)), base)
        if d:
            return monday_of(d) if monday_of(d) >= monday_of(base) else None
    intent = bool(_INTENT.search(t))
    # 2. explicit dates
    for pat, order in ((r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s*)?" + _MON_RE + r"\b", "dm"),
                       (r"\b" + _MON_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", "md"),
                       (r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", "num")):
        m = re.search(pat, t)
        if not m:
            continue
        if order == "dm":
            found = _with_year(_MONTHS[m.group(2)[:3]], int(m.group(1)), base)
        elif order == "md":
            found = _with_year(_MONTHS[m.group(1)[:3]], int(m.group(2)), base)
        else:
            y = m.group(3)
            yy = (2000 + int(y) if y and len(y) == 2 else int(y)) if y else None
            found = (_mk(yy, int(m.group(2)), int(m.group(1))) if yy
                     else _with_year(int(m.group(2)), int(m.group(1)), base))
        break
    if found:
        return found if (intent and found > base) else None
    if not intent:
        return None
    # 3. relative
    if re.search(r"\b(today|tonight|this (evening|afternoon)|later today)\b", t):
        return base
    if re.search(r"\btomorrow\b", t):
        return base + timedelta(days=1)
    if re.search(r"\bend of next week\b", t):
        return monday_of(base) + timedelta(days=11)
    if re.search(r"\b(start|beginning) of next week\b", t):
        return monday_of(base) + timedelta(days=7)
    if re.search(r"\bnext week\b", t):
        return monday_of(base) + timedelta(days=7)
    if re.search(r"\bend of (the |this )?week\b", t):
        return max(monday_of(base) + timedelta(days=4), base)
    if re.search(r"\blater (in |this )?(the )?week\b", t):
        return (monday_of(base) + timedelta(days=3) if base.weekday() < 3
                else monday_of(base) + timedelta(days=4) if base.weekday() == 3
                else monday_of(base) + timedelta(days=7))
    if re.search(r"\bend of (the |this )?month\b", t):
        nxt = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        d = nxt - timedelta(days=1)
        while not working(d):
            d -= timedelta(days=1)
        return d
    if re.search(r"\bnext month\b", t):
        nxt = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        return next_working_day_on_or_after(nxt)
    m = re.search(r"\b(?:in\s+)?(\d+|a|one|two|three|four|couple)(?: of)?\s+(day|week)s?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        return base + timedelta(days=n * (7 if m.group(2) == "week" else 1))
    # 4. weekday: next one after today ("next tuesday" said on Monday = tomorrow)
    m = re.search(_DAY_RE, t)
    if m:
        wd = _DAYS[m.group(1)[:3]]
        ahead = (wd - base.weekday()) % 7 or 7
        return base + timedelta(days=ahead)
    return None


def looks_dated(text: str) -> bool:
    """A note that probably meant a date (flag it if parse_note_date failed)."""
    return bool(_INTENT.search(text or "")) and bool(_TIMEISH.search(text or ""))


# ---------------------------------------------------------------------------
# Who rings whom, and when (pure; tested)
# ---------------------------------------------------------------------------
def row_open(row: dict) -> bool:
    """Not booked, and not closed by the Next Action Step."""
    status = str(row.get("Reactivation Status") or "").strip().lower()
    na = str(row.get("Next Action Step") or "").strip()
    return status not in BOOKED_STATUSES and na not in (NA_LEAVE, NA_REBOOKED)


def week_phase_ok(row: dict) -> bool:
    """Reception's first-week calls: open status, and the physio hasn't kept it."""
    if not str(row.get("appointment_id") or "").strip():
        return False
    if str(row.get("Reactivation Status") or "").strip().lower() not in OPEN_STATUSES:
        return False
    if str(row.get("Clinical / Non-Clinical") or "").strip().lower() == "clinical":
        return False
    return str(row.get("Next Step (Physio)") or "").strip().lower() not in PHYSIO_OWNS


def landed(row: dict) -> date | None:
    return _day(row.get("pulled_at")) or _day(row.get("Cancellation Date")) \
        or _day(row.get("Appointment Date"))


def attempts_of(row: dict, st: dict | None) -> int:
    if st and str(st.get("Attempts") or "").isdigit():
        return int(st["Attempts"])
    if str(row.get("Reactivation Status") or "").strip().lower() == "contact_attempted":
        return 1      # reception had already rung once before the cards existed
    return 0


def _retry_due(st: dict | None, n: int, floor: date) -> date | None:
    if n >= config.REACT_MAX_ATTEMPTS:
        return None
    if n == 0:
        return floor
    last = _day((st or {}).get("Last Attempt At")) or floor
    gap = config.REACT_RETRY_GAPS_DAYS[min(n, len(config.REACT_RETRY_GAPS_DAYS)) - 1]
    return next_working_day_on_or_after(max(add_working_days(last, gap), floor))


def _untapped_today(st: dict, n: int, today: date) -> bool:
    """A card already out today that nobody has tapped yet: not a retry."""
    return bool(st.get("Slack TS")) and n == 0 and _day(st.get("Posted At")) == today


def decide(row: dict, st: dict | None, today: date) -> dict | None:
    """What's owed on this row today: {"owner", "due", "kind", "new_card"} or None.
    kind: week | assigned | retry | scheduled | tricky."""
    if not row_open(row) or not str(row.get("appointment_id") or "").strip():
        return None
    st = st or {}
    na = str(row.get("Next Action Step") or "").strip()
    owner = na if na in CALL_OWNERS | {NA_TRICKY} else (WEEK_OWNER if not na else "")
    sched = _day(st.get("Scheduled For"))
    if sched and not st.get("Sched Posted"):
        if sched > today:
            return None                           # someone asked for a later day
        return {"owner": owner if owner not in ("", WEEK_OWNER, NA_TRICKY) else NA_RECEPTION,
                "due": sched, "kind": "scheduled", "new_card": True}
    if st.get("Outcome") == "call back" and _day(st.get("Next Due")):
        due = _day(st["Next Due"])
        return ({"owner": st.get("Owner") or NA_RECEPTION, "due": due, "kind": "retry",
                 "new_card": False} if due <= today else None)
    if na == NA_TRICKY:
        return (None if st.get("Owner") == NA_TRICKY else
                {"owner": NA_TRICKY, "due": today, "kind": "tricky", "new_card": True})
    if na in CALL_OWNERS:
        if st.get("Owner") != na:                 # a fresh decision: new card now
            return {"owner": na, "due": today, "kind": "assigned", "new_card": True}
        if st.get("Outcome") in DONE_OUTCOMES:
            return None
        if _untapped_today(st, int(st.get("Attempts") or 0), today):
            return None
        due = _retry_due(st, int(st.get("Attempts") or 0), today)
        return ({"owner": na, "due": due, "kind": "retry", "new_card": False}
                if due and due <= today else None)
    if na:                                        # an option we don't act on
        return None
    # Blank: reception's drop-off week only; older weeks wait for the review.
    if week_of_tab(row.get("_tab", "")) != monday_of(today) or not week_phase_ok(row):
        return None
    if st.get("Outcome") in DONE_OUTCOMES:
        return None
    land = landed(row)
    if not land:
        return None
    n = attempts_of(row, st)
    if _untapped_today(st, n, today):
        return None
    due = _retry_due(st, n, next_working_day_on_or_after(land))
    if not due or due > today:
        return None
    return {"owner": WEEK_OWNER, "due": due, "kind": "week" if not st.get("Slack TS") else "retry",
            "new_card": not st.get("Slack TS")}


def needs_review(row: dict) -> bool:
    """Monday review: open, no decision yet."""
    return (row_open(row) and not str(row.get("Next Action Step") or "").strip()
            and str(row.get("Reactivation Status") or "").strip().lower() in OPEN_STATUSES
            and bool(str(row.get("appointment_id") or "").strip()))


def apply_tap(choice: str, st: dict, today: date, who: str) -> tuple[dict, str, str, str]:
    """(state cells, new Reactivation Status or "", new Next Action Step or "", note)."""
    n = int(st.get("Attempts") or 0) + 1
    stamp = today.strftime("%d/%m")
    cells = {"Attempts": str(n), "Last Attempt At": today.isoformat()}
    if choice == "rebooked":
        cells["Outcome"] = "rebooked"
        return cells, "", NA_REBOOKED, f"{stamp} {who}: rebooked (call {n})"
    if choice == "notinterested":
        cells["Outcome"] = "not interested"
        return cells, "leave", NA_LEAVE, f"{stamp} {who}: not interested (call {n})"
    if choice == "callback":
        due = next_working_day_on_or_after(today + timedelta(days=config.REACT_CALLBACK_DAYS))
        cells.update({"Outcome": "call back", "Next Due": due.isoformat()})
        return cells, "contact_attempted", "", f"{stamp} {who}: call back w/c {due:%d/%m} (call {n})"
    cells["Outcome"] = "no answer"
    return cells, "contact_attempted", "", f"{stamp} {who}: no answer (call {n})"


# ---------------------------------------------------------------------------
# Slack text (pure; tested)
# ---------------------------------------------------------------------------
def owner_mention(owner: str, row: dict) -> str:
    if owner == NA_SINEAD:
        return f"<@{config.REACT_SINEAD_ROCKS_USER_ID}>"
    if owner == NA_MARTY:
        return f"<@{config.REACT_MARTY_USER_ID}>"
    if owner in (NA_PHYSIO, NA_TRICKY):
        return physio_mention(row.get("Physio"))
    return f"<@{config.STL_RECEPTION_USER_ID}>"


def card_text(row: dict, phone: str, n_next: int, head: str = "") -> str:
    clinic = str(row.get("Clinic") or "").replace("Elite Physiotherapy ", "")
    drop = DROP_TYPE_LABELS.get(str(row.get("Drop-off Type") or "").lower(),
                                row.get("Drop-off Type") or "")
    when = _day(row.get("Cancellation Date")) or _day(row.get("Appointment Date"))
    reason = row.get("Cancellation Reason") or ""
    bits = ([head] if head else []) + [
        f"*{esc(row.get('Patient Name') or '?')}* · <tel:{phone}|{phone}> · {clinic}"
        f" · {esc(row.get('Physio') or '')}"
        + (f" · {esc(row['Body Area'])}" if row.get("Body Area") else ""),
        f"{drop}"
        + (f" · {when.strftime('%a %d %b')}" if when else "")
        + (f" · session {row['Session #']}" if row.get("Session #") else "")
        + (f" · reason: {esc(reason)}" if reason else "")]
    physio = [x for x in (row.get("Next Step (Physio)"), row.get("Physio Action"),
                          row.get("Physio Reactivation Notes")) if str(x or "").strip()]
    if physio:
        bits.append(f"_Physio: {esc(' · '.join(str(x).strip() for x in physio))[:300]}_")
    for col in ("Marty's Comments", "Reception Reactivation Notes"):
        if str(row.get(col) or "").strip():
            bits.append(f"_{col}: {esc(str(row[col]))[-200:]}_")
    bits.append(f"Call {n_next} of {config.REACT_MAX_ATTEMPTS}. Ask where they are now vs "
                "where they wanted to be, why it's worth coming back, what we'd do, then "
                "book a time before you hang up.")
    return "\n".join(bits)


def card_head(kind: str, owner: str, row: dict, st: dict | None) -> str:
    who = owner_mention(owner, row)
    if kind == "scheduled":
        return f":calendar: *Scheduled call* for {who} — from the note: “{esc((st or {}).get('Scheduled Note', ''))[:160]}”"
    if kind == "assigned":
        return f":clipboard: *{owner}* (Monday review) — {who}"
    return f":telephone_receiver: *New drop-off* — {who}"


def win_text(row: dict, mention: str) -> str:
    """#reactivation post in the team's format: "Name @physio" or "Name IA"."""
    name = str(row.get("Patient Name") or "?").strip()
    if str(row.get("Drop-off Type") or "").strip().lower() in IA_DROP_TYPES:
        return f"{name} IA"
    return f"{name} {mention}".strip()


def tap_note(choice: str, cells: dict, user_id: str, now: datetime,
             win_posted: bool | None = None) -> str:
    t = now.strftime("%H:%M")
    n = cells["Attempts"]
    if choice == "rebooked":
        win = (" Posted in #reactivation for you." if win_posted else
               " Couldn't post in #reactivation, so please post the win there."
               if win_posted is False else "")
        return (f"✅ *Rebooked* by <@{user_id}> at {t}.{win} The sheet confirms it "
                "from Cliniko tomorrow morning.")
    if choice == "notinterested":
        return (f"✋ *Not interested* — <@{user_id}> at {t}. Marked *Leave*. Please add "
                "their real reason in Reception Reactivation Notes (not \"Other\").")
    if choice == "callback":
        due = date.fromisoformat(cells["Next Due"])
        return (f"📅 *Call back* — <@{user_id}> at {t}. Back on the list "
                f"{due.strftime('%a %d %b')}.")
    if int(n) >= config.REACT_MAX_ATTEMPTS:
        return (f"📵 *No answer* — call {n} of {config.REACT_MAX_ATTEMPTS} by <@{user_id}> "
                f"at {t}. That's the last try for now: it goes to Monday's review. "
                "Add a dated note (e.g. \"ring Thursday evening\") to bring it back sooner.")
    return (f"📵 *No answer* — call {n} of {config.REACT_MAX_ATTEMPTS} by <@{user_id}> "
            f"at {t}. Back on the list next working day. No voicemail with a question.")


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


def _slot(now: datetime, hm) -> datetime:
    return now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def list_due_now(now: datetime, last_sent: datetime | None) -> bool:
    t = now.astimezone(LONDON)
    if not working(t.date()):
        return False
    slot = _slot(t, config.REACT_LIST_TIME)
    if t < slot or t - slot > timedelta(minutes=config.REACT_LIST_GRACE_MIN):
        return False
    return not (last_sent and last_sent >= slot)


def review_due_now(now: datetime, last_sent: datetime | None) -> bool:
    """08:30 on the first working day of the week (Tuesday after a bank holiday)."""
    t = now.astimezone(LONDON)
    d = t.date()
    if not working(d) or next_working_day_on_or_after(monday_of(d)) != d:
        return False
    slot = _slot(t, config.REACT_REVIEW_TIME)
    if t < slot or t - slot > timedelta(hours=3):
        return False
    return not (last_sent and last_sent >= slot)


def in_post_window(now: datetime) -> bool:
    t = now.astimezone(LONDON)
    a, b = config.REACT_POST_WINDOW
    return working(t.date()) and _slot(t, a) <= t < _slot(t, b)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
def _plain(name) -> str:
    t = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.replace("'", "").lower()).strip()


def physio_email(physio: str) -> str:
    low = _plain(physio)
    if not low:
        return ""
    for m in config.TEAM:
        names = [m.get("display", "")] + list(m.get("full_names") or [])
        if any(low == _plain(n) for n in names if n):
            return m.get("slack_email") or m.get("clinic_email") or ""
    return ""


def physio_mention(physio: str) -> str:
    email = physio_email(physio)
    if email:
        try:
            return f"<@{stl._uid(email)}>"
        except Exception:
            pass
    return str(physio or "").strip()


def post_win(row: dict) -> bool:
    if not config.REACT_WINS_CHANNEL:
        return False
    try:
        stl.slack().chat_postMessage(channel=config.REACT_WINS_CHANNEL,
                                     text=win_text(row, physio_mention(row.get("Physio"))),
                                     unfurl_links=False)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  react: #reactivation post failed: {exc}", flush=True)
        return False


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
            head = stl._retry(lambda: ws.row_values(1), "react headers")
            if head and len(head) < len(HEADERS) and head == HEADERS[:len(head)]:
                if ws.col_count < len(HEADERS):
                    stl._retry(lambda: ws.add_cols(len(HEADERS) - ws.col_count), "widen react tab")
                stl._retry(lambda: ws.update(values=[HEADERS[len(head):]],
                                             range_name=f"{stl._col_letter(len(head))}1"),
                           "add react headers")
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


def _state_or_empty() -> dict:
    try:
        return read_state(stl._retry(lambda: sheet().worksheet(config.REACT_STATE_TAB),
                                     "open react tab"))
    except Exception:
        return {}


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


def put_state(ws, state: dict, aid: str, values: dict):
    """Update the row if it exists, else append it (and remember it)."""
    st = state.get(aid)
    if st and st.get("_row"):
        update_state(ws, st["_row"], values)
        st.update(values)
    else:
        row = append_state(ws, {"Appt ID": aid, **values})
        state[aid] = {**{h: "" for h in HEADERS}, "Appt ID": aid, **values, "_row": row}


def dropoff_rows(today: date) -> list[dict]:
    """Rows from the W/C tabs in range, as {header: value} + '_tab', '_row'."""
    floor = monday_of(max(config.REACT_START_DATE,
                          today - timedelta(days=config.REACT_LOOKBACK_DAYS)))
    tabs = [w for w in stl._retry(lambda: sheet().worksheets(), "list tabs")
            if w.title.startswith("W/C ") and (week_of_tab(w.title) or date.min) >= floor]
    if not tabs:
        return []
    resp = stl._retry(lambda: sheet().values_batch_get([f"'{w.title}'" for w in tabs]),
                      "read W/C tabs")
    out = []
    for w, vr in zip(tabs, resp.get("valueRanges", [])):
        vals = vr.get("values", [])
        if not vals:
            continue
        head = [h.strip() for h in vals[0]]
        for n, r in enumerate(vals[1:], start=2):
            r = r + [""] * (len(head) - len(r))
            d = dict(zip(head, r))
            d["_tab"], d["_row"], d["_gid"] = w.title, n, w.id
            out.append(d)
    return out


def find_sheet_row(appt_id: str, tab_hint: str = "") -> tuple:
    sh = sheet()
    tabs = [w for w in sh.worksheets() if w.title.startswith("W/C ")]
    tabs.sort(key=lambda w: (w.title != tab_hint,
                             -((week_of_tab(w.title) or date.min).toordinal())))
    for w in tabs[:12]:
        vals = stl._retry(lambda: w.get_all_values(), f"read {w.title}")
        if not vals:
            continue
        head = [h.strip() for h in vals[0]]
        if "appointment_id" not in head:
            continue
        i = head.index("appointment_id")
        for n, r in enumerate(vals[1:], start=2):
            if len(r) > i and r[i] == appt_id:
                return w, n, head, dict(zip(head, r + [""] * (len(head) - len(r))))
    return None, None, None, None


def write_sheet(w, n: int, head: list, current: dict, status: str, next_action: str,
                note: str):
    data = []

    def cell(label, value):
        if label in head:
            data.append({"range": f"{stl._col_letter(head.index(label))}{n}",
                         "values": [[value]]})
    if status:
        cell("Reactivation Status", status)
    if next_action:
        cell("Next Action Step", next_action)
    if note:
        old = str(current.get("Reception Reactivation Notes") or "").strip()
        cell("Reception Reactivation Notes", f"{old} | {note}" if old else note)
    if data:
        stl._retry(lambda: w.batch_update(data, value_input_option="RAW"),
                   f"write drop-off row {n}")


# ---------------------------------------------------------------------------
# The run (every 5 min from bot_worker.py)
# ---------------------------------------------------------------------------
def _patient_for(appt_id: str) -> tuple[str, str]:
    # A plain GET 404s for cancelled appointments; the filtered search finds both.
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


def scan_notes(rows, state, ws, today, baseline: bool, dry_run: bool, log) -> int:
    """New note lines -> Scheduled For. Returns how many were scheduled."""
    n_sched = 0
    for row in rows:
        aid = str(row.get("appointment_id") or "").strip()
        if not aid:
            continue
        segs = note_segments(row.get("Reception Reactivation Notes"), row.get("Marty's Comments"))
        if not segs:
            continue
        st = state.get(aid) or {}
        seen = set(filter(None, str(st.get("Notes Seen") or "").split(",")))
        new = [s for s in segs if seg_hash(s) not in seen]
        if not new:
            continue
        vals = {"Notes Seen": ",".join(sorted(seen | {seg_hash(s) for s in new})),
                "Tab": row["_tab"]}
        if not baseline:
            for seg in new:
                d = parse_note_date(seg, today)
                if d:
                    vals.update({"Scheduled For": next_working_day_on_or_after(d).isoformat(),
                                 "Scheduled Note": seg[:200], "Sched Posted": ""})
                    n_sched += 1
                elif looks_dated(seg):
                    vals["Note Flag"] = seg[:200]
        if not dry_run:
            put_state(ws, state, aid, vals)
    return n_sched


def note_hold(segs: list[str], today: date) -> tuple[date | None, str]:
    """Earliest future call date in these note lines (pure)."""
    best, note = None, ""
    for seg in segs:
        d = parse_note_date(seg, today)
        if d and d > today:
            d = next_working_day_on_or_after(d)
            if best is None or d < best:
                best, note = d, seg
    return best, note


def apply_holds(rows, state, ws, today, dry_run: bool, log) -> int:
    """Dated notes already on the sheet (from before the cards, or the Monday
    review's comments) hold the card until that day. Each note line is read
    once (its date is fixed relative to the day it was first seen, or to the
    date written at its start). An open card gets a thread note saying so."""
    held = 0
    for row in rows:
        aid = str(row.get("appointment_id") or "").strip()
        if not aid or not row_open(row):
            continue
        segs = note_segments(row.get("Reception Reactivation Notes"), row.get("Marty's Comments"))
        st = state.get(aid) or {}
        checked = set(filter(None, str(st.get("Holds Checked") or "").split(",")))
        new = [x for x in segs if seg_hash(x) not in checked]
        if not new:
            continue
        vals = {"Holds Checked": ",".join(sorted(checked | {seg_hash(x) for x in new})),
                "Tab": row["_tab"]}
        d, note = note_hold(new, today)
        cur = _day(st.get("Scheduled For")) if not st.get("Sched Posted") else None
        if d and (cur is None or cur <= today or d < cur):
            vals.update({"Scheduled For": d.isoformat(), "Scheduled Note": note[:200],
                         "Sched Posted": ""})
            held += 1
            if st.get("Slack TS") and st.get("Outcome") not in DONE_OUTCOMES and not dry_run:
                try:
                    stl.slack().chat_postMessage(
                        channel=st["Slack Channel"], thread_ts=st["Slack TS"], unfurl_links=False,
                        text=(f":pause_button: *On hold* — the notes say “{esc(note)[:160]}”. "
                              f"No need to ring yet: this comes back as a new card on "
                              f"*{d.strftime('%a %d %b')}*."))
                except Exception as exc:  # noqa: BLE001
                    log(f"  react {aid}: hold note failed ({exc})")
        if not dry_run:
            put_state(ws, state, aid, vals)
    return held


def _post_card(row, st, kind, owner, now, ws, state, log) -> bool:
    """Post one card (or the tricky-form DM). Returns True if posted."""
    slack = stl.slack()
    aid = str(row["appointment_id"]).strip()
    pid, phone = _patient_for(aid)
    if pid and kind != "tricky" and cliniko.has_future_booking(pid):
        log(f"  react {aid}: already has a future booking - skipped")
        if kind == "scheduled":
            put_state(ws, state, aid, {"Sched Posted": now.date().isoformat()})
        return False
    phone = phone or "no mobile on file"
    if owner in (NA_PHYSIO, NA_TRICKY):
        email = physio_email(row.get("Physio"))
        channel = stl._uid(email) if email else config.REACT_CHANNEL
        if not email:
            log(f"  react {aid}: no Slack for physio - posting in the channel")
    else:
        channel = config.REACT_CHANNEL
    if kind == "tricky":
        text = (f":memo: *Monday review:* please complete the "
                f"<{config.TRICKY_PATIENT_URL}|Tricky Patient Form> for "
                f"*{esc(row.get('Patient Name') or '?')}*"
                + (f" ({esc(row['Body Area'])})" if row.get("Body Area") else "")
                + f" — {owner_mention(owner, row)}")
        slack.chat_postMessage(channel=channel, text=text, unfurl_links=False)
        put_state(ws, state, aid, {"Owner": NA_TRICKY, "Posted At": _iso(now), "Tab": row["_tab"],
                                   "Patient ID": pid})
        return True
    # A review decision or a dated note starts a fresh run of calls.
    n_prev = 0 if kind in ("assigned", "scheduled") else attempts_of(row, st)
    head = card_head(kind, owner, row, st)
    resp = slack.chat_postMessage(
        channel=channel, unfurl_links=False,
        text=f"Reactivation call: {row.get('Patient Name')}",
        blocks=[{"type": "section", "text": {"type": "mrkdwn",
                                             "text": card_text(row, phone, n_prev + 1, head)}},
                actions_block(aid)])
    vals = {"Tab": row["_tab"], "Patient ID": pid, "Posted At": _iso(now),
            "Slack Channel": resp["channel"], "Slack TS": resp["ts"],
            "Attempts": str(n_prev), "Owner": owner}
    if kind == "scheduled":
        vals["Sched Posted"] = now.date().isoformat()
        vals["Outcome"] = ""
    if kind == "assigned":
        vals["Outcome"] = ""
    put_state(ws, state, aid, vals)
    return True


def _review_text(rows, state, today) -> str | None:
    last_week = monday_of(today) - timedelta(days=7)
    need = [r for r in rows if week_of_tab(r["_tab"]) == last_week and needs_review(r)]
    older = [r for r in rows if (week_of_tab(r["_tab"]) or date.max) < last_week
             and needs_review(r)]
    stuck = [r for r in rows if row_open(r)
             and str(r.get("Next Action Step") or "").strip() in CALL_OWNERS
             and (state.get(str(r.get("appointment_id"))) or {}).get("Outcome") == "no answer"
             and int((state.get(str(r.get("appointment_id"))) or {}).get("Attempts") or 0)
             >= config.REACT_MAX_ATTEMPTS]
    flags = [(r, (state.get(str(r.get("appointment_id"))) or {}).get("Note Flag"))
             for r in rows if row_open(r)
             and (state.get(str(r.get("appointment_id"))) or {}).get("Note Flag")]
    if not (need or older or stuck or flags):
        return None
    gid = next((r["_gid"] for r in rows if week_of_tab(r["_tab"]) == last_week), None)
    link = (f"<https://docs.google.com/spreadsheets/d/{config.REACT_DROPOFF_SHEET_ID}/edit#gid={gid}"
            f"|W/C {last_week:%d %b}>") if gid else f"W/C {last_week:%d %b}"
    out = [f":mag: <@{config.REACT_SINEAD_ROCKS_USER_ID}> <@{config.REACT_MARTY_USER_ID}> "
           f"*Monday review* — set the *Next Action Step* on {link}. Cards go out "
           "within a few minutes of each choice."]
    if need:
        out.append(f"*{len(need)} drop-off{'s' if len(need) != 1 else ''} from last week need a decision.*")
    if older:
        out.append(f"{len(older)} older one{'s' if len(older) != 1 else ''} still blank.")
    if stuck:
        out.append(f"*Still not reached after {config.REACT_MAX_ATTEMPTS} calls ({len(stuck)}):* "
                   + ", ".join(esc(r.get("Patient Name") or "?") for r in stuck[:15]))
    if flags:
        out.append("*Notes I couldn't read a date from* (add a day or date, e.g. \"ring 2/12\"):")
        out += [f"• {esc(r.get('Patient Name') or '?')}: “{esc(f)[:120]}”" for r, f in flags[:10]]
    return "\n".join(out)


def run(now: datetime | None = None, dry_run: bool = False, force_list: bool = False,
        log=print) -> dict:
    if cliniko is None:
        raise RuntimeError("Cliniko client unavailable")
    now = (now or datetime.now(LONDON)).astimezone(LONDON)
    today = now.date()
    ws = None if dry_run else state_ws()
    state = _state_or_empty() if dry_run else read_state(ws)
    rows = dropoff_rows(today)
    stats = {}

    # 1. Dated notes (first ever run only records what's there: no surprises).
    baseline = BASELINE_KEY not in state
    stats["scheduled"] = scan_notes(rows, state, ws, today, baseline, dry_run, log)
    if baseline and not dry_run:
        put_state(ws, state, BASELINE_KEY, {"Posted At": _iso(now), "Outcome": "meta"})
    stats["held"] = apply_holds(rows, state, ws, today, dry_run, log)

    # 2. Monday review prompt.
    rv = state.get(REVIEW_KEY)
    if review_due_now(now, _parse(rv["Posted At"]) if rv else None):
        text = _review_text(rows, state, today)
        stats["review"] = 1 if text else 0
        if text and not dry_run:
            stl.slack().chat_postMessage(channel=config.REACT_CHANNEL, text=text,
                                         unfurl_links=False)
        if not dry_run:
            put_state(ws, state, REVIEW_KEY, {"Posted At": _iso(now), "Outcome": "meta"})

    # 3. What's owed today.
    owed = []
    for row in rows:
        aid = str(row.get("appointment_id") or "").strip()
        d = decide(row, state.get(aid), today)
        if d:
            owed.append((row, d))
    lm = state.get(LIST_KEY)
    list_now = force_list or list_due_now(now, _parse(lm["Posted At"]) if lm else None)
    window = in_post_window(now)
    camp = None
    posted, retries = 0, []
    for row, d in owed:
        aid = str(row["appointment_id"]).strip()
        st = state.get(aid)
        if d["kind"] in ("assigned", "scheduled", "tricky"):
            if not window:
                continue                             # out of hours: next run in hours
        elif not list_now:
            continue                                 # week cards + retries: 10:00 list
        if not d["new_card"]:
            retries.append((row, d, st))
            continue
        if d["kind"] == "week":
            camp = _campaign_pids() if camp is None else camp
            pid, _ = _patient_for(aid)
            if pid and pid in camp:
                stats["skip campaign"] = stats.get("skip campaign", 0) + 1
                continue
        stats[d["kind"]] = stats.get(d["kind"], 0) + 1
        if dry_run:
            continue
        if _post_card(row, st, d["kind"], d["owner"], now, ws, state, log):
            posted += 1
    if retries:
        stats["retry"] = len(retries)
    if list_now and not dry_run:
        _post_retry_list(retries, ws, state, now)
        put_state(ws, state, LIST_KEY, {"Posted At": _iso(now), "Outcome": "meta"})
    if any(stats.values()):
        log(f"  react: {stats}")
    return stats


def _post_retry_list(retries, ws, state, now):
    slack = stl.slack()
    if not retries:
        return
    by_owner = {}
    for row, d, st in retries:
        by_owner.setdefault(d["owner"], []).append((row, st))
    lines = [f":telephone_receiver: *Reactivation calls to try again today ({len(retries)})* "
             "— tap the card after each call:"]
    for owner, items in by_owner.items():
        who = owner_mention(owner if owner != WEEK_OWNER else NA_RECEPTION, items[0][0])
        lines.append(f"{who}")
        for row, st in items:
            try:
                link = slack.chat_getPermalink(channel=st["Slack Channel"],
                                               message_ts=st["Slack TS"])["permalink"]
                name = f"<{link}|{esc(row.get('Patient Name') or '?')}>"
            except Exception:
                name = esc(row.get("Patient Name") or "?")
            n = attempts_of(row, st)
            why = ("call back booked" if st.get("Outcome") == "call back" else
                   "not called yet" if n == 0 else
                   f"call {n + 1} of {config.REACT_MAX_ATTEMPTS}")
            lines.append(f"• {name} · {esc(row.get('Physio') or '')} · {why}")
    slack.chat_postMessage(channel=config.REACT_CHANNEL, text="\n".join(lines),
                           unfurl_links=False)


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
        cells, status, next_action, note = apply_tap(choice, st, now.date(), _who(user_id))
        cells["Attempt Log"] = (f"{st.get('Attempt Log')}; {note}"
                                if st.get("Attempt Log") else note)
        w, n, head, current = find_sheet_row(aid, st.get("Tab", ""))
        if w is None:
            raise RuntimeError(f"drop-off row for {aid} not found")
        write_sheet(w, n, head, current, status, next_action, note)
        update_state(ws, st["_row"], cells)
        win_posted = None
        if choice == "rebooked" and st.get("Outcome") != "rebooked":   # once only
            win_posted = post_win(current)
        blocks = [b for b in original_blocks
                  if not str(b.get("block_id", "")).startswith(
                      ("react_actions|", "react_done|", "react_warn"))]
        blocks.append({"type": "context", "block_id": f"react_done|{aid}",
                       "elements": [{"type": "mrkdwn",
                                     "text": tap_note(choice, cells, user_id, now, win_posted)}]})
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
        run(log=lambda m: print(m, flush=True))
        bot_state.record_result("reactivation_calls", True)
        return 0
    except Exception as exc:
        fails = bot_state.record_result("reactivation_calls", False)
        print(f"reactivation calls failed ({fails}): {exc}", flush=True)
        if fails in (3, 12):
            try:
                slack_notifier._send_dm(
                    config.CEO_SLACK_EMAIL,
                    f":warning: Reactivation calls have failed {fails} times in a row "
                    f"— cards are *not* going out. Last error: `{exc}`",
                    target_label="reactivation calls failure")
            except Exception as alert_exc:
                print(f"  (could not raise the alert: {alert_exc})", flush=True)
        return stl.HANDLED_ERROR


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if "--once-if-due" in sys.argv:
        sys.exit(run_once())
    print(run(dry_run=True, force_list=True))
