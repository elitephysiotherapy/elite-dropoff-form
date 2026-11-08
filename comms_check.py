"""Daily patient-comms overlap check (Martin, 8 Nov 2026).

Cliniko and our Python bots both message patients and neither can see the
other's sends. Once a day (07:15 London) this reads both and flags only what
is actually WRONG — never the agreed sequences:

  R1  opt-out breach  — a non-appointment message to a phone after it replied
                        STOP (Twilio or Cliniko), with no START since.
  R2  exact repeat    — the same message to the same person twice within 7
                        days (the 9 Oct 2026 bug: one patient got 43).
  R3  system clash    — Cliniko AND our bots both sent a non-appointment
                        message to one patient within 48h.

Deliberately NOT flagged (Martin's decision): message volume. SMS+email pairs,
NPS + rebook after a DNA, etc. are the intended process.

Appointment admin (confirmations, reminders, invoices, forms...) never counts.
Flagged items go to the "Comms Check" tab on the marketing sheet (one row per
issue, never repeated) and a private Slack DM to Martin. Quiet day = no DM.

Runs as a thread in server.py. CLI:
  python comms_check.py --dry-run   counts only, no writes, no names printed
  python comms_check.py --post      run once for real now
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials
from slack_sdk import WebClient

import config
import sheets_retry

LONDON = ZoneInfo("Europe/London")
TAB = "Comms Check"
HEADERS = ["Flagged At", "Rule", "Patient", "Patient ID", "Phone (last 4)",
           "What happened", "Messages", "Action Taken", "Key"]
MARKER = "__last_run__"
RUN_AT = (7, 15)
LOOKBACK_DAYS = 30        # history read each run (repeats need 7d; phone->patient map needs more)
REPORT_HOURS = 72         # only issues whose latest message is this recent
REPEAT_DAYS = 7
CLASH_HOURS = 48

APPT = {"Appointment Confirmation", "Appointment Reminder",
        "Rescheduled Appointment Notification", "Appointment Cancellation",
        "Cancelled appointment notification", "Invoice", "Account Statement",
        "Payment Receipt", "Patient Form", "Letter", "Treatment note",
        "Upcoming appointments", "Online payment request"}
STAFF = {"Practitioner Appointment Notification", "Patient Reply Forwarded",
         "Completed Patient Form"}
STOP_RE = re.compile(r"^\s*(stop|stopall|unsubscribe|end|quit|opt.?out|remove me)\b", re.I)
START_RE = re.compile(r"^\s*(start|unstop|yes)\s*$", re.I)
STL_MARK = "thanks for getting in touch with Elite Physio"   # speed-to-lead text
RULE_NAMES = {"R1": "Opt-out breach", "R2": "Exact repeat", "R3": "Cliniko + bots clash"}


# --------------------------------------------------------------------------- pure helpers
def norm_phone(s) -> str:
    d = re.sub(r"\D", "", str(s or ""))
    return d[-10:] if len(d) >= 10 else ""


def body_key(s) -> str:
    """Hash of a message body with links removed (short links differ per send)."""
    t = re.sub(r"https?://\S+", "", str(s or "")).strip().lower()
    return hashlib.sha1(t.encode()).hexdigest()[:12]


def last4(phone: str) -> str:
    return phone[-4:] if phone else ""


def find_issues(cliniko, python_sms, python_email, stop_events, now):
    """Pure rule engine.

    cliniko:      dicts t, cat, type, dir ("Sent"/"Received"), contact, pid, ckey
    python_sms:   dicts t, phone, bkey, stl (bool)
    python_email: dicts t, pid, flow, anchor, name
    stop_events:  dicts t, phone, kind ("stop"/"start")
    Returns issue dicts: key, rule, pid, phone, what, msgs, t.
    """
    since = now - timedelta(hours=REPORT_HOURS)
    issues = []

    phone_pid = {}
    for m in cliniko:
        if m["type"] == "SMS" and m["pid"] and m["contact"]:
            phone_pid.setdefault(m["contact"], m["pid"])

    def person(pid, phone):
        pid = pid or phone_pid.get(phone, "")
        return (f"p:{pid}" if pid else f"ph:{phone}"), pid

    # outbound non-appointment events, both systems
    events = []      # (t, system, label, person, pid, phone, channel)
    for m in cliniko:
        if m["dir"] != "Sent" or m["cat"] in APPT or m["cat"] in STAFF:
            continue
        ph = m["contact"] if m["type"] == "SMS" else ""
        who, pid = person(m["pid"], ph) if (m["pid"] or ph) else (f"c:{m['contact']}", "")
        events.append((m["t"], "Cliniko", f"Cliniko {m['cat']} ({m['type']})", who, pid, ph, m["type"]))
    for m in python_sms:
        who, pid = person("", m["phone"])
        events.append((m["t"], "Bots", "speed-to-lead text" if m["stl"] else "bot text",
                       who, pid, m["phone"], "SMS"))
    for m in python_email:
        who, pid = person(m["pid"], "")
        events.append((m["t"], "Bots", f"bot email ({m['flow']})", who, pid, "", "Email"))

    # R1 opt-out breach -------------------------------------------------------
    by_phone = defaultdict(list)
    for e in stop_events:
        by_phone[e["phone"]].append(e)
    for t, system, label, who, pid, ph, ch in events:
        if ch != "SMS" or not ph or t < since or ph not in by_phone:
            continue
        prior = [e for e in by_phone[ph] if e["t"] < t]
        if prior and max(prior, key=lambda e: e["t"])["kind"] == "stop":
            stop_t = max(e["t"] for e in prior if e["kind"] == "stop")
            issues.append(dict(key=f"R1|{ph}|{t.isoformat()}", rule="R1", pid=pid, phone=ph, t=t,
                               what=f"{label} sent after they replied STOP",
                               msgs=f"STOP {fmt(stop_t)} → {label} {fmt(t)}"))

    # R2 exact repeats --------------------------------------------------------
    def repeats(groups, label_fn):
        for k, ts in groups.items():
            ts = sorted(ts)
            for a, b in zip(ts, ts[1:]):
                if b - a <= timedelta(days=REPEAT_DAYS) and b >= since:
                    yield k, a, b, len(ts)
                    break
    g = defaultdict(list)
    for m in python_sms:
        g[(m["phone"], m["bkey"])].append(m["t"])
    for (ph, bk), a, b, n in repeats(g, None):
        who, pid = person("", ph)
        issues.append(dict(key=f"R2|sms|{ph}|{bk}|{b.date()}", rule="R2", pid=pid, phone=ph, t=b,
                           what=f"same bot text sent {n}× within {REPEAT_DAYS} days",
                           msgs=f"{fmt(a)} and {fmt(b)}"))
    g = defaultdict(list)
    for m in python_email:
        g[(m["pid"], m["flow"], m["anchor"])].append(m["t"])
    for (pid, flow, anc), a, b, n in repeats(g, None):
        issues.append(dict(key=f"R2|email|{pid}|{flow}|{anc}|{b.date()}", rule="R2", pid=pid,
                           phone="", t=b, what=f"bot email '{flow}' sent {n}× for the same visit",
                           msgs=f"{fmt(a)} and {fmt(b)}"))
    g = defaultdict(list)
    for m in cliniko:
        if m["dir"] == "Sent" and m["cat"] not in APPT | STAFF and m["contact"]:
            g[(m["contact"], m["ckey"], m["pid"], m["cat"])].append(m["t"])
    for (contact, ck, pid, cat), a, b, n in repeats(g, None):
        ph = contact if contact.isdigit() else ""
        issues.append(dict(key=f"R2|cliniko|{contact}|{ck}|{b.date()}", rule="R2", pid=pid,
                           phone=ph, t=b, what=f"same Cliniko '{cat}' sent {n}× within {REPEAT_DAYS} days",
                           msgs=f"{fmt(a)} and {fmt(b)}"))

    # R3 Cliniko + bots within 48h --------------------------------------------
    per = defaultdict(list)
    for e in events:
        if e[2] != "speed-to-lead text":
            per[e[3]].append(e)
    for who, evs in per.items():
        cl = [e for e in evs if e[1] == "Cliniko"]
        py = [e for e in evs if e[1] == "Bots"]
        best = None
        for a in cl:
            for b in py:
                gap = abs((a[0] - b[0]).total_seconds())
                if gap <= CLASH_HOURS * 3600 and max(a[0], b[0]) >= since:
                    if best is None or gap < best[2]:
                        best = (a, b, gap)
        if best:
            a, b, _ = best
            latest = max(a[0], b[0])
            issues.append(dict(key=f"R3|{who}|{latest.date()}", rule="R3", pid=a[4] or b[4],
                               phone=a[5] or b[5], t=latest,
                               what=f"{a[2]} and {b[2]} within {CLASH_HOURS}h",
                               msgs=f"{a[2]} {fmt(a[0])}; {b[2]} {fmt(b[0])}"))
    return issues


def fmt(t: datetime) -> str:
    return t.astimezone(LONDON).strftime("%a %d %b %H:%M")


def due(now_london: datetime, last_run_date: str) -> bool:
    return (now_london.hour, now_london.minute) >= RUN_AT and \
        last_run_date != now_london.date().isoformat()


# --------------------------------------------------------------------------- fetchers
_cl = None


def cliniko_session():
    global _cl
    if _cl is None:
        s = requests.Session()
        s.auth = (os.environ["CLINIKO_API_KEY"], "")
        s.headers.update({"User-Agent": os.environ.get("CLINIKO_USER_AGENT", "Elite Physio comms check"),
                          "Accept": "application/json"})
        _cl = s
    return _cl


def _cliniko_base():
    return f"https://api.{os.environ.get('CLINIKO_SHARD', 'uk1')}.cliniko.com/v1"


def fetch_cliniko(since: datetime):
    out, stops = [], []
    url = f"{_cliniko_base()}/communications"
    params = {"per_page": 100, "q[]": f"created_at:>{since.strftime('%Y-%m-%dT%H:%M:%SZ')}"}
    while url:
        r = cliniko_session().get(url, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        params = None
        j = r.json()
        for m in j.get("communications", []):
            link = ((m.get("patient") or {}).get("links") or {}).get("self", "")
            pid = link.rsplit("/", 1)[-1] if link else ""
            d = m.get("direction_description") or ""
            raw = m.get("to") if d == "Sent" else m.get("from")
            contact = norm_phone(raw) if m.get("type") == "SMS" else str(raw or "").strip().lower()
            t = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
            out.append(dict(t=t, cat=m.get("category"), type=m.get("type"), dir=d,
                            contact=contact, pid=pid, ckey=body_key(m.get("content"))))
            if d == "Received" and m.get("type") == "SMS" and contact:
                body = m.get("content") or ""
                if STOP_RE.match(body):
                    stops.append(dict(t=t, phone=contact, kind="stop"))
                elif START_RE.match(body):
                    stops.append(dict(t=t, phone=contact, kind="start"))
        url = (j.get("links") or {}).get("next")
    return out, stops


def fetch_twilio(out_since: datetime, in_since: datetime):
    sid, tok = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    params = {"PageSize": 1000, "DateSent>": in_since.strftime("%Y-%m-%d")}
    test = norm_phone(config.MARKETING_TEST_PHONE)
    sms, stops = [], []
    while url:
        r = requests.get(url, params=params, auth=(sid, tok), timeout=60)
        r.raise_for_status()
        params = None
        j = r.json()
        for m in j.get("messages", []):
            raw_t = m.get("date_sent") or m.get("date_created")
            if not raw_t:
                continue
            t = datetime.strptime(raw_t, "%a, %d %b %Y %H:%M:%S %z")
            body = m.get("body") or ""
            if m.get("direction") == "inbound":
                ph = norm_phone(m.get("from"))
                if STOP_RE.match(body):
                    stops.append(dict(t=t, phone=ph, kind="stop"))
                elif START_RE.match(body):
                    stops.append(dict(t=t, phone=ph, kind="start"))
            elif t >= out_since and m.get("status") not in ("failed", "undelivered", "canceled"):
                ph = norm_phone(m.get("to"))
                if ph and ph != test and not body.startswith("[TEST"):
                    sms.append(dict(t=t, phone=ph, bkey=body_key(body), stl=STL_MARK in body))
        nxt = j.get("next_page_uri")
        url = f"https://api.twilio.com{nxt}" if nxt else None
    return sms, stops


_gc = None


def gspread_client():
    global _gc
    if _gc is None:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        raw = os.environ.get("SERVICE_ACCOUNT_JSON")
        if raw:
            creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
        else:
            creds = Credentials.from_service_account_file(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "service_account.json"),
                scopes=scopes)
        _gc = gspread.authorize(creds)
    return _gc


def _retry(call, label):
    return sheets_retry.gs_retry(call, label, prefix="  comms ")


def marketing_sheet():
    return _retry(lambda: gspread_client().open_by_key(config.MARKETING_SPREADSHEET_ID),
                  "open marketing sheet")


def read_sent_log_emails(sh, since: datetime):
    rows = _retry(lambda: sh.worksheet("Marketing - Sent Log").get_all_values(), "read Sent Log")
    if not rows:
        return [], {}
    i = {n: k for k, n in enumerate(rows[0])}
    out, names = [], {}
    for r in rows[1:]:
        try:
            pid, name, flow, ch = r[i["Patient ID"]], r[i["Patient Name"]], r[i["Flow Name"]], r[i["Channel"]]
            status = r[i["Status"]] if len(r) > i["Status"] else ""
            t = datetime.strptime(r[i["Timestamp"]][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=LONDON)
        except (KeyError, ValueError, IndexError):
            continue
        if pid and name:
            names[pid] = name
        if t < since or "mail" not in ch.lower() or "internal" in ch.lower():
            continue
        if status and not status.lower().startswith("sent"):
            continue
        out.append(dict(t=t, pid=pid, flow=flow, anchor=r[i["Episode Anchor Date"]], name=name))
    return out, names


def check_ws(sh):
    try:
        ws = _retry(lambda: sh.worksheet(TAB), "open Comms Check tab")
    except gspread.WorksheetNotFound:
        ws = _retry(lambda: sh.add_worksheet(TAB, rows=500, cols=len(HEADERS)), "create Comms Check tab")
        _retry(lambda: ws.update([HEADERS, [MARKER, "", "", "", "", "last run", "", "", MARKER]],
                                 "A1", value_input_option="RAW"), "write headers")
        _retry(lambda: ws.freeze(rows=1), "freeze header")
    return ws


def patient_name(pid: str, names: dict) -> str:
    if not pid:
        return ""
    if pid in names:
        return names[pid]
    try:
        r = cliniko_session().get(f"{_cliniko_base()}/patients/{pid}", timeout=30)
        if r.ok:
            j = r.json()
            names[pid] = f"{j.get('first_name', '')} {j.get('last_name', '')}".strip()
            return names[pid]
    except requests.RequestException:
        pass
    return ""


# --------------------------------------------------------------------------- run
def run_once(post: bool, log=print, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)
    cliniko, cl_stops = fetch_cliniko(since)
    sms, tw_stops = fetch_twilio(since, now - timedelta(days=365))
    sh = marketing_sheet()
    emails, names = read_sent_log_emails(sh, since)
    issues = find_issues(cliniko, sms, emails, cl_stops + tw_stops, now)
    by_rule = ", ".join("%s:%d" % (r, sum(x["rule"] == r for x in issues)) for r in RULE_NAMES)
    log(f"comms check: cliniko={len(cliniko)} bot_sms={len(sms)} bot_email={len(emails)} "
        f"stops={len(cl_stops) + len(tw_stops)} issues={len(issues)} ({by_rule})")
    if not post:
        return issues

    ws = check_ws(sh)
    rows = _retry(lambda: ws.get_all_values(), "read Comms Check tab")
    seen = {r[8] for r in rows[1:] if len(r) > 8}
    new = [x for x in issues if x["key"] not in seen]
    new.sort(key=lambda x: (x["rule"], x["t"]))
    stamp = now.astimezone(LONDON).strftime("%Y-%m-%d %H:%M")
    if new:
        out = [[stamp, RULE_NAMES[x["rule"]], patient_name(x["pid"], names), "'" + x["pid"] if x["pid"] else "",
                last4(x["phone"]), x["what"], x["msgs"], "", x["key"]] for x in new]
        _retry(lambda: ws.append_rows(out, value_input_option="USER_ENTERED"), "append issues")
        dm(new, names, sh.url + f"#gid={ws.id}")
    _set_marker(ws, rows, now.astimezone(LONDON).date().isoformat())
    log(f"comms check: {len(new)} new issue(s) reported")
    return new


def _set_marker(ws, rows, date_str):
    for n, r in enumerate(rows, start=1):
        if r and r[0] == MARKER:
            _retry(lambda: ws.update_cell(n, 2, date_str), "update marker")
            return
    _retry(lambda: ws.append_row([MARKER, date_str, "", "", "", "last run", "", "", MARKER],
                                 value_input_option="RAW"), "add marker")


def _last_run(ws) -> str:
    for r in _retry(lambda: ws.get_all_values(), "read marker"):
        if r and r[0] == MARKER:
            return r[1] if len(r) > 1 else ""
    return ""


_slack = None


def slack():
    global _slack
    if _slack is None:
        _slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    return _slack


def _martin():
    return slack().users_lookupByEmail(email=config.CEO_SLACK_EMAIL)["user"]["id"]


def dm(new, names, link):
    lines = [f":envelope: *Comms check — {len(new)} issue{'s' if len(new) != 1 else ''}*"]
    for x in new[:15]:
        who = patient_name(x["pid"], names) or (f"…{last4(x['phone'])}" if x["phone"] else "unknown")
        icon = ":rotating_light:" if x["rule"] == "R1" else "•"
        lines.append(f"{icon} *{RULE_NAMES[x['rule']]}* — {who}: {x['what']} ({x['msgs']})")
    if len(new) > 15:
        lines.append(f"…and {len(new) - 15} more")
    lines.append(f"<{link}|Open the Comms Check tab>")
    slack().chat_postMessage(channel=_martin(), text="\n".join(lines), unfurl_links=False)


_LOCK_PATH = "/tmp/comms_check.lock"


@contextmanager
def _only_one_runner():
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


def run_forever(interval: int = 300):
    """server.py thread: wake every 5 min; run once per day from 07:15 London."""
    fails, alerted = 0, ""
    while True:
        try:
            with _only_one_runner() as mine:
                now_l = datetime.now(LONDON)
                if mine and (now_l.hour, now_l.minute) >= RUN_AT:
                    ws = check_ws(marketing_sheet())
                    if due(now_l, _last_run(ws)):
                        run_once(post=True, log=lambda m: print(m, flush=True))
            fails = 0
        except Exception as exc:                      # never kill the thread
            fails += 1
            print(f"comms check error ({fails}): {exc}", flush=True)
            today = datetime.now(LONDON).date().isoformat()
            if fails >= 3 and alerted != today:
                try:
                    slack().chat_postMessage(channel=_martin(), text=(
                        f":warning: The daily comms check has failed {fails} times today "
                        f"and hasn't run. Last error: {str(exc)[:300]}"))
                    alerted = today
                except Exception:
                    pass
        time.sleep(interval)


if __name__ == "__main__":
    if "--post" in sys.argv:
        run_once(post=True)
    else:
        found = run_once(post=False)
        for r in RULE_NAMES:
            print(f"  {RULE_NAMES[r]}: {sum(x['rule'] == r for x in found)}")
