"""Speed-to-lead responder: text every new Meta lead within ~2 minutes and
make sure someone at the desk phones them.

Flow (scope agreed with Martin 2026-11-08)
  1. Every STL_POLL_SECONDS, read the Online Leads sheet (Meta lead forms, one
     tab per clinic — the same source meta_leads.py copies hourly).
  2. A lead we have not seen before gets ONE text from the two-way number:
       "Hi Sarah, thanks for getting in touch with Elite Physio Maghera about
        your knee. You can pick a time here: <link>. Or reply with a good time
        and we'll give you a call. - Elite Physio. Reply STOP to opt out."
     The body area comes from keywords in the lead's free-text answer; no
     clear match = the "about your ..." part is left out (never guessed, and
     never a condition — a text can be read by anyone holding the phone).
     21:00–08:00 the text is held until 08:00.
  3. A Slack post (#leads; Martin's DM in safe mode) with the lead's details
     and "Called" / "No answer" buttons.
  4. Nobody pressed a button 15 min after the desk opened (desk hours in
     config.STL_DESK_HOURS) -> one message in #leads pinging reception. No
     further escalation: instead Martin gets a private end-of-day DM (desk
     close + 5 min) with each lead's text/call speed and anyone not
     actioned. Reception never sees it. Leads that
     the Online Leads sheet or the Leads tab mark booked/declined stop being
     chased; so does anything older than STL_GIVE_UP_DAYS.
  5. A lead's text reply (server.py /twilio/inbound) lands in their thread.
  6. FINAL TEXT (2026-11-21): one last text (intro video + free discovery call
     link) if still no booking FINAL_TEXT_AFTER_DAYS after the lead hit the
     sheet. See "Final text" below and config.FINAL_TEXT_*.

State lives on the "Speed to Lead" tab of the New Patient Bookings sheet —
one row per lead, so every text is sent at most once even across restarts
and the tab doubles as the speed log (Mins to Call).

Safety
  - config.STL_LIVE False (default) = safe mode: texts go to Martin's mobile
    prefixed "[TEST → number]" and Slack goes to Martin's DM.
  - Leads created before STL_START_AT are ignored entirely; leads older than
    STL_MAX_AGE_HOURS when first seen are posted but not texted.
  - Same phone within STL_REPEAT_DAYS = no second text, noted in the first
    lead's thread instead.

Run alone:  python speed_to_lead.py            (dry run: what would happen now)
            python speed_to_lead.py --tick     (one real tick, honours STL_LIVE)
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
import net_timeouts  # noqa: F401  (hard Sheets timeouts — see module)
import requests
from google.oauth2.service_account import Credentials
from slack_sdk import WebClient

import config
import slack_notifier
import meta_leads
import sheets_retry
import campaign
from marketing import twilio_client

try:   # needs the Cliniko API key just to import; tests/dry runs may lack it
    from marketing import cliniko as cliniko_api
except Exception:  # noqa: BLE001
    cliniko_api = None

# Everything this module needs is imported HERE, at import time, never inside
# a function: functions below run in background threads, and two threads
# importing the same module at once can be handed a half-initialised copy
# ("partially initialized module 'sheets_retry'" — the 2026-11-08 button bug).

LONDON = ZoneInfo("Europe/London")

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in test_speed_to_lead.py)
# ---------------------------------------------------------------------------

# Order matters only for display; first two matches are used.
BODY_AREAS = [
    ("back", r"\bback\b|spine|spinal|sciatic|lumbar|\bdisc\b|\bslipped disc"),
    ("neck", r"\bneck\b|whiplash|cervical"),
    ("shoulder", r"shoulder|rotator cuff|frozen shoulder"),
    ("hip", r"\bhips?\b|groin|glute"),
    ("knee", r"\bknees?\b|\bacl\b|menisc|patell"),
    ("ankle", r"ankle|achilles"),
    ("foot", r"\bfoot\b|\bfeet\b|\bheel|plantar|\btoes?\b"),
    ("elbow", r"elbow"),
    ("wrist", r"wrist"),
    ("hand", r"\bhands?\b|finger|thumb"),
    ("hamstring", r"hamstring"),
    ("calf", r"\bcalf\b|\bcalves\b"),
    ("thigh", r"\bthigh|\bquads?\b"),
    ("shin", r"\bshins?\b"),
]


def body_areas(issue: str) -> list[str]:
    """Body areas named in the lead's free text, in the order written, max 2."""
    text = (issue or "").lower()
    hits = []
    for area, pattern in BODY_AREAS:
        m = re.search(pattern, text)
        if m:
            hits.append((m.start(), area))
    hits.sort()
    out = []
    for _, area in hits:
        if area not in out:
            out.append(area)
    return out[:2]


# GSM-7 safe: one stray emoji or curly quote turns a 2-part SMS into 4.
_NAME_OK = re.compile(r"[^A-Za-z' \-]")


def first_name(full_name: str) -> str:
    """Tidy first name, or "" if the field doesn't look like a name."""
    raw = (full_name or "").replace("’", "'").strip()
    first = _NAME_OK.sub("", raw.split()[0]) if raw.split() else ""
    first = first.strip("'- ")
    if len(first) < 2:
        return ""
    if first.isupper() or first.islower():
        first = "-".join(p[:1].upper() + p[1:].lower() for p in first.split("-"))
    return first


def compose_text(*, name: str, clinic: str, issue: str, link: str) -> str:
    fn = first_name(name)
    hello = f"Hi {fn}" if fn else "Hi there"
    areas = body_areas(issue)
    about = f" about your {' and '.join(areas)}" if areas else ""
    return (f"{hello}, thanks for getting in touch with Elite Physio {clinic}"
            f"{about}. You can pick a time here: {link}. Or reply with a good "
            f"time and we'll give you a call. - Elite Physio. "
            f"Reply STOP to opt out.")


def _at(dt: datetime, hm: tuple[int, int]) -> datetime:
    return dt.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def text_send_time(created: datetime) -> datetime:
    """When the text may go: now, unless inside quiet hours (then 08:00)."""
    t = created.astimezone(LONDON)
    q_start, q_end = config.STL_QUIET_START, config.STL_QUIET_END
    if t >= _at(t, q_start):
        return _at(t + timedelta(days=1), q_end)
    if t < _at(t, q_end):
        return _at(t, q_end)
    return t


def desk_open_at(t: datetime) -> bool:
    t = t.astimezone(LONDON)
    hours = config.STL_DESK_HOURS.get(t.weekday())
    return bool(hours) and _at(t, hours[0]) <= t < _at(t, hours[1])


def clock_start(created: datetime) -> datetime:
    """When the 15-min call clock starts: lead time if the desk is open,
    else the next time the desk opens."""
    t = created.astimezone(LONDON)
    if desk_open_at(t):
        return t
    for d in range(0, 8):
        day = t + timedelta(days=d)
        hours = config.STL_DESK_HOURS.get(day.weekday())
        if not hours:
            continue
        opens = _at(day, hours[0])
        if opens > t:
            return opens
    raise RuntimeError("no desk hours configured")


def norm_phone(raw: str) -> str:
    """Last 10 digits — matches meta_leads/Leads tab keying."""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def fmt_mins(delta: timedelta) -> str:
    m = int(delta.total_seconds() // 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


# ---------------------------------------------------------------------------
# State tab
# ---------------------------------------------------------------------------
HEADERS = ["Lead Key", "Seen At", "Lead Created", "Name", "Phone", "Clinic",
           "Body Area", "Text Status", "Text Due", "Texted At", "Clock Start",
           "Slack Channel", "Slack TS", "Pings", "Called At", "Called By",
           "Outcome", "Mins to Call", "Closed", "Assigned",
           # 2026-11-21: final text for leads we never reached (see below).
           "Final Text Status", "Final Text At", "Replied At",
           # 2026-11-21: every Called / No answer tap counted and timed.
           "Attempts", "Attempt Log", "Last Attempt At"]
COL = {h: i for i, h in enumerate(HEADERS)}


# ---------------------------------------------------------------------------
# Call attempts (Martin 2026-11-21)
#   The bot can't see the desk phone, so an attempt = a tap on the lead's
#   card. Each tap is counted, timed and logged; the next call is due
#   STL_RETRY_GAP_HOURS later (inside desk hours), and at the times in
#   STL_DUE_LIST_TIMES #leads gets one list of everyone due a call now.
# ---------------------------------------------------------------------------
DUE_LIST_KEY = "__duelist__"   # state row holding when the last list went


def attempts_of(s: dict) -> int:
    """Taps so far. Rows from before 2026-11-21 have an Outcome but no count."""
    raw = str(s.get("Attempts") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 1 if s.get("Outcome") else 0


def last_attempt_at(s: dict) -> datetime | None:
    return _parse(s.get("Last Attempt At", "")) or _parse(s.get("Called At", ""))


def next_call_due(s: dict) -> datetime | None:
    """When this lead should next be rung, or None if we've stopped chasing
    (spoken to, closed, out of attempts, or past STL_CHASE_DAYS)."""
    if s.get("Outcome") == "called":
        return None
    if s.get("Closed") not in ("", None, "expired"):
        return None
    if str(s.get("Text Status", "")).startswith("repeat"):
        return None
    n = attempts_of(s)
    if n >= config.STL_MAX_ATTEMPTS:
        return None
    created = _parse(s.get("Lead Created", ""))
    clock = _parse(s.get("Clock Start", "")) or created
    if not created or not clock:
        return None
    last = last_attempt_at(s)
    t = clock if (n == 0 or not last) else last + timedelta(hours=config.STL_RETRY_GAP_HOURS)
    t = t if desk_open_at(t) else clock_start(t)
    if t - created > timedelta(days=config.STL_CHASE_DAYS):
        return None
    return t


def due_for_call(s: dict, at: datetime) -> bool:
    due = next_call_due(s)
    return bool(due) and due <= at


def due_line(s: dict, link: str | None = None) -> str:
    """One line of the due-a-call list (pure; `link` = card permalink)."""
    name = f"<{link}|{esc(s['Name'])}>" if link else esc(s["Name"])
    n = attempts_of(s)
    last = last_attempt_at(s)
    tried = (f"last tried {last.strftime('%a %H:%M')}" if n and last
             else "not called yet")
    area = f" · {s['Body Area']}" if s.get("Body Area") else ""
    return (f"• {name} · {s['Clinic']}{area} · <tel:{s['Phone']}|{s['Phone']}> · "
            f"call {n + 1} of {config.STL_MAX_ATTEMPTS} · {tried}")


def build_due_list(rows: list[dict], at: datetime, links: dict | None = None) -> str | None:
    """The 12:30 / 17:30 message, or None if nobody is due."""
    links = links or {}
    due = [s for s in rows if not s["Lead Key"].startswith(("__", "test:"))
           and s.get("Closed") != "test" and due_for_call(s, at)]
    if not due:
        return None
    due.sort(key=lambda s: s.get("Lead Created", ""))
    n = len(due)
    head = (f":telephone_receiver: *{n} lead{'s' if n != 1 else ''} due a call now* "
            f"({at.strftime('%H:%M')})")
    foot = ("_Tap *Called* or *No answer* on each card after you ring, so the next "
            "call is timed for a different part of the day._")
    return "\n".join([head] + [due_line(s, links.get(s["Lead Key"])) for s in due]
                     + [foot])


def list_slot_due(now: datetime, last_sent: datetime | None) -> datetime | None:
    """The list time to post now, or None. A slot counts once, and only within
    STL_DUE_LIST_GRACE_MIN of its time (a restart never posts a stale list)."""
    t = now.astimezone(LONDON)
    if t.date() in config.CLINIC_CLOSED_DAYS:
        return None
    for hm in sorted(config.STL_DUE_LIST_TIMES.get(t.weekday(), []), reverse=True):
        slot = _at(t, hm)
        if slot <= t:
            if t - slot > timedelta(minutes=config.STL_DUE_LIST_GRACE_MIN):
                return None
            if last_sent and last_sent >= slot:
                return None
            return slot
    return None


# ---------------------------------------------------------------------------
# Final text (agreed with Martin 2026-11-21)
#   1 speed-to-lead text -> calls at varied times (playbook) -> if still no
#   booking FINAL_TEXT_AFTER_DAYS after the lead reached the sheet, ONE last
#   text with the intro video + the free discovery call link. Never anyone who
#   booked, was spoken to, replied, said no, or opted out.
# ---------------------------------------------------------------------------
_NO_CONTACT_EXACT = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit",
                     "optout", "opt out", "opt-out", "no", "no thanks",
                     "no thank you", "not interested", "wrong number"}
_NO_CONTACT_PHRASES = ("not interested", "leave me alone", "do not contact",
                       "dont contact", "don't contact", "stop texting",
                       "stop messaging", "stop sending", "remove me",
                       "take me off", "no more text", "no more messages",
                       "unsubscribe", "opt out", "opt-out", "wrong number")
# Closed values that mean "do not contact again".
_NO_CONTACT_CLOSED = {"opted out", "replied no"}
# The only Closed values that still allow the final text ("expired" = the
# 3-day reminder window ended with nobody marking the lead).
_FINAL_OK_CLOSED = {"", "expired"}


def wants_no_contact(body: str) -> bool:
    """True if an inbound reply is a stop / no-thanks. Deliberately broad:
    one wrongly-silenced lead costs far less than one unwanted text."""
    t = re.sub(r"[^a-z' \-]", "", (body or "").lower().replace("’", "'")).strip()
    t = re.sub(r"\s+", " ", t)
    return t in _NO_CONTACT_EXACT or any(p in t for p in _NO_CONTACT_PHRASES)


def final_window_open(t: datetime) -> bool:
    """Final texts go out Mon–Fri 09:00–20:00 London, never on closure days."""
    t = t.astimezone(LONDON)
    if t.weekday() > 4 or t.date() in config.CLINIC_CLOSED_DAYS:
        return False
    (h0, m0), (h1, m1) = config.FINAL_TEXT_WINDOW
    return _at(t, (h0, m0)) <= t < _at(t, (h1, m1))


def compose_final_text(*, name: str, video: str, discovery: str) -> str:
    fn = first_name(name)
    hello = f"Hi {fn}" if fn else "Hi there"
    return (f"{hello}, it's Elite Physio. Sorry we keep missing each other, "
            f"no pressure at all. Here's a short video from one of our physios "
            f"about how we help: {video} If you'd rather start with a 20 min "
            f"free chat with a physio (phone or video), book it here "
            f"and choose 'Free Discovery Call': {discovery} Or just reply "
            f"and we'll ring when suits. Reply STOP to opt out.")


def final_text_decision(s: dict, *, now: datetime, lead_status: str,
                        booked_phones: set, suppressed_phones: set,
                        live: bool) -> tuple[str, str]:
    """('send'|'wait'|'skip', reason) for one state row. Pure — unit-tested."""
    fs = (s.get("Final Text Status") or "")
    if fs and not fs.startswith("test sent"):
        return "skip", "already handled"
    text_ok = ("sent",) if live else ("sent", "sent (safe mode)")
    if (s.get("Text Status") or "") not in text_ok:
        return "skip", "first text not sent"
    if (s.get("Closed") or "") not in _FINAL_OK_CLOSED:
        return "skip", f"closed: {s.get('Closed')}"
    if (s.get("Outcome") or "").lower() == "called":
        return "skip", "spoken to"
    if s.get("Replied At"):
        return "skip", "replied"
    if (lead_status or "") in _CLOSED_STATUSES:
        return "skip", f"marked {lead_status} on the sheet"
    p = norm_phone(s.get("Phone", ""))
    if not p:
        return "skip", "no usable phone"
    if p in suppressed_phones:
        return "skip", "opted out / said no"
    if p in booked_phones:
        return "skip", "booked in Cliniko"
    created = _parse(s.get("Lead Created", ""))
    if not created:
        return "skip", "no lead date"
    if created < datetime.fromisoformat(config.FINAL_TEXT_START_AT).astimezone(LONDON):
        return "skip", "before final-text start"
    age = now - created
    if age < timedelta(days=config.FINAL_TEXT_AFTER_DAYS):
        return "wait", "not due yet"
    if age > timedelta(days=config.FINAL_TEXT_MAX_AGE_DAYS):
        return "skip", "too old - not sent"
    return "send", "due"


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(LONDON).strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _parse(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LONDON)
    except ValueError:
        return None


_gc = None


def _gspread():
    global _gc
    if _gc is None:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        raw = os.environ.get("SERVICE_ACCOUNT_JSON")
        if raw:
            creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
        else:
            creds = Credentials.from_service_account_file(
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "service_account.json"), scopes=scopes)
        _gc = gspread.authorize(creds)
    return _gc


def _retry(call, label):
    return sheets_retry.gs_retry(call, label, prefix="  stl ")


_ws_cache = {"ws": None}


def state_ws():
    """The state tab, opened once per process (saves 2 metadata reads a tick
    on the Sheets quota every Elite job shares)."""
    if _ws_cache["ws"] is None:
        _ws_cache["ws"] = _open_state_ws()
    return _ws_cache["ws"]


def _open_state_ws():
    sh = _retry(lambda: _gspread().open_by_key(config.LEADS_SPREADSHEET_ID),
                "open bookings sheet")
    try:
        ws = _retry(lambda: sh.worksheet(config.STL_STATE_TAB), "open state tab")
        # 2026-11-14: "Assigned" column added (Meta leads routed to physios).
        head = _retry(lambda: ws.row_values(1), "read state headers")
        # Older tab = a prefix of HEADERS: widen it and add only the new
        # headers (Assigned 2026-11-14; Final Text columns 2026-11-21).
        if head and len(head) < len(HEADERS) and head == HEADERS[:len(head)]:
            if ws.col_count < len(HEADERS):
                _retry(lambda: ws.add_cols(len(HEADERS) - ws.col_count), "widen state tab")
            _retry(lambda: ws.update(values=[HEADERS[len(head):]],
                                     range_name=f"{_col_letter(len(head))}1"),
                   "add new state headers")
        return ws
    except Exception as exc:  # gspread.WorksheetNotFound
        if type(exc).__name__ != "WorksheetNotFound":
            raise
        ws = _retry(lambda: sh.add_worksheet(title=config.STL_STATE_TAB,
                                             rows=1000, cols=len(HEADERS)),
                    "create state tab")
        _retry(lambda: ws.update("A1", [HEADERS]), "write state headers")
        _retry(lambda: ws.freeze(rows=1), "freeze header")
        return ws


def read_state(ws) -> dict[str, dict]:
    """{lead key: row dict with '_row' = 1-based sheet row}."""
    vals = _retry(lambda: ws.get_all_values(), "read state tab")
    if not vals or vals[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError(f"'{config.STL_STATE_TAB}' headers changed — "
                           f"expected {HEADERS}")
    out = {}
    for n, row in enumerate(vals[1:], start=2):
        row = row + [""] * (len(HEADERS) - len(row))
        if row[0]:
            d = {h: row[i] for h, i in COL.items()}
            d["_row"] = n
            out[row[0]] = d
    return out


def update_cells(ws, row: int, values: dict[str, str]):
    """Write only the named cells, so the button handler and the poller never
    overwrite each other's columns."""
    data = [{"range": f"{_col_letter(COL[h])}{row}", "values": [[v]]}
            for h, v in values.items()]
    _retry(lambda: ws.batch_update(data, value_input_option="RAW"),
           f"update state row {row}")


def append_row(ws, values: dict[str, str]) -> int:
    row = [values.get(h, "") for h in HEADERS]
    resp = _retry(lambda: ws.append_row(row, value_input_option="RAW",
                                        table_range="A1"), "append state row")
    m = re.search(r"![A-Z]+(\d+)", (resp or {}).get("updates", {}).get("updatedRange", ""))
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def read_leads():
    # Direct from Meta; only the last few days matter here (older leads are
    # past STL_MAX_AGE_HOURS and never texted).
    since = (datetime.now(LONDON) - timedelta(days=3)).date()
    return _retry(lambda: meta_leads.read_source_leads(_gspread(), since=since), "read Meta leads")


_CLOSED_STATUSES = {"booked", "declined", "lost", "not appropriate"}


def leads_tab_statuses() -> dict[str, str]:
    """{Meta Lead ID or phone key: Status} from the Leads tab — reception
    marking a lead booked/declined there stops the chasing."""
    sh = _retry(lambda: _gspread().open_by_key(config.LEADS_SPREADSHEET_ID),
                "open bookings sheet")
    vals = _retry(lambda: sh.worksheet("Leads").get_all_values(), "read Leads tab")
    if not vals:
        return {}
    h = vals[0]
    try:
        si, ii, pi = h.index("Status"), h.index("Meta Lead ID"), h.index("Phone")
    except ValueError:
        return {}
    out = {}
    for r in vals[1:]:
        r = r + [""] * (len(h) - len(r))
        st = r[si].strip().lower()
        if not st:
            continue
        if r[ii]:
            out[r[ii]] = st
        if norm_phone(r[pi]):
            out[f"phone:{norm_phone(r[pi])}"] = st
    return out


# ---------------------------------------------------------------------------
# Outbound: SMS + Slack
# ---------------------------------------------------------------------------
def send_text(phone: str, body: str, live: bool | None = None) -> tuple[bool, str]:
    dest = twilio_client.normalise_uk_phone(phone)
    if not dest:
        return False, "no usable phone number"
    if live is None:
        live = config.STL_LIVE
    if not live:
        body = f"[TEST → {dest}] {body}"
        dest = config.MARKETING_TEST_PHONE
    # Straight to Twilio rather than twilio_client.send_sms: that helper also
    # obeys MARKETING_SAFE_MODE, which defaults ON for this server, so a live
    # responder would still have texted Martin. STL_LIVE is the only switch.
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        return False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set"
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={"From": config.SMS_SENDER_NUMBER, "To": dest, "Body": body},
            auth=(sid, token), timeout=20)
    except requests.RequestException as e:
        return False, f"request failed: {e}"
    if r.status_code in (200, 201):
        return True, r.json().get("sid", "sent")
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


_slack = None
_uid_cache: dict[str, str] = {}


def slack():
    global _slack
    if _slack is None:
        _slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"], timeout=20)
    return _slack


def _uid(email: str) -> str:
    if email not in _uid_cache:
        _uid_cache[email] = slack().users_lookupByEmail(email=email)["user"]["id"]
    return _uid_cache[email]


def post_channel() -> str:
    """#leads when live; Martin's DM in safe mode (or if no channel is set)."""
    if config.STL_LIVE and config.STL_CHANNEL:
        return config.STL_CHANNEL
    return _uid(config.CEO_SLACK_EMAIL)


def reception_mention() -> str:
    if config.STL_LIVE:
        return f"<@{config.STL_RECEPTION_USER_ID}>"
    return "*@reception* _(safe mode — not pinged)_"


def physio_mention(display: str) -> str:
    """Slack mention for a physio (plain bold name in safe mode)."""
    if not config.STL_LIVE:
        return f"*@{display}* _(safe mode — not pinged)_"
    try:
        return f"<@{_uid(campaign.physio_email(display))}>"
    except Exception:
        return f"*{display}*"


def assign_to_physio(ws, rec: dict, lead: dict, status: str, key: str, log=print):
    """Meta lead → the physio with the most free slots at that clinic: DM them
    the same card (with Called / No answer buttons) and note who has it."""
    try:
        pick = campaign.pick_physio(lead["clinic"])
        if not pick:
            log(f"  no physio found for {lead['clinic']} — reception keeps {key}")
            return
        display = pick[0]
        email = campaign.physio_email(display)
        target = email if config.STL_LIVE else config.CEO_SLACK_EMAIL
        blocks = lead_blocks(lead, status + f"\n:bust_in_silhouette: Assigned to *{display}* "
                             f"— please ring them in your next free slot.", key)
        if not config.STL_LIVE:
            blocks = [{"type": "context", "elements": [{"type": "mrkdwn",
                       "text": f"[SAFE MODE → would DM {display}]"}]}] + blocks
        slack().chat_postMessage(channel=_uid(target), unfurl_links=False,
                                 text=f"New lead for you — {lead['clinic']}: {lead['name']}",
                                 blocks=blocks)
        rec["Assigned"] = display
        if rec.get("_row"):
            update_cells(ws, rec["_row"], {"Assigned": display})
        _thread_note(rec, f":bust_in_silhouette: Sent to *{display}* to call.")
    except Exception as exc:
        log(f"  physio routing failed for {key}: {exc}")


def esc(text: str) -> str:
    """Slack mrkdwn escaping for patient-typed text."""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def lead_blocks(lead: dict, status_line: str, key: str) -> list:
    when = lead["created"].astimezone(LONDON).strftime("%a %d %b %H:%M")
    phone = lead["phone"]
    issue = esc((lead.get("issue") or "").strip()) or "_(left blank)_"
    if len(issue) > 300:
        issue = issue[:300] + "…"
    head = (f":rotating_light: *New lead — {lead['clinic']}* · {when}"
            + ("" if config.STL_LIVE else "  _[SAFE MODE]_"))
    body = (f"*{esc(lead['name'])}*  ·  <tel:{phone}|{phone}>\n>{issue}\n"
            + (f"_{esc(lead['ad'])}_\n" if lead.get("ad") else "")
            + status_line)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{head}\n{body}"}},
        actions_block(key),
    ]


def actions_block(key: str) -> dict:
    return {"type": "actions", "block_id": f"stl_actions|{key}", "elements": [
        {"type": "button", "style": "primary", "action_id": f"stl_called|{key}",
         "text": {"type": "plain_text", "text": "✅ Called"}, "value": key},
        {"type": "button", "action_id": f"stl_noanswer|{key}",
         "text": {"type": "plain_text", "text": "📵 No answer"}, "value": key},
    ]}


def status_line(text_status: str, clock: datetime, now: datetime) -> str:
    if clock > now:
        desk = ("Desk closed — call clock starts "
                f"{clock.astimezone(LONDON).strftime('%a %H:%M')}.")
    else:
        desk = f"Please call within {config.STL_FIRST_PING_MIN} min."
    return f"{text_status}\n{desk}"


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------
def _start_at() -> datetime:
    return datetime.fromisoformat(config.STL_START_AT).astimezone(LONDON)


def tick(now: datetime | None = None, dry_run: bool = False, log=print) -> dict:
    now = (now or datetime.now(LONDON)).astimezone(LONDON)
    ws = state_ws()
    state = read_state(ws)
    leads = read_leads()
    stats = {"new": 0, "texted": 0, "queued": 0, "pinged": 0, "closed": 0}

    # Repeat detection: phone -> earliest state row seen within REPEAT_DAYS.
    by_phone = {}
    for s in state.values():
        seen = _parse(s["Seen At"])
        p = norm_phone(s["Phone"])
        if p and seen and now - seen <= timedelta(days=config.STL_REPEAT_DAYS):
            by_phone.setdefault(p, s)

    # 1. New leads.
    start = _start_at()
    for lead in leads:
        created = lead.get("dt")
        if not created or created < start or lead["key"] in state:
            continue
        lead["created"] = created
        stats["new"] += 1
        key = lead["key"]
        p = norm_phone(lead["phone"])
        rec = {"Lead Key": key, "Seen At": _iso(now), "Lead Created": _iso(created),
               "Name": lead["name"], "Phone": lead["phone"], "Clinic": lead["clinic"],
               "Body Area": " and ".join(body_areas(lead.get("issue", ""))),
               "Clock Start": _iso(clock_start(created)), "Pings": "0"}
        repeat = by_phone.get(p) if p else None
        link = config.STL_BOOKING_LINKS.get(lead["clinic"])
        age = now - created

        if repeat:
            rec["Text Status"] = "repeat - not texted"
            rec["Closed"] = "repeat"
        elif lead.get("status") in ("booked", "declined"):
            rec["Text Status"] = f"already {lead['status']} - not texted"
            rec["Closed"] = lead["status"]
        elif not link:
            rec["Text Status"] = f"no booking link for {lead['clinic']} - not texted"
        elif age > timedelta(hours=config.STL_MAX_AGE_HOURS):
            rec["Text Status"] = f"too old when seen ({fmt_mins(age)}) - not texted"
        elif not norm_phone(lead["phone"]):
            rec["Text Status"] = "no usable phone - not texted"
        else:
            # Quiet hours apply to when the text actually goes out, not when
            # the lead came in (a lead seen late must not be texted at 07:40).
            rec["Text Due"] = _iso(text_send_time(max(created, now)))
            rec["Text Status"] = "queued"

        log(f"  new lead {key} {lead['clinic']} -> {rec['Text Status']}"
            f" (seen {fmt_mins(age)} after created)")
        if dry_run:
            if rec["Text Status"] == "queued":
                body = compose_text(name=lead["name"], clinic=lead["clinic"],
                                    issue=lead.get("issue", ""), link=link)
                log(f"    text ({len(body)} chars): {body}")
            continue

        if rec.get("Closed") and not repeat:
            append_row(ws, rec)          # already handled: log it, no post
            continue
        if repeat:
            _thread_note(repeat, f":repeat: Same number enquired again "
                                 f"({lead['clinic']}): \"{esc(lead.get('issue', '')[:200])}\" "
                                 f"— no second text sent.")
            append_row(ws, rec)
            continue

        row = append_row(ws, rec)
        rec["_row"] = row
        # Post to Slack first so a texting failure is still visible.
        clock = clock_start(created)
        status_txt = (":hourglass: Text queued" if rec["Text Status"] == "queued"
                      else f":warning: {rec['Text Status']}")
        try:
            resp = slack().chat_postMessage(
                channel=post_channel(), unfurl_links=False,
                text=f"New lead — {lead['clinic']}: {lead['name']}",
                blocks=lead_blocks(lead, status_line(status_txt, clock, now), key))
            rec["Slack Channel"], rec["Slack TS"] = resp["channel"], resp["ts"]
            if row:
                update_cells(ws, row, {"Slack Channel": resp["channel"],
                                       "Slack TS": resp["ts"]})
        except Exception as exc:
            log(f"  Slack post failed for {key}: {exc}")
        if config.STL_ROUTE_TO_PHYSIO:
            assign_to_physio(ws, rec, lead, status_line(status_txt, clock, now), key, log)
        state[key] = {**rec, "_lead": lead}
        if p:
            by_phone.setdefault(p, state[key])

    if dry_run:
        return stats

    # 2. Send queued texts that are due.
    lead_by_key = {l["key"]: l for l in leads}
    for key, s in state.items():
        if s["Text Status"] != "queued":
            continue
        due = _parse(s["Text Due"])
        if (due and due > now) or text_send_time(now) > now:
            continue                     # not due yet, or inside quiet hours
        lead = lead_by_key.get(key)
        if not lead or not s.get("_row"):
            continue
        body = compose_text(name=lead["name"], clinic=lead["clinic"],
                            issue=lead.get("issue", ""),
                            link=config.STL_BOOKING_LINKS[lead["clinic"]])
        ok, info = send_text(lead["phone"], body)
        status = (("sent" if config.STL_LIVE else "sent (safe mode)") if ok
                  else f"FAILED: {info}")
        update_cells(ws, s["_row"], {"Text Status": status,
                                     "Texted At": _iso(now) if ok else ""})
        s["Text Status"] = status
        stats["texted" if ok else "queued"] += 1
        _thread_note(s, (f":white_check_mark: Text sent {now.strftime('%H:%M')}:\n>{body}"
                         if ok else f":x: Text failed ({info}) — please phone them."))

    # 3. Remind reception (in #leads) once, 15 min after the desk opened.
    #    No further escalation — Martin gets the end-of-day report instead
    #    (2026-11-08: "I don't need a Slack post after 30 mins").
    statuses = None
    due = []
    for key, s in state.items():
        if key.startswith("__"):
            continue
        if s.get("Closed") or s.get("Called At") or s.get("Outcome"):
            continue
        if not s.get("_row") or not s.get("Slack TS"):
            continue
        clock = _parse(s["Clock Start"])
        if not clock or clock > now:
            continue
        lead = lead_by_key.get(key, {})
        if statuses is None:
            statuses = leads_tab_statuses()
        st = (lead.get("status") or statuses.get(key)
              or statuses.get(f"phone:{norm_phone(s['Phone'])}") or "")
        if st in _CLOSED_STATUSES:
            update_cells(ws, s["_row"], {"Closed": st})
            s["Closed"] = st
            _thread_note(s, f":ballot_box_with_check: Marked *{st}* on the sheet — "
                            f"no more reminders.")
            stats["closed"] += 1
            continue
        if now - clock > timedelta(days=config.STL_GIVE_UP_DAYS):
            update_cells(ws, s["_row"], {"Closed": "expired"})
            s["Closed"] = "expired"
            stats["closed"] += 1
            continue
        if (int(s.get("Pings") or 0) == 0
                and now - clock >= timedelta(minutes=config.STL_FIRST_PING_MIN)):
            due.append(s)

    if due:
        lines = []
        for s in due:
            try:
                link = slack().chat_getPermalink(channel=s["Slack Channel"],
                                                 message_ts=s["Slack TS"])["permalink"]
                name = f"<{link}|{esc(s['Name'])}>"
            except Exception:
                name = esc(s["Name"])
            waited = fmt_mins(now - _parse(s["Clock Start"]))
            lines.append(f"• {name} — {s['Clinic']} — <tel:{s['Phone']}|{s['Phone']}> "
                         f"(waiting {waited})")
        who = reception_mention()
        assigned = sorted({s.get("Assigned") for s in due if s.get("Assigned")})
        if config.STL_ROUTE_TO_PHYSIO and assigned and all(s.get("Assigned") for s in due):
            who = " ".join(physio_mention(a) for a in assigned)
        head = (f":telephone_receiver: {who} "
                f"{'these leads have' if len(due) > 1 else 'this lead has'} "
                f"not had a call yet:")
        try:
            slack().chat_postMessage(channel=post_channel(), unfurl_links=False,
                                     text=head + "\n" + "\n".join(lines))
            for s in due:
                update_cells(ws, s["_row"], {"Pings": "1"})
                stats["pinged"] += 1
        except Exception as exc:
            log(f"  reminder post failed: {exc}")

    # 3b. Final text to leads we never reached (Mon–Fri 09:00–20:00 only).
    try:
        stats["final_texted"] = send_final_texts(ws, state, now, log)
    except Exception as exc:       # never let this step break the rest
        log(f"  final texts skipped: {exc}")

    # 3c. "Due a call now" list at 12:30 / 17:30 (Fri 12:30).
    try:
        if maybe_post_due_list(ws, state, now, log):
            stats["due_list"] = 1
    except Exception as exc:       # never let this step break the rest
        log(f"  due list skipped: {exc}")

    # 4. End-of-day report to Martin's DM, once per desk day after closing.
    if maybe_send_report(ws, state, now, log):
        stats["report"] = 1

    return stats


class CacheWarming(Exception):
    """The patient-phone cache is still filling; try again next run."""


PHONE_CACHE_NAME = "stl_patient_phones"
PHONE_LOOKUP_BUDGET = 30     # seconds of Cliniko lookups per run (kill limit is 50)


def cliniko_booked_phones(since: datetime, now: datetime) -> set:
    """Last-10-digit phones of patients who BOOKED any live appointment since
    `since` (most Meta leads never become Cliniko patients, so the Leads tab
    status alone can't be trusted). Raises if Cliniko can't be read — the
    caller then sends nothing this tick rather than risk texting a patient.

    Each patient fetch is throttled to ~3/s (Cliniko's rate limit), so a
    multi-day window used to take 40-50 s and the 50 s run limit killed it,
    over and over (25 Nov 2026). Patient id -> phone is now remembered between
    runs (bot_state; numbers rarely change), so a run only fetches patients it
    hasn't seen, and stops at PHONE_LOOKUP_BUDGET with its progress saved.
    Raises CacheWarming in that case; the next run carries on."""
    if cliniko_api is None:
        raise RuntimeError("Cliniko client unavailable")
    import bot_state
    utc = ZoneInfo("UTC")
    appts = cliniko_api.appointments_created_between(
        since.astimezone(utc) - timedelta(days=1), now.astimezone(utc))
    ids = set()
    for a in appts:
        if not cliniko_api.is_cancelled(a):
            pid = cliniko_api.patient_id_of(a)
            if pid:
                ids.add(str(pid))
    cache = bot_state.load(PHONE_CACHE_NAME)
    deadline = time.time() + PHONE_LOOKUP_BUDGET
    fetched = 0
    for pid in sorted(ids - set(cache)):
        if time.time() > deadline:
            if fetched:
                bot_state.save(PHONE_CACHE_NAME, cache)
            raise CacheWarming(f"{len(ids - set(cache))} patient(s) still to look up")
        pat = cliniko_api.get_patient(pid)
        if pat is None:
            raise RuntimeError(f"could not read Cliniko patient {pid}")
        cache[pid] = norm_phone(pat.get("mobile", "")) or ""
        fetched += 1
    if fetched:
        bot_state.save(PHONE_CACHE_NAME, cache)
    return {cache[pid] for pid in ids if cache.get(pid)}


def send_final_texts(ws, state: dict, now: datetime, log=print) -> int:
    """The one last text. Safe mode (STL_LIVE or FINAL_TEXT_LIVE false) texts
    only Martin, at most FINAL_TEXT_TEST_CAP times, and never marks a lead as
    really sent."""
    if not config.FINAL_TEXT_ENABLED or not final_window_open(now):
        return 0
    live = config.STL_LIVE and config.FINAL_TEXT_LIVE   # its own switch
    video, discovery = config.FINAL_TEXT_VIDEO_LINK, config.FINAL_TEXT_DISCOVERY_LINK
    rows = [s for k, s in state.items() if not k.startswith("__") and s.get("_row")]
    suppressed = {norm_phone(s["Phone"]) for s in rows
                  if (s.get("Closed") in _NO_CONTACT_CLOSED
                      or (s.get("Final Text Status") or "").startswith("opted out"))}
    suppressed.discard("")
    statuses = None
    cands = []
    for s in rows:
        if not live and (s.get("Final Text Status") or "").startswith("test sent"):
            continue                    # already test-texted to Martin once
        # First pass without Cliniko/Leads-tab data just to find candidates.
        verdict, _ = final_text_decision(s, now=now, lead_status="", booked_phones=set(),
                                         suppressed_phones=suppressed, live=live)
        if verdict == "send":
            cands.append(s)
    if not cands:
        return 0
    if not video or not discovery:
        log(f"  final text: {len(cands)} lead(s) due but video/discovery link "
            f"not set - holding")
        return 0
    if not live:
        used = sum(1 for s in rows if (s.get("Final Text Status") or "").startswith("test sent"))
        if used >= config.FINAL_TEXT_TEST_CAP:
            return 0
    statuses = leads_tab_statuses()
    since = min(_parse(s["Lead Created"]) for s in cands)
    booked = cliniko_booked_phones(since, now)      # raises -> nothing sent
    sent = 0
    for s in cands:
        if not live and sum(1 for r in rows if (r.get("Final Text Status") or "")
                            .startswith("test sent")) >= config.FINAL_TEXT_TEST_CAP:
            break
        st = (statuses.get(s["Lead Key"]) or statuses.get(f"phone:{norm_phone(s['Phone'])}") or "")
        verdict, reason = final_text_decision(
            s, now=now, lead_status=st, booked_phones=booked,
            suppressed_phones=suppressed, live=live)
        if verdict != "send":
            if reason not in ("not due yet",) and live:
                update_cells(ws, s["_row"], {"Final Text Status": f"skipped: {reason}"})
                s["Final Text Status"] = f"skipped: {reason}"
            continue
        body = compose_final_text(name=s["Name"], video=video, discovery=discovery)
        ok, info = send_text(s["Phone"], body, live=live)
        if ok:
            status = "sent" if live else "test sent (safe mode)"
        elif "21610" in info:                       # Twilio: number opted out
            status = "opted out (Twilio)"
        else:
            status = f"FAILED: {info}"
        update_cells(ws, s["_row"], {"Final Text Status": status,
                                     "Final Text At": _iso(now) if ok else ""})
        s["Final Text Status"] = status
        if ok:
            sent += 1
            if live:
                _thread_note(s, f":white_check_mark: Final text sent {now.strftime('%H:%M')} "
                                f"(video + free discovery call link):\n>{body}")
    return sent


def maybe_post_due_list(ws, state: dict, now: datetime, log=print) -> bool:
    marker = state.get(DUE_LIST_KEY)
    slot = list_slot_due(now, _parse(marker["Seen At"]) if marker else None)
    if not slot:
        return False
    rows = [s for k, s in state.items() if not k.startswith("__") and s.get("_row")]
    cands = [s for s in rows if due_for_call(s, now)]
    if cands:
        # Leads booked/declined on the Leads tab, or booked in Cliniko, are done.
        statuses = leads_tab_statuses()
        try:
            since = min(_parse(s["Lead Created"]) for s in cands)
            booked = cliniko_booked_phones(since, now)
        except CacheWarming as exc:       # next run (1 min) carries on; slot stays due
            log(f"  due list: waiting for patient-phone cache ({exc})")
            return False
        except Exception as exc:          # list still goes; just can't drop these
            log(f"  due list: Cliniko check failed ({exc})")
            booked = set()
        for s in cands:
            p = norm_phone(s["Phone"])
            st = statuses.get(s["Lead Key"]) or statuses.get(f"phone:{p}") or ""
            if st in _CLOSED_STATUSES or (p and p in booked):
                st = st if st in _CLOSED_STATUSES else "booked"
                update_cells(ws, s["_row"], {"Closed": st})
                s["Closed"] = st
    links = {}
    for s in rows:
        if s.get("Slack TS") and due_for_call(s, now):
            try:
                links[s["Lead Key"]] = slack().chat_getPermalink(
                    channel=s["Slack Channel"], message_ts=s["Slack TS"])["permalink"]
            except Exception:
                pass
    text = build_due_list(rows, now, links)
    if text:
        live = config.STL_LIVE and config.STL_DUE_LIST_LIVE
        if live:
            channel = post_channel()
            text = text.replace("* (", f"* {reception_mention()} (", 1)
        else:
            channel = _uid(config.CEO_SLACK_EMAIL)
            text = "_[SAFE MODE — would post in #leads]_\n" + text
        slack().chat_postMessage(channel=channel, text=text, unfurl_links=False)
        log(f"  due list posted ({text.count(chr(10) + '•')} lead(s))")
    if marker:
        update_cells(ws, marker["_row"], {"Seen At": _iso(now)})
    else:
        append_row(ws, {"Lead Key": DUE_LIST_KEY, "Seen At": _iso(now),
                        "Name": "(due-a-call list marker)", "Closed": "meta"})
    return True


REPORT_KEY = "__report__"   # state row holding when the last report went


def report_due_at(now: datetime) -> datetime | None:
    """Today's report time (desk close + 5 min), or None on a closed day."""
    t = now.astimezone(LONDON)
    hours = config.STL_DESK_HOURS.get(t.weekday())
    if not hours:
        return None
    return _at(t, hours[1]) + timedelta(minutes=5)


def build_report(rows: list[dict], since: datetime, now: datetime) -> str:
    """Martin's end-of-day speed-to-lead summary. Pure: rows are state dicts."""
    rows = [{**{h: "" for h in HEADERS}, **r} for r in rows]   # new rows lack cols
    leads = [r for r in rows if not r["Lead Key"].startswith(("__", "test:"))
             and r.get("Closed") != "test"]
    new = [r for r in leads if (_parse(r["Seen At"]) or since) > since]
    new.sort(key=lambda r: r["Lead Created"])

    def text_speed(r):
        c, t = _parse(r["Lead Created"]), _parse(r["Texted At"])
        return fmt_mins(t - c) if c and t else None

    def outcome(r):
        if r.get("Outcome") == "called":
            return f"called {r['Mins to Call']} min after desk clock started"
        if r.get("Outcome") == "no answer":
            n = attempts_of(r)
            return (f"no answer × {n} (first try {r['Mins to Call']} min) - not yet reached"
                    + (" - *out of attempts*" if n >= config.STL_MAX_ATTEMPTS else ""))
        if r.get("Closed") in _CLOSED_STATUSES:
            return f"marked {r['Closed']} on the sheet"
        if r.get("Closed") == "repeat":
            return "repeat enquiry"
        clock = _parse(r["Clock Start"])
        if clock and clock > now:
            return f"waiting - desk opens {clock.strftime('%a %H:%M')}"
        return "*NOT ACTIONED*"

    def not_actioned(r):
        return outcome(r) == "*NOT ACTIONED*"

    title = (f":bar_chart: *Speed to lead — {now.strftime('%a %d %b')}*  "
             f"_(leads since {since.astimezone(LONDON).strftime('%a %H:%M')})_")
    if not new:
        out = [title, "No new leads."]
    else:
        texted = [r for r in new if r.get("Texted At")]
        called = [r for r in new if r.get("Outcome") == "called"
                  and r.get("Mins to Call", "").isdigit()]
        mins = sorted(int(r["Mins to Call"]) for r in called)
        med = mins[len(mins) // 2] if mins else None
        out = [title,
               f"*{len(new)}* lead{'s' if len(new) != 1 else ''} · "
               f"*{len(texted)}* texted · *{len(called)}* called"
               + (f" · median time to call *{med} min*" if med is not None else "")
               + (f" · within 15 min: *{sum(m <= 15 for m in mins)}/{len(mins)}*"
                  if mins else ""),
               ""]
        for r in new:
            arrived = (_parse(r["Lead Created"]) or now).strftime("%a %H:%M")
            ts = text_speed(r)
            out.append(f"• {esc(r['Name'])} ({r['Clinic']}) · in {arrived} · "
                       + (f"texted in {ts}" if ts else f"text: {r['Text Status'] or '-'}")
                       + f" · {outcome(r)}")
    older = [r for r in leads if r not in new and not_actioned(r)
             and r.get("Closed") != "expired"]
    waiting = [r for r in new if not_actioned(r)] + older
    out.append("")
    if waiting:
        out.append(f":warning: *Not actioned ({len(waiting)})* — no Called / No answer tap:")
        for r in waiting:
            out.append(f"• {esc(r['Name'])} ({r['Clinic']}) — "
                       f"<tel:{r['Phone']}|{r['Phone']}> — came in "
                       f"{(_parse(r['Lead Created']) or now).strftime('%a %H:%M')}")
    else:
        out.append(":white_check_mark: Every lead has been actioned.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Daily health check (Martin 2026-11-23): Martin and Julie aren't in #leads,
# so the end-of-day DM says whether each automation ran and whether the desk
# is tapping the cards. Reads the bots' own state tabs; never the channels.
# ---------------------------------------------------------------------------
HEALTH_JOBS = {"speed_to_lead": "Speed-to-lead", "save_calls": "Save-call alerts",
               "reactivation_calls": "Reactivation cards", "not_now_reminders": "Not-now reminders",
               "day_before_calls": "Day-before list", "team_sheet": "Team sheet sync"}


def _tab_dicts(sheet_id: str, tab: str) -> list[dict] | None:
    try:
        vals = _retry(lambda: _gspread().open_by_key(sheet_id).worksheet(tab).get_all_values(),
                      f"read {tab}")
    except Exception:
        return None
    if not vals:
        return []
    head = vals[0]
    return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in vals[1:] if r and r[0]]


def build_health(stl_rows: list[dict], save_rows, react_rows, notnow_rows,
                 job_fails: dict, now: datetime) -> list[str]:
    """Health lines for the end-of-day DM (pure; tested). Lists may be None
    when a tab couldn't be read."""
    t = now.astimezone(LONDON)
    today, ddmm, long = t.date(), t.strftime("%d/%m"), t.strftime("%a %d %b")
    iso = today.isoformat()
    ok, warn = ":white_check_mark:", ":warning:"
    out = ["*Health check*"]

    # #leads: taps today and the due-a-call lists
    leads = [r for r in stl_rows if not r.get("Lead Key", "").startswith(("__", "test:"))
             and r.get("Closed") != "test"]
    taps = sum(str(r.get("Attempt Log") or "").count(long) for r in leads)
    untapped = [r for r in leads if not r.get("Outcome") and not r.get("Closed")
                and (_parse(r.get("Clock Start", "")) or now) <= now - timedelta(hours=1)]
    out.append(f"{ok if not untapped else warn} #leads: {taps} call tap{'s' if taps != 1 else ''} today"
               + (f" · *{len(untapped)} lead{'s' if len(untapped) != 1 else ''} with no tap at all*"
                  if untapped else " · every lead tapped"))
    slots = config.STL_DUE_LIST_TIMES.get(t.weekday(), []) if today not in config.CLINIC_CLOSED_DAYS else []
    if slots and t >= _at(t, slots[0]) + timedelta(minutes=config.STL_DUE_LIST_GRACE_MIN):
        last = next((_parse(r.get("Seen At", "")) for r in stl_rows
                     if r.get("Lead Key") == DUE_LIST_KEY), None)
        out.append(f"{ok} Due-a-call lists ran today" if last and last.date() == today
                   else f"{warn} *No due-a-call list ran today*")

    # Save calls
    if save_rows is None:
        out.append(f"{warn} Save calls: couldn't read the Save Calls tab")
    else:
        alerts = [r for r in save_rows if str(r.get("Alerted At", "")).startswith(iso)]
        open_ = [r for r in save_rows if r.get("Alerted At") and not r.get("Outcome")]
        line = (f"Save calls: {len(alerts)} alert{'s' if len(alerts) != 1 else ''} today"
                + (f" · *{len(open_)} not tapped*" if open_ else ""))
        out.append(f"{warn if open_ else ok} {line}")

    # Reactivation cards
    if react_rows is None:
        out.append(f"{warn} Reactivation: couldn't read the Reactivation Calls tab")
    else:
        cards = [r for r in react_rows if not r.get("Appt ID", "").startswith("__")]
        posted = sum(1 for r in cards if str(r.get("Posted At", "")).startswith(iso))
        rtaps = sum(1 for r in cards for e in str(r.get("Attempt Log") or "").split("; ")
                    if e.startswith(ddmm + " "))
        untapped_r = [r for r in cards if r.get("Slack TS") and not r.get("Outcome")
                      and str(r.get("Attempts") or "0") in ("", "0")
                      and not (r.get("Scheduled For") and not r.get("Sched Posted")
                               and r["Scheduled For"] > iso)]
        held = sum(1 for r in cards if r.get("Scheduled For") and not r.get("Sched Posted")
                   and r["Scheduled For"] > iso)
        out.append(f"{warn if untapped_r else ok} Reactivation: {posted} card"
                   f"{'s' if posted != 1 else ''} posted, {rtaps} tap{'s' if rtaps != 1 else ''} today"
                   + (f" · *{len(untapped_r)} card{'s' if len(untapped_r) != 1 else ''} never tapped*"
                      if untapped_r else "")
                   + (f" · {held} on hold for a later date" if held else ""))
        if t.weekday() == 0 or today == next_working_monday(today):
            rv = next((r for r in react_rows if r.get("Appt ID") == "__review__"), None)
            out.append(f"{ok} Monday review posted" if rv and str(rv.get("Posted At", "")).startswith(iso)
                       else f"{warn} *Monday review didn't post*")

    # Not-now reminders
    if notnow_rows is not None:
        sent = sum(1 for r in notnow_rows if str(r.get("Reminded At", "")).startswith(iso))
        if sent:
            out.append(f"{ok} Not-now reminders: {sent} sent to Sinéad Rocks today")

    # Jobs failing right now
    bad = [f"{HEALTH_JOBS.get(k, k)} ({n} runs)" for k, n in job_fails.items() if n]
    out.append(f"{warn} *Failing:* " + ", ".join(bad) if bad else f"{ok} No automation errors")
    return out


def next_working_monday(d) -> object:
    """The first working day of d's week (Tuesday after a bank holiday)."""
    m = d - timedelta(days=d.weekday())
    while m.weekday() < 5 and m in config.CLINIC_CLOSED_DAYS:
        m += timedelta(days=1)
    return m


def health_text(state: dict, now: datetime) -> str:
    import bot_state
    fails = {}
    for job in HEALTH_JOBS:
        try:
            fails[job] = int(bot_state.load(job).get("fails", 0))
        except Exception:
            fails[job] = 0
    rows = [v for v in state.values()]
    save = _tab_dicts(config.LEADS_SPREADSHEET_ID, config.SAVE_CALL_STATE_TAB)
    react = _tab_dicts(config.REACT_DROPOFF_SHEET_ID, config.REACT_STATE_TAB)
    notnow = _tab_dicts(config.LEADS_SPREADSHEET_ID, "Not Now Reminders")
    return "\n".join(build_health(rows, save, react, notnow, fails, now))


def maybe_send_report(ws, state: dict, now: datetime, log=print) -> bool:
    due = report_due_at(now)
    if not due or now < due:
        return False
    marker = state.get(REPORT_KEY)
    last = _parse(marker["Seen At"]) if marker else None
    if last and last >= due:
        return False                         # already sent today
    since = last or datetime.fromisoformat(config.STL_START_AT).astimezone(LONDON)
    text = build_report(list(state.values()), since, now)
    try:
        text += "\n\n" + health_text(state, now)
    except Exception as exc:  # noqa: BLE001  never block the report
        text += f"\n\n:warning: Health check couldn't run: `{exc}`"
    if not config.STL_LIVE:
        text = "_[SAFE MODE]_\n" + text
        emails = [config.CEO_SLACK_EMAIL]
    else:
        emails = config.STL_REPORT_SLACK_EMAILS
    sent_any = False
    for email in emails:                 # one private DM each, never #leads
        try:
            slack().chat_postMessage(channel=_uid(email), text=text,
                                     unfurl_links=False)
            sent_any = True
        except Exception as exc:
            log(f"  report DM to {email} failed: {exc}")
    if not sent_any:
        raise RuntimeError("end-of-day report could not be sent to anyone")
    if marker:
        update_cells(ws, marker["_row"], {"Seen At": _iso(now)})
    else:
        append_row(ws, {"Lead Key": REPORT_KEY, "Seen At": _iso(now),
                        "Name": "(end-of-day report marker)", "Closed": "meta"})
    log(f"  end-of-day report sent ({since:%a %H:%M} -> {now:%a %H:%M})")
    return True


def _thread_note(s: dict, text: str):
    if not s.get("Slack TS"):
        return
    try:
        slack().chat_postMessage(channel=s["Slack Channel"], thread_ts=s["Slack TS"],
                                 text=text, unfurl_links=False)
    except Exception as exc:
        print(f"  stl thread note failed: {exc}")


# ---------------------------------------------------------------------------
# Slack button + inbound reply (called from server.py)
# ---------------------------------------------------------------------------
DOUBLE_TAP_SECONDS = 60   # same tap again within this = one attempt, not two


def record_attempt(s: dict, outcome: str, user_id: str, now: datetime,
                   clock: datetime) -> tuple[dict, dict]:
    """(cells to write, row as it will be). Pure — unit-tested."""
    n = attempts_of(s)
    last = last_attempt_at(s)
    double = (last is not None and s.get("Outcome") == outcome
              and (now - last).total_seconds() < DOUBLE_TAP_SECONDS)
    log = s.get("Attempt Log") or ""
    if not double:
        n += 1
        entry = f"{now.strftime('%a %d %b %H:%M')} {outcome} ({user_id})"
        log = f"{log}; {entry}" if log else entry
    cells = {"Outcome": outcome, "Called By": f"<@{user_id}>",
             "Attempts": str(n), "Attempt Log": log, "Last Attempt At": _iso(now)}
    if not s.get("Called At"):
        cells["Called At"] = _iso(now)
        cells["Mins to Call"] = str(max(0, int((now - clock).total_seconds() // 60)))
    return cells, {**s, **cells}


def attempt_note(s: dict, outcome: str, user_id: str, now: datetime,
                 created: datetime) -> str:
    """The line under the card after a tap (pure)."""
    n = attempts_of(s)
    since = fmt_mins(now - created)
    if outcome == "called":
        return (f"✅ *Called* by <@{user_id}> at {now.strftime('%H:%M')} "
                f"({since} after the enquiry, attempt {n}).")
    times = [e.split(" no answer")[0].split(" called")[0][4:]   # "21 Nov 19:50"
             for e in (s.get("Attempt Log") or "").split("; ") if e]
    hist = " · ".join(t[-5:] if t[:6] == now.strftime("%d %b") else t for t in times[-6:])
    text = (f"📵 *No answer* — attempt {n} of {config.STL_MAX_ATTEMPTS} by <@{user_id}> "
            f"at {now.strftime('%H:%M')} ({since} after the enquiry)."
            + (f"\nTried: {hist}" if n > 1 and hist else ""))
    due = next_call_due(s)
    if n >= config.STL_MAX_ATTEMPTS:
        text += "\nThat's the last call. The final text takes over; tap *Called* if they ring back."
    elif due:
        when = (f"after {due.strftime('%H:%M')} today" if due.date() == now.date()
                else due.strftime("%a from %H:%M"))
        text += f"\nNext call due {when}, a different time of day. Tap *Called* when you get through."
    else:
        text += "\nTap *Called* if you get through."
    return text


def handle_button(action_id: str, user_id: str, response_url: str,
                  original_blocks: list, fallback_text: str):
    try:
        _handle_button(action_id, user_id, response_url, original_blocks,
                       fallback_text)
    except Exception:
        # Never leave the clicker with a dead button: say it didn't save.
        warn = {"type": "context", "block_id": "stl_warn", "elements": [{
            "type": "mrkdwn", "text": ":warning: Couldn't save that just now "
                                      "- please tap again in a minute."}]}
        blocks = [b for b in original_blocks if b.get("block_id") != "stl_warn"]
        try:
            requests.post(response_url, json={"replace_original": True,
                          "text": fallback_text, "blocks": blocks + [warn]},
                          timeout=10)
        finally:
            raise


def _handle_button(action_id: str, user_id: str, response_url: str,
                   original_blocks: list, fallback_text: str):
    kind, key = action_id.split("|", 1)
    now = datetime.now(LONDON)
    ws = state_ws()
    s = read_state(ws).get(key)
    if not s:
        return
    clock = _parse(s["Clock Start"]) or now
    created = _parse(s["Lead Created"]) or now
    outcome = "called" if kind == "stl_called" else "no answer"
    cells, s2 = record_attempt(s, outcome, user_id, now, clock)
    update_cells(ws, s["_row"], cells)
    blocks = [b for b in original_blocks
              if not str(b.get("block_id", "")).startswith(
                  ("stl_actions|", "stl_done|", "stl_warn"))]
    blocks.append({"type": "context", "block_id": f"stl_done|{key}", "elements": [
        {"type": "mrkdwn", "text": attempt_note(s2, outcome, user_id, now, created)}]})
    if outcome == "no answer":
        # Both buttons stay: the next try may be another no answer.
        els = actions_block(key)["elements"]
        if attempts_of(s2) >= config.STL_MAX_ATTEMPTS:
            els = els[:1]                # only "Called" (if they ring back)
        blocks.append({"type": "actions", "block_id": f"stl_actions|{key}",
                       "elements": els})
    requests.post(response_url, json={"replace_original": True, "text": fallback_text,
                                      "blocks": blocks}, timeout=10)


_phone_index = {"at": 0.0, "rows": {}}


def find_lead_by_phone(phone: str) -> dict | None:
    """State row for a texted lead with this number (last 30 days), or None."""
    p = norm_phone(phone)
    if not p:
        return None
    if time.monotonic() - _phone_index["at"] > 60:
        rows = {}
        for s in read_state(state_ws()).values():
            if s.get("Text Status") in ("sent",) and s.get("Slack TS"):
                seen = _parse(s["Seen At"])
                if seen and datetime.now(LONDON) - seen <= timedelta(
                        days=config.STL_REPLY_LOOKBACK_DAYS):
                    rows[norm_phone(s["Phone"])] = s
        _phone_index.update(at=time.monotonic(), rows=rows)
    return _phone_index["rows"].get(p)


def post_reply(s: dict, body: str, optout: bool = False):
    who = reception_mention() if desk_open_at(datetime.now(LONDON)) else ""
    if optout:
        text = (f":no_bell: *{s['Name']}* replied STOP — no more texts will reach "
                f"them from this number. A phone call is still fine.")
    else:
        text = f":speech_balloon: *{esc(s['Name'])}* replied: {who}\n>{esc(body)}"
    _thread_note(s, text)
    # Any reply stops the final text; a stop / no-thanks closes the lead for
    # good (the suppression list is simply these Closed values, by phone).
    try:
        cells = {"Replied At": _iso(datetime.now(LONDON))}
        if optout or wants_no_contact(body):
            cells["Closed"] = "opted out" if optout else "replied no"
        update_cells(state_ws(), s["_row"], cells)
    except Exception as exc:
        print(f"  stl could not record reply for {s.get('Name')}: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Background loop (server.py starts this in a thread)
# ---------------------------------------------------------------------------
_LOCK_PATH = "/tmp/speed_to_lead.lock"


@contextmanager
def _only_one_runner():
    """gunicorn runs 2 workers; only one may tick at a time (see team_sheet)."""
    fh = open(_LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Never skip silently: a lock held for good by another process
            # (an inherited fd after a fork) is how the bots stopped 8–14 Nov.
            print(f"speed-to-lead: lock {_LOCK_PATH} held by another process — skipped",
                  flush=True)
            yield False
            return
        yield True
    finally:
        fh.close()


def run_forever(interval: int | None = None):
    interval = interval or config.STL_POLL_SECONDS
    fails = 0
    while True:
        try:
            with _only_one_runner() as mine:
                if mine:
                    stats = tick(log=lambda m: print(m, flush=True))
                    if any(stats.values()):
                        print(f"speed-to-lead: {stats}", flush=True)
            fails = 0
        except Exception as exc:                       # never kill the thread
            fails += 1
            print(f"speed-to-lead tick failed ({fails}): {exc}", flush=True)
            if fails in (5, 60):                       # ~5 min, then ~1 h
                try:
                    slack_notifier._send_dm(
                        config.CEO_SLACK_EMAIL,
                        f":warning: Speed-to-lead has failed {fails} times in a row "
                        f"— new Meta leads are *not* being texted or posted. "
                        f"Last error: `{exc}`",
                        target_label="speed-to-lead failure")
                except Exception as alert_exc:
                    print(f"  (could not raise the alert: {alert_exc})")
        time.sleep(interval)


HANDLED_ERROR = 3   # exit code: error already reported by this module's own alert


def run_once(dry_run: bool = False) -> int:
    """One tick as its own short-lived process (bot_worker.py, since
    2026-11-15). Same alerting as run_forever, with the consecutive-failure
    count kept in bot_state between runs. Returns the process exit code."""
    import bot_state
    try:
        stats = tick(dry_run=dry_run, log=lambda m: print(m, flush=True))
        if any(stats.values()):
            print(f"speed-to-lead: {stats}", flush=True)
        if not dry_run:
            bot_state.record_result("speed_to_lead", True)
        return 0
    except Exception as exc:
        fails = 0 if dry_run else bot_state.record_result("speed_to_lead", False)
        print(f"speed-to-lead tick failed ({fails}): {exc}", flush=True)
        if fails in (5, 60):                           # ~5 min, then ~1 h
            try:
                slack_notifier._send_dm(
                    config.CEO_SLACK_EMAIL,
                    f":warning: Speed-to-lead has failed {fails} times in a row "
                    f"— new Meta leads are *not* being texted or posted. "
                    f"Last error: `{exc}`",
                    target_label="speed-to-lead failure")
            except Exception as alert_exc:
                print(f"  (could not raise the alert: {alert_exc})", flush=True)
        return HANDLED_ERROR


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if "--once" in sys.argv:
        sys.exit(run_once(dry_run="--dry" in sys.argv))
    if "--tick" in sys.argv:
        print(tick())
    else:
        print(tick(dry_run=True))
