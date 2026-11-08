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
import requests
from google.oauth2.service_account import Credentials
from slack_sdk import WebClient

import config
import slack_notifier
import meta_leads
import sheets_retry
from marketing import twilio_client

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
           "Outcome", "Mins to Call", "Closed"]
COL = {h: i for i, h in enumerate(HEADERS)}


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
        return _retry(lambda: sh.worksheet(config.STL_STATE_TAB), "open state tab")
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
    return _retry(lambda: meta_leads.read_source_leads(_gspread()), "read Online Leads")


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
def send_text(phone: str, body: str) -> tuple[bool, str]:
    dest = twilio_client.normalise_uk_phone(phone)
    if not dest:
        return False, "no usable phone number"
    if not config.STL_LIVE:
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
        _slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
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
        head = (f":telephone_receiver: {reception_mention()} "
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

    # 4. End-of-day report to Martin's DM, once per desk day after closing.
    if maybe_send_report(ws, state, now, log):
        stats["report"] = 1

    return stats


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
            return f"no answer ({r['Mins to Call']} min) - not yet reached"
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
    channel = (_uid(config.STL_REPORT_SLACK_EMAIL))
    if not config.STL_LIVE:
        text = "_[SAFE MODE]_\n" + text
    slack().chat_postMessage(channel=channel, text=text, unfurl_links=False)
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
    cells = {"Outcome": outcome, "Called By": f"<@{user_id}>"}
    if not s.get("Called At"):
        cells["Called At"] = _iso(now)
        cells["Mins to Call"] = str(max(0, int((now - clock).total_seconds() // 60)))
    update_cells(ws, s["_row"], cells)
    since = fmt_mins(now - created)
    label = ("✅ *Called*" if outcome == "called" else "📵 *No answer*")
    note = {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"{label} by <@{user_id}> at {now.strftime('%H:%M')} "
            f"({since} after the enquiry)"
            + (" — tap *Called* when you get through." if outcome == "no answer" else "")}]}
    blocks = [b for b in original_blocks
              if not str(b.get("block_id", "")).startswith(
                  ("stl_actions|", "stl_done|", "stl_warn"))]
    note["block_id"] = f"stl_done|{key}"
    blocks.append(note)
    if outcome == "no answer":
        blocks.append({"type": "actions", "block_id": f"stl_actions|{key}", "elements": [
            actions_block(key)["elements"][0]]})
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
                if seen and datetime.now(LONDON) - seen <= timedelta(days=30):
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


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if "--tick" in sys.argv:
        print(tick())
    else:
        print(tick(dry_run=True))
