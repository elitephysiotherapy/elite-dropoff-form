"""Pre-Christmas campaign (16 Nov – 23 Dec 2026): MOT, club players, cold
leads, IA rebooks — built to keep the admin team's work near zero.

Four jobs, all run from one server.py thread (always-on Render service):

  1. TEXTS + EMAILS   consented MOT/club patients get the offer with the clinic
                      booking link (second MOT text after 6 days if not booked).
  2. CALL LISTS       Mon–Fri 08:00 each physio gets a Slack DM of the people
                      THEY should ring today (their own lapsed patients, assigned
                      cold leads / rebooks, club players who replied), with
                      Booked / No answer / Call back / Not interested buttons.
                      A text reply from a campaign patient goes to their physio
                      as a card with the same buttons.
  3. OUTCOMES         every morning Cliniko is checked: anyone who booked or
                      attended is marked and drops off the call lists. Monday
                      scorecard DM to Martin, Julie and Sinéad Rocks.
  4. VOUCHER          after an attended "Pre-Christmas MOT" appointment, a text
                      with the free Pilates class code. (Meta lead routing to
                      physios lives in speed_to_lead.py, using pick_physio().)

Safety:
  CAMPAIGN_MODE off / test / live (config). In test mode every patient text and
  email goes to Martin's phone/email and every DM goes to Martin, capped at
  CAMPAIGN_TEST_CAP per job. Marketing consent is re-read from Cliniko right
  before every send. Every send is written to the marketing Sent Log (so the
  daily comms check sees it) and to this tab, so nothing is sent twice.

State: the "Campaign" tab on the marketing sheet, one row per person.
Console output is IDs and counts only — never names, numbers or message text.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials
from slack_sdk import WebClient

import config
import phase2
import sheets_retry
from marketing import resend_client, sent_log, twilio_client

LONDON = ZoneInfo("Europe/London")
SCRIPTS_DOC = "https://docs.google.com/document/d/1ZBJTb6kfZ7LhvoObFFVPLYSW4WSxtdjR7cMSwyvXX-o/edit"
CLINIKO_WEB = "https://elite-physiotherapy.uk1.cliniko.com"

HEADERS = ["Key", "Group", "Wave", "Patient ID", "Name", "Phone", "Email",
           "Text OK", "Email OK", "Physio", "Practitioner ID", "Clinic", "NPS",
           "Added", "Text 1", "Text 2", "Email 1", "Replied", "Call Due",
           "Attempts", "Last Call", "Last Outcome", "Called By", "Booked",
           "Attended", "MOT Attended", "Voucher Sent", "Excluded", "Notes",
           "Text 3", "Email 2"]
COL = {h: i for i, h in enumerate(HEADERS)}

GROUP_LABEL = {"mot": "Black Friday MOT", "club": "Club player (GAA profiling)",
               "cold": "Cold lead", "rebook": "IA rebook"}
CHOICES = {"booked": "✅ Booked", "noanswer": "📵 No answer",
           "callback": "🔁 Call back", "notinterested": "🚫 Not interested"}

# ---------------------------------------------------------------------------
# Message wording (Martin to approve before CAMPAIGN_MODE=live)
# ---------------------------------------------------------------------------
GUARANTEE = ("Our promise: you'll leave knowing exactly where you stand and your "
             "next three steps, or the MOT is on us.")
# Black Friday MOT (Martin 2026-11-14). Text 1 from Mon 23 Nov, Text 2 on Black
# Friday, deadline Cyber Monday 30 Nov, Text 3 "last slots before Christmas".
# Wording revised 2026-11-14 after Dan Martell's review (tighter SMS 1, shorter
# SMS 2, contrarian email subject, reply hook in emails).
# SMS 1 = Martin's own wording (14 Nov), NOT Dan's tightened version.
SMS_MOT_1 = ("Hi {first}, it's Elite Physio. We just wanted to check in with you and "
             "offer you our Black Friday MOT: a full re-check vs where you started, a "
             "1-page report + your 3 key exercises, £60. Book by Mon 30 Nov for a free "
             "Pilates class + a 2-for-1 to share. You'll leave knowing exactly where you "
             "stand, or it's on us. Book: {link} or reply with a day that suits. "
             "Reply STOP to opt out")
SMS_MOT_2 = ("Hi {first}, last few days for your Elite Physio MOT - £60 re-check, free "
             "Pilates class + a 2-for-1. Ends Mon. Book: {link} Reply STOP to opt out")
SMS_MOT_3 = ("Hi {first}, last MOT slots before we close for Christmas on 23 Dec: "
             "full re-check, 1-page report + your 3 key exercises, £60, with a free "
             "Pilates class. Book: {link} Reply STOP to opt out")
SMS_CLUB_1 = ("Hi {first}, it's Elite Physio. Now the season's over it's the best "
              "time to sort anything still niggling. Our 80-min winter GAA profiling "
              "(strength, speed + movement testing, with a plan for pre-season) is "
              "£99. Book: {link} or reply YES and we'll ring you. Reply STOP to "
              "opt out")
SMS_VOUCHER = ("Thanks for coming in for your MOT, {first}! Here's your free Pilates "
               "matwork class (Cookstown or Maghera): use code {code} at "
               "{pilates_link}. Valid until {expiry}. Elite Physio")
SMS_VOUCHER_BF = ("Thanks for coming in for your Black Friday MOT, {first}! Your free "
                  "Pilates matwork class: code {code}. Plus a 2-for-1 to share with a "
                  "friend: code {code2}. Book at {pilates_link} (Cookstown or Maghera, "
                  "valid until {expiry}). Elite Physio")
EMAIL_MOT_SUBJECT = "We're not doing discounts, {first}"
EMAIL_MOT_BODY = """Hi {first},

We wanted to check in on how you've been getting on since you were last with us.

This Black Friday we're not cutting prices, we're adding value. Our Black Friday MOT is for people we've seen this year:

- A full re-check of your strength and movement, compared with where you started
- A one-page report and your three key exercises (with videos)
- A free Pilates matwork class at Cookstown or Maghera

It's £60. Book by Monday 30 November and you'll also get a 2-for-1 Pilates pass to share with a friend.

""" + GUARANTEE + """

Book online here: {link}
Or just reply with your preferred day and time and we'll sort it.

Elite Physiotherapy

You're receiving this because you've been a patient with us. Don't want these emails? Unsubscribe here: {unsub}"""


EMAIL_MOT2_SUBJECT = "Last day for your Black Friday MOT, {first}"
EMAIL_MOT2_BODY = """Hi {first},

A quick reminder: today is the last day to book your Black Friday MOT.

- A full re-check of your strength and movement, compared with where you started
- A one-page report and your three key exercises (with videos)
- A free Pilates matwork class at Cookstown or Maghera, plus a 2-for-1 Pilates pass to share with a friend when you book today

It's £60. """ + GUARANTEE + """

Book online here: {link}
Or just reply with your preferred day and time and we'll sort it.

Elite Physiotherapy

You're receiving this because you've been a patient with us. Don't want these emails? Unsubscribe here: {unsub}"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def mode() -> str:
    m = config.CAMPAIGN_MODE
    return m if m in ("off", "test", "live") else "off"


def today_l(now: datetime | None = None) -> date:
    return (now or datetime.now(LONDON)).astimezone(LONDON).date()


def in_window(d: date) -> bool:
    return date.fromisoformat(config.CAMPAIGN_START) <= d <= date.fromisoformat(config.CAMPAIGN_END)


def norm_phone(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else ""


def _d(s: str) -> date | None:
    """Date in a cell — also reads 'sending …' / 'sent (from log) …' stamps.
    A 'failed …' cell has no usable date (nothing was confirmed sent)."""
    s = s or ""
    if s.startswith("failed"):
        return None
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    try:
        return date.fromisoformat(m.group(0)) if m else None
    except ValueError:
        return None


def first_name(name: str) -> str:
    n = (name or "").strip().split(" ")[0]
    return re.sub(r"[^A-Za-z'\-]", "", n).capitalize() or "there"


def is_closed(r: dict) -> bool:
    """True if this person needs nothing more from the campaign."""
    return bool(r.get("Booked") or r.get("Attended") or r.get("Excluded"))


# ---------------------------------------------------------------------------
# Pure rules (tested in test_campaign.py)
# ---------------------------------------------------------------------------
def sms_allowed(r: dict) -> bool:
    """'Yes' = ticked marketing consent. 'Soft' = past patient who has not
    opted out (texted only while CAMPAIGN_SMS_SOFT_OPT_IN is on, MOT only).
    'Opted out' / 'Under 18' / 'No' are never texted."""
    if r["Text OK"] == "Yes":
        return True
    return (r["Text OK"] == "Soft" and r["Group"] == "mot"
            and getattr(config, "CAMPAIGN_SMS_SOFT_OPT_IN", False))


def _cfg_date(name: str) -> date:
    return date.fromisoformat(getattr(config, name))


def group_active(r: dict) -> bool:
    return r["Group"] in getattr(config, "CAMPAIGN_ACTIVE_GROUPS", ["mot", "club", "cold", "rebook"])


def text1_due(r: dict, d: date) -> bool:
    if not group_active(r):
        return False
    if r["Group"] == "mot" and d < _cfg_date("CAMPAIGN_TEXT1_FROM"):
        return False
    # No first texts weeks after launch (Omagh lesson): late joiners are skipped.
    if r["Group"] == "mot" and d > date.fromisoformat(getattr(config, "CAMPAIGN_TEXT1_UNTIL", "2026-11-20")):
        return False
    return (in_window(d) and r["Group"] in ("mot", "club") and sms_allowed(r)
            and not r["Text 1"] and not is_closed(r) and bool(r["Phone"]))


def email_allowed(r: dict) -> bool:
    """'Yes' = ticked email marketing. 'Soft' = past MOT patient with an email
    who hasn't unsubscribed (Martin 2026-11-14: email everyone except
    opt-outs; every email carries a one-click unsubscribe link)."""
    if r["Email OK"] == "Yes":
        return True
    return (r["Email OK"] == "Soft" and r["Group"] == "mot"
            and getattr(config, "CAMPAIGN_EMAIL_SOFT_OPT_IN", False))


def email1_due(r: dict, d: date) -> bool:
    return (in_window(d) and r["Group"] == "mot" and email_allowed(r)
            and d >= _cfg_date(getattr(config, "CAMPAIGN_EMAIL_ON", None) and "CAMPAIGN_EMAIL_ON" or "CAMPAIGN_TEXT1_FROM")
            and d <= date.fromisoformat(getattr(config, "CAMPAIGN_EMAIL1_UNTIL", "2026-11-21"))
            and not r["Email 1"] and not is_closed(r) and bool(r["Email"]))


def text2_due(r: dict, d: date) -> bool:
    """MOT: the Black Friday reminder, Black Friday to Cyber Monday only."""
    t1 = _d(r["Text 1"])
    return (in_window(d) and r["Group"] == "mot" and t1 is not None and not r["Text 2"]
            and not r["Replied"] and not is_closed(r)
            and _cfg_date("CAMPAIGN_TEXT2_ON") <= d <= _cfg_date("CAMPAIGN_BF_DEADLINE")
            and t1 < d)


def text3_due(r: dict, d: date) -> bool:
    """MOT: 'last slots before Christmas', once, from CAMPAIGN_TEXT3_ON
    (blank setting = no December text)."""
    if not getattr(config, "CAMPAIGN_TEXT3_ON", ""):
        return False
    t1 = _d(r["Text 1"])
    return (in_window(d) and r["Group"] == "mot" and t1 is not None and not r["Text 3"]
            and not r["Replied"] and not is_closed(r) and sms_allowed(r)
            and d >= _cfg_date("CAMPAIGN_TEXT3_ON") and t1 < d)


def email2_due(r: dict, d: date) -> bool:
    """MOT: Cyber Monday 'last day' email to email-allowed people who got
    email 1 and haven't booked. Only on CAMPAIGN_EMAIL2_ON itself."""
    on = getattr(config, "CAMPAIGN_EMAIL2_ON", "")
    return (bool(on) and in_window(d) and r["Group"] == "mot" and email_allowed(r)
            and bool(r["Email 1"]) and not r["Email 2"] and not is_closed(r)
            and d == date.fromisoformat(on))


def booked_by_deadline(r: dict) -> bool:
    b = _d(r["Booked"])
    return b is not None and b <= _cfg_date("CAMPAIGN_BF_DEADLINE")


def call_due(r: dict, d: date) -> bool:
    if not in_window(d) or is_closed(r):
        return False
    if int(r["Attempts"] or 0) >= config.CAMPAIGN_MAX_ATTEMPTS:
        return False
    due = _d(r["Call Due"])
    if due and d < due:
        return False
    g = r["Group"]
    if g in ("cold", "rebook"):
        return True
    if r["Replied"]:
        return True
    if g == "club":
        return False                       # club players are only rung if they reply
    # MOT: textable people get the texts first, then a call.
    if sms_allowed(r) and r["Phone"]:
        t2 = _d(r["Text 2"])
        return t2 is not None and d >= t2 + timedelta(days=config.CAMPAIGN_CALL_AFTER_TEXT2_DAYS)
    return True


def call_priority(r: dict) -> tuple:
    g = r["Group"]
    order = {"rebook": 0, "cold": 1, "mot": 2, "club": 3}[g]
    return (0 if r["Replied"] else 1, order,
            0 if r["NPS"] == "Promoter" else 1, r["Wave"] or "9", r["Key"])


def apply_choice(r: dict, choice: str, user: str, d: date) -> dict:
    """Cells to write when a physio taps a button."""
    cells = {"Last Call": d.isoformat(), "Last Outcome": choice, "Called By": user,
             "Attempts": str(int(r.get("Attempts") or 0) + 1)}
    if choice == "booked":
        cells["Booked"] = f"{d.isoformat()} (call)"
    elif choice == "notinterested":
        cells["Excluded"] = "not interested"
    else:   # noanswer / callback
        cells["Call Due"] = (d + timedelta(days=config.CAMPAIGN_RETRY_AFTER_DAYS)).isoformat()
    return cells


def scorecard_numbers(rows: list[dict]) -> dict:
    out = {}
    for g in ("mot", "club", "cold", "rebook"):
        rs = [r for r in rows if r["Group"] == g]
        booked = sum(1 for r in rs if r["Booked"] or r["Attended"])
        t = config.CAMPAIGN_TARGETS[g]
        out[g] = {
            "listed": len(rs),
            "texted": sum(1 for r in rs if r["Text 1"]),
            "emailed": sum(1 for r in rs if r["Email 1"]),
            "called": sum(1 for r in rs if int(r["Attempts"] or 0) > 0),
            "replied": sum(1 for r in rs if r["Replied"]),
            "booked": booked,
            "attended": sum(1 for r in rs if r["Attended"]),
            "not_interested": sum(1 for r in rs if r["Excluded"] == "not interested"),
            "target_people": round(len(rs) * t["rate"]),
            "est_value": booked * t["value"],
            "target_value": round(len(rs) * t["rate"]) * t["value"],
        }
    return out


# ---------------------------------------------------------------------------
# Sheet state
# ---------------------------------------------------------------------------
_gc = None
_ws = None


def _gspread():
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
    return sheets_retry.gs_retry(call, label, prefix="  ")


def state_ws():
    global _ws
    if _ws is None:
        sh = _retry(lambda: _gspread().open_by_key(config.MARKETING_SPREADSHEET_ID), "open marketing sheet")
        try:
            _ws = _retry(lambda: sh.worksheet(config.CAMPAIGN_TAB), "open campaign tab")
            head = _retry(lambda: _ws.row_values(1), "read campaign headers")
            if len(head) < len(HEADERS) and HEADERS[:len(head)] == head:
                if _ws.col_count < len(HEADERS):
                    _retry(lambda: _ws.add_cols(len(HEADERS) - _ws.col_count), "widen campaign tab")
                _retry(lambda: _ws.update(values=[HEADERS[len(head):]],
                                          range_name=f"{_col_letter(len(head))}1"),
                       "add campaign headers")
        except Exception as exc:
            if type(exc).__name__ != "WorksheetNotFound":
                raise
            _ws = _retry(lambda: sh.add_worksheet(title=config.CAMPAIGN_TAB, rows=2000,
                                                  cols=len(HEADERS)), "create campaign tab")
            _retry(lambda: _ws.update(values=[HEADERS], range_name="A1"), "write headers")
            _retry(lambda: _ws.freeze(rows=1), "freeze header")
    return _ws


def read_rows(ws=None) -> list[dict]:
    ws = ws or state_ws()
    vals = _retry(lambda: ws.get_all_values(), "read campaign tab")
    if not vals or vals[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError(f"'{config.CAMPAIGN_TAB}' headers changed — expected {HEADERS}")
    out = []
    for n, row in enumerate(vals[1:], start=2):
        row = row + [""] * (len(HEADERS) - len(row))
        if row[0]:
            d = {h: row[i] for h, i in COL.items()}
            d["_row"] = n
            out.append(d)
    return out


def _col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def update_cells(row: int, values: dict, ws=None):
    ws = ws or state_ws()
    data = [{"range": f"{_col_letter(COL[h])}{row}", "values": [[str(v)]]}
            for h, v in values.items()]
    _retry(lambda: ws.batch_update(data, value_input_option="RAW"), f"update campaign row {row}")


def append_rows(rows: list[dict], ws=None):
    if not rows:
        return
    ws = ws or state_ws()
    vals = [[str(r.get(h, "")) for h in HEADERS] for r in rows]
    _retry(lambda: ws.append_rows(vals, value_input_option="RAW", table_range="A1"),
           f"append {len(vals)} campaign rows")


def find_row(key: str) -> dict | None:
    return next((r for r in read_rows() if r["Key"] == key), None)


_phone_cache = {"at": 0.0, "rows": {}}


def find_by_phone(phone: str) -> dict | None:
    """Campaign row for this number (any group), cached 60 s."""
    p = norm_phone(phone)
    if not p or mode() == "off":
        return None
    if time.monotonic() - _phone_cache["at"] > 60:
        _phone_cache["rows"] = {norm_phone(r["Phone"]): r for r in read_rows() if r["Phone"]}
        _phone_cache["at"] = time.monotonic()
    return _phone_cache["rows"].get(p)


# ---------------------------------------------------------------------------
# Sending (patients) — test mode reroutes to Martin
# ---------------------------------------------------------------------------
_test_sent = Counter()


def _test_capped(job: str) -> bool:
    if mode() != "test":
        return False
    if _test_sent[job] >= config.CAMPAIGN_TEST_CAP:
        return True
    _test_sent[job] += 1
    return False


def send_sms(to: str, body: str) -> tuple[bool, str]:
    if mode() == "test":
        body = f"[CAMPAIGN TEST → {norm_phone(to)[-4:]}] {body}"
        to = config.MARKETING_TEST_PHONE
    return twilio_client.send_sms(to=to, body=body, sender=config.SMS_SENDER_NUMBER)


def send_email(to: str, subject: str, text: str, unsub: str = "") -> tuple[bool, str]:
    if mode() == "test":
        subject = f"[CAMPAIGN TEST] {subject}"
        to = config.MARKETING_TEST_EMAIL
    from marketing.templates import _to_html
    headers = ({"List-Unsubscribe": f"<{unsub}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"} if unsub else None)
    # Email replies go to Sinéad Rocks (Martin 2026-11-14), same as text replies.
    return resend_client.send_email(to=to, subject=subject, text=text, html=_to_html(text),
                                    headers=headers, reply_to=config.EMAIL_SINEAD[1])


# ---- One-click unsubscribe (link in every campaign email) ------------------
import hashlib as _hashlib
import hmac as _hmac


def _unsub_secret() -> bytes:
    return (os.environ.get("CAMPAIGN_UNSUB_SECRET") or os.environ.get("SLACK_SIGNING_SECRET")
            or "elite-campaign").encode()


def unsub_token(key: str) -> str:
    """Opaque token — no patient data in the URL."""
    return _hmac.new(_unsub_secret(), key.encode(), _hashlib.sha256).hexdigest()[:24]


def unsub_url(key: str) -> str:
    return f"{config.CAMPAIGN_PUBLIC_URL}/campaign/unsubscribe?t={unsub_token(key)}"


def unsubscribe(token: str) -> bool:
    """Mark the row 'Opted out' for email and untick Cliniko email marketing."""
    if not re.fullmatch(r"[0-9a-f]{24}", token or ""):
        return False
    for r in read_rows():
        if _hmac.compare_digest(unsub_token(r["Key"]), token):
            update_cells(r["_row"], {"Email OK": "Opted out"})
            if r["Patient ID"]:
                try:
                    phase2_put_patient(r["Patient ID"], {"accepted_email_marketing": False})
                except Exception as exc:
                    _alert(f"unsubscribe for patient {r['Patient ID']} saved here but Cliniko "
                           f"not updated (`{str(exc)[:120]}`) — please untick email marketing.")
            return True
    return False


def phase2_put_patient(pid: str, body: dict):
    s = requests.Session()
    s.auth = (os.environ["CLINIKO_API_KEY"], "")
    s.headers.update({"User-Agent": os.environ.get("CLINIKO_USER_AGENT", "Elite Physio automation"),
                      "Accept": "application/json", "Content-Type": "application/json"})
    shard = os.environ.get("CLINIKO_SHARD", "uk1")
    r = s.put(f"https://api.{shard}.cliniko.com/v1/patients/{pid}", data=json.dumps(body), timeout=20)
    r.raise_for_status()


def _consent_now(pid: str) -> tuple[bool, bool, str, str]:
    """Fresh (sms_ok, email_ok, mobile, email) from Cliniko just before a send."""
    p = next(iter(phase2.fetch_all("/patients", [("q[]", f"id:={pid}")])), None)
    if not p or p.get("archived_at") or p.get("deleted_at"):
        return False, False, "", ""
    mob = next((x.get("number") for x in p.get("patient_phone_numbers") or []
                if (x.get("phone_type") or "").lower() == "mobile"), "")
    return (bool(p.get("accepted_sms_marketing")), bool(p.get("accepted_email_marketing")),
            mob, p.get("email") or "")


def _log(r: dict, flow: str, channel: str, status: str):
    try:
        sent_log.log_send(r["Patient ID"], r["Name"], flow, channel, "campaign-2026-xmas",
                          flow, status if mode() == "live" else f"test-{status}")
    except Exception as exc:
        print(f"  WARN campaign sent-log write failed: {exc}")


def _alert(msg: str):
    try:
        dm(config.CEO_SLACK_EMAIL, f":rotating_light: *Campaign:* {msg}", force_martin=True)
    except Exception as exc:
        print(f"  WARN campaign alert failed: {exc}")


RECENT_MESSAGE_DAYS = 3

# ---- Repeat-send safeguards (Omagh lesson, 2026-11-14) ----------------------
# The Omagh launch re-texted 193 people because the "already sent" check failed
# open. Every guard below fails CLOSED: if we can't prove a message hasn't gone,
# it doesn't go.
_PROCESS_STARTED = time.monotonic()
STARTUP_GRACE_SECONDS = 15 * 60     # Render keeps the old instance running a few
                                    # minutes after a deploy; the new one waits.
_sent_in_process = Counter()        # (date, channel) -> sends by THIS process
_seen_this_process = set()          # (patient id, column) already sent here


def campaign_ledger() -> set | None:
    """(patient id, flow) pairs already sent for real, read fresh from the
    Sent Log — an independent record from the Campaign tab. None = the ledger
    couldn't be read, so the caller must send nothing."""
    sent_log._rows_cache = None
    rows = sent_log._rows()
    if rows is sent_log._READ_FAILED or not isinstance(rows, list):
        return None
    return {(str(r[1]), r[3]) for r in rows
            if len(r) >= 4 and str(r[3]).startswith("campaign_")
            and not (len(r) > 7 and str(r[7]).startswith(("failed", "test-")))}


def daily_ceiling(channel: str) -> int:
    """Hard stop per channel per day, counted in memory (not from the sheet,
    so a failed sheet write can't hide sends). Generous vs the real plan."""
    return config.CAMPAIGN_MOT_TEXTS_PER_DAY + 20 if channel == "sms" else 600


def recent_other_messages(days: int = RECENT_MESSAGE_DAYS) -> set[str]:
    """Patient IDs sent any non-campaign message (NPS, DNA rebook, etc.) in the
    last `days` — their campaign text waits so we don't pile messages on."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = set()
    rows = sent_log._rows()
    if not isinstance(rows, list):
        return out
    for r in rows:
        if len(r) < 4 or str(r[3]).startswith("campaign_"):
            continue
        try:
            ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff:
            out.add(str(r[1]))
    return out


def run_sends(now: datetime | None = None, log=print) -> dict:
    """Job 1: due texts and emails, paced, consent re-checked."""
    now = now or datetime.now(LONDON)
    d = today_l(now)
    stats = Counter()
    if now.weekday() >= 5 or now.hour >= 20:
        return stats
    times = getattr(config, "CAMPAIGN_SEND_TIMES", {})
    time_ok = lambda col: (now.hour, now.minute) >= times.get(col, (config.CAMPAIGN_SEND_HOUR, 0))
    if not any(time_ok(col) for col in ("Text 1", "Text 2", "Text 3", "Email 1", "Email 2")):
        return stats
    if mode() == "live" and time.monotonic() - _PROCESS_STARTED < STARTUP_GRACE_SECONDS:
        log("  campaign sends: waiting out the post-deploy grace period")
        return stats
    ledger = campaign_ledger()
    if ledger is None:
        log("  campaign sends: Sent Log unreadable — sending nothing this round")
        return stats
    rows = read_rows()
    budget = config.CAMPAIGN_MOT_TEXTS_PER_DAY - sum(
        1 for r in rows for col in ("Text 1", "Text 2", "Text 3") if r[col][:10] == d.isoformat())
    link = lambda r: config.CAMPAIGN_BOOKING_LINKS.get(r["Clinic"]) or config.CAMPAIGN_BOOKING_LINKS["Cookstown"]
    # Family phones: one campaign text per number, whoever it's for.
    texted_phones = {norm_phone(r["Phone"]) for r in rows if r["Text 1"]}
    opted_out = {norm_phone(r["Phone"]) for r in rows if r["Text OK"] == "Opted out"}
    recent = recent_other_messages()
    for r in sorted(rows, key=call_priority):
        if r["Patient ID"] in recent:
            stats["deferred_recent_message"] += 1
            continue
        jobs = []
        if text1_due(r, d):
            jobs.append(("Text 1", "sms", SMS_MOT_1 if r["Group"] == "mot" else SMS_CLUB_1,
                         f"campaign_{r['Group']}_text1"))
        elif text2_due(r, d):
            jobs.append(("Text 2", "sms", SMS_MOT_2, "campaign_mot_text2"))
        elif text3_due(r, d):
            jobs.append(("Text 3", "sms", SMS_MOT_3, "campaign_mot_text3"))
        if email1_due(r, d):
            jobs.append(("Email 1", "email", EMAIL_MOT_BODY, "campaign_mot_email1"))
        elif email2_due(r, d):
            jobs.append(("Email 2", "email", EMAIL_MOT2_BODY, "campaign_mot_email2"))
        jobs = [j for j in jobs if time_ok(j[0])]
        if not jobs:
            continue
        sms_ok, email_ok, mob, email = _consent_now(r["Patient ID"]) if r["Patient ID"] else (False, False, "", "")
        for col, ch, tmpl, flow in jobs:
            if ch == "sms" and col == "Text 1" and norm_phone(mob) in texted_phones:
                update_cells(r["_row"], {"Text OK": "No", "Notes": "texts off: shared family phone already texted · " + r["Notes"]})
                r["Text OK"] = "No"
                stats["shared_phone_skipped"] += 1
                continue
            soft = r["Text OK"] == "Soft" and sms_allowed(r)
            if ch == "sms" and norm_phone(mob) in opted_out:
                update_cells(r["_row"], {"Text OK": "Opted out"}); r["Text OK"] = "Opted out"
                stats["opted_out"] += 1
                continue
            if ch == "sms" and (budget <= 0 or not (sms_ok or soft) or not mob):
                if not (sms_ok or soft):
                    update_cells(r["_row"], {"Text OK": "No"}); r["Text OK"] = "No"
                    stats["consent_withdrawn"] += 1
                continue
            soft_e = r["Email OK"] == "Soft" and email_allowed(r)
            if ch == "email" and (not (email_ok or soft_e) or not email):
                if not (email_ok or soft_e):
                    update_cells(r["_row"], {"Email OK": "No"}); r["Email OK"] = "No"
                continue
            if _test_capped("sends"):
                stats["test_capped"] += 1
                continue
            # --- Repeat-send guards -------------------------------------
            pid = str(r["Patient ID"])
            stamp = now.strftime("%Y-%m-%d %H:%M")
            if mode() == "live" and ((pid, flow) in ledger or (pid, col) in _seen_this_process):
                # Sent Log says it already went but the tab cell is blank —
                # mend the tab, never re-send.
                try:
                    update_cells(r["_row"], {col: f"sent (from log) {stamp}"})
                except Exception:
                    pass
                r[col] = f"sent (from log) {stamp}"
                stats["blocked_already_sent"] += 1
                continue
            if _sent_in_process[(d, ch)] >= daily_ceiling(ch):
                _alert(f"daily {ch} ceiling ({daily_ceiling(ch)}) reached — sends stopped "
                       f"until tomorrow. Check the Campaign tab before it resumes.")
                log(f"  campaign sends: {dict(stats)} — CEILING HIT")
                return stats
            # Claim the cell BEFORE sending. If we can't write to the sheet,
            # we can't record the send, so we don't send.
            try:
                update_cells(r["_row"], {col: f"sending {stamp}"})
            except Exception as exc:
                _alert(f"couldn't write to the Campaign tab (`{str(exc)[:120]}`) — "
                       f"sends paused this round so nobody is texted twice.")
                log(f"  campaign sends: {dict(stats)} — sheet write failed, stopped")
                return stats
            r[col] = f"sending {stamp}"
            _seen_this_process.add((pid, col))
            ctx = dict(first=first_name(r["Name"]), physio=r["Physio"] or "the team",
                       link=link(r), unsub=unsub_url(r["Key"]))
            if ch == "sms":
                ok, info = send_sms(mob, tmpl.format(**ctx))
                budget -= 1
            else:
                subj = EMAIL_MOT2_SUBJECT if col == "Email 2" else EMAIL_MOT_SUBJECT
                ok, info = send_email(email, subj.format(**ctx), tmpl.format(**ctx),
                                      unsub=ctx["unsub"])
            _sent_in_process[(d, ch)] += 1
            status = "sent" if ok else f"failed: {info}"
            _log(r, flow, ch, status)
            ledger.add((pid, flow))
            # A failure is NOT retried automatically: a timeout can still have
            # delivered the text. The cell keeps "failed …" so it never re-fires.
            final = stamp if ok else f"failed {stamp}"
            try:
                update_cells(r["_row"], {col: final})
            except Exception:
                pass                       # the "sending …" claim still blocks a repeat
            r[col] = final
            if ok:
                if ch == "sms":
                    texted_phones.add(norm_phone(mob))
                stats[f"{flow}"] += 1
            else:
                stats["failed"] += 1
                _alert(f"{ch} failed for patient {r['Patient ID']} ({flow}): `{str(info)[:150]}` "
                       f"— not retried automatically.")
    log(f"  campaign sends: {dict(stats)}")
    sent = {k: v for k, v in stats.items() if k.startswith("campaign_")}
    if sent and mode() == "live":
        _alert_summary = ", ".join(f"{v} × {k.replace('campaign_mot_', '')}" for k, v in sent.items())
        try:
            dm(config.CEO_SLACK_EMAIL, f":outbox_tray: *Campaign sent this round:* {_alert_summary}"
               + (f" · blocked repeats: {stats['blocked_already_sent']}"
                  if stats.get("blocked_already_sent") else ""), force_martin=True)
        except Exception:
            pass
    return stats


# ---------------------------------------------------------------------------
# Slack (physios)
# ---------------------------------------------------------------------------
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


def member_by_display(display: str) -> dict | None:
    return next((m for m in config.TEAM if m["display"] == display), None)


def member_by_practitioner(pid: str) -> dict | None:
    return next((m for m in config.TEAM if str(pid) in m["practitioner_ids"]), None)


def physio_email(display: str) -> str:
    m = member_by_display(display) or {}
    return m.get("slack_email") or m.get("clinic_email") or ""


def dm(email: str, text: str, blocks=None, force_martin: bool = False):
    """DM a person; in test mode (or force_martin) it goes to Martin, labelled."""
    target = email
    if mode() == "test" or force_martin:
        if email != config.CEO_SLACK_EMAIL:
            text = f"_[CAMPAIGN TEST → would go to {email}]_\n{text}"
            if blocks:
                blocks = [{"type": "context", "elements": [{"type": "mrkdwn",
                           "text": f"[CAMPAIGN TEST → would go to {email}]"}]}] + blocks
        target = config.CEO_SLACK_EMAIL
    kw = {"channel": _uid(target), "text": text, "unfurl_links": False}
    if blocks:
        kw["blocks"] = blocks
    return slack().chat_postMessage(**kw)


def esc(t: str) -> str:
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def actions_block(key: str) -> dict:
    return {"type": "actions", "block_id": f"cmp_actions|{key}", "elements": [
        {"type": "button", "action_id": f"cmp|{c}|{key}", "value": key,
         "text": {"type": "plain_text", "text": label},
         **({"style": "primary"} if c == "booked" else {})}
        for c, label in CHOICES.items()]}


def person_blocks(r: dict, extra: str = "") -> list:
    bits = [f"*{esc(r['Name'])}*", f"<tel:{r['Phone']}|{r['Phone']}>" if r["Phone"] else "_no phone_",
            GROUP_LABEL[r["Group"]]]
    if r["Wave"]:
        bits.append(r["Wave"])
    if r["NPS"]:
        bits.append(r["NPS"])
    if int(r["Attempts"] or 0):
        bits.append(f"attempt {int(r['Attempts']) + 1}")
    link = (f"<{CLINIKO_WEB}/patients/{r['Patient ID']}|Cliniko>" if r["Patient ID"] else "")
    text = " · ".join(bits) + (f" · {link}" if link else "")
    if r["Notes"]:
        text += f"\n>{esc(r['Notes'][:250])}"
    if extra:
        text += f"\n{extra}"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}, actions_block(r["Key"])]


def run_call_lists(now: datetime | None = None, log=print) -> dict:
    """Job 2: Mon–Fri morning DM per physio with today's calls."""
    now = now or datetime.now(LONDON)
    d = today_l(now)
    stats = Counter()
    if not getattr(config, "CAMPAIGN_CALL_LISTS", True):
        return stats                      # physios aren't phoning (Martin 2026-11-14)
    if now.weekday() >= 5 or not in_window(d):
        return stats
    rows = read_rows()
    by_physio = defaultdict(list)
    for r in sorted(rows, key=call_priority):
        if not call_due(r, d):
            continue
        if not r["Physio"]:
            pick = pick_physio(r["Clinic"] or "Cookstown")
            if not pick:
                stats["no_physio"] += 1
                continue
            r["Physio"], r["Practitioner ID"] = pick
            update_cells(r["_row"], {"Physio": r["Physio"], "Practitioner ID": r["Practitioner ID"]})
        by_physio[r["Physio"]].append(r)
    for physio, rs in by_physio.items():
        rs = rs[:config.CAMPAIGN_CALLS_PER_PHYSIO_PER_DAY]
        email = physio_email(physio)
        if not email:
            stats["no_email"] += 1
            continue
        if _test_capped("call_lists"):
            stats["test_capped"] += 1
            continue
        head = (f":telephone_receiver: *Your Pre-Christmas calls today, {physio}* ({len(rs)})\n"
                f"Use any empty slots. Tap a button after each call so nobody rings them twice. "
                f"<{SCRIPTS_DOC}|Scripts>")
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": head}}]
        for r in rs:
            blocks += person_blocks(r)
        dm(email, head, blocks[:49])
        stats["lists"] += 1
        stats["people"] += len(rs)
    log(f"  campaign call lists: {dict(stats)}")
    return stats


def mark_opted_out(phone: str) -> int:
    """A STOP to the two-way number: never text this number again in the
    campaign (every row sharing it). Called from server.py."""
    p = norm_phone(phone)
    if not p or mode() == "off":
        return 0
    n = 0
    for r in read_rows():
        if norm_phone(r["Phone"]) == p and r["Text OK"] != "Opted out":
            update_cells(r["_row"], {"Text OK": "Opted out"})
            n += 1
    _phone_cache["at"] = 0.0
    return n


def post_reply_card(r: dict, body: str):
    """A text reply from a campaign patient → plain Slack DM to Sinéad Rocks
    (Martin 2026-11-14): name, number, their message, Cliniko link."""
    d = today_l()
    update_cells(r["_row"], {"Replied": d.isoformat()})
    r["Replied"] = d.isoformat()
    link = f" · <{CLINIKO_WEB}/patients/{r['Patient ID']}|Cliniko>" if r["Patient ID"] else ""
    text = (f":speech_balloon: *{esc(r['Name'])}* replied to the {GROUP_LABEL[r['Group']]} text"
            f" · <tel:{r['Phone']}|{r['Phone']}>{link}\n>{esc(body[:1000])}")
    email = getattr(config, "CAMPAIGN_REPLIES_EMAIL", "") or physio_email(r["Physio"]) or config.CEO_SLACK_EMAIL
    dm(email, text)


def handle_button(action_id: str, user_id: str, response_url: str, original_blocks: list,
                  fallback_text: str):
    """cmp|<choice>|<key> from a call list or reply card."""
    _, choice, key = action_id.split("|", 2)
    d = today_l()
    try:
        r = find_row(key)
        if not r:
            raise RuntimeError("row not found")
        update_cells(r["_row"], apply_choice(r, choice, f"<@{user_id}>", d))
        note = f"{CHOICES.get(choice, choice)} — <@{user_id}> {datetime.now(LONDON):%H:%M}"
        if choice in ("noanswer", "callback"):
            note += f" · back on your list {(d + timedelta(days=config.CAMPAIGN_RETRY_AFTER_DAYS)):%a %d %b}"
    except Exception as exc:
        print(f"campaign button error for {key}: {exc}")
        note = ":warning: Couldn't save that just now — please tap again in a minute."
        requests.post(response_url, json={"replace_original": True, "text": fallback_text,
                      "blocks": original_blocks + [{"type": "context", "elements": [
                          {"type": "mrkdwn", "text": note}]}]}, timeout=10)
        return
    blocks = []
    for b in original_blocks:
        if b.get("block_id") == f"cmp_actions|{key}":
            blocks.append({"type": "context", "block_id": f"cmp_done|{key}",
                           "elements": [{"type": "mrkdwn", "text": note}]})
        else:
            blocks.append(b)
    requests.post(response_url, json={"replace_original": True, "text": fallback_text,
                                      "blocks": blocks}, timeout=10)


# ---------------------------------------------------------------------------
# Who has free slots (cold leads, Meta leads)
# ---------------------------------------------------------------------------
_biz = {}
_pick_cache = {"day": None, "load": {}, "clinics": {}}


def _biz_names() -> dict:
    if not _biz:
        for b in phase2.fetch_all("/businesses"):
            name = (b.get("business_name") or b.get("label") or "")
            for c in ("Cookstown", "Maghera", "Omagh", "Armagh"):
                if c.lower() in name.lower():
                    _biz[str(b["id"])] = c
    return _biz


def _refresh_load(now: datetime):
    """Per physio: upcoming appointments next 3 days, and clinics worked (last 28 d)."""
    biz = _biz_names()
    load, clinics = Counter(), defaultdict(set)
    z = lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for a in phase2.fetch_all("/individual_appointments", [
            ("q[]", f"starts_at:>={z(now - timedelta(days=28))}"),
            ("q[]", f"starts_at:<{z(now + timedelta(days=3))}")]):
        if a.get("cancelled_at") or a.get("deleted_at"):
            continue
        m = member_by_practitioner(phase2.id_from_link(a.get("practitioner")))
        if not m or m.get("owner_consultant") or not config.is_active_on(m):
            continue
        clinic = biz.get(phase2.id_from_link(a.get("business")), "")
        s = phase2.parse_iso(a["starts_at"])
        if s >= now:
            load[(m["display"], clinic)] += 1
        clinics[m["display"]].add(clinic)
    _pick_cache.update(day=today_l(now), load=load, clinics=clinics)


def pick_physio(clinic: str, now: datetime | None = None) -> tuple[str, str] | None:
    """(display, practitioner_id) of the non-owner physio who works at `clinic`
    and has the fewest booked appointments over the next 3 days."""
    now = now or datetime.now(LONDON)
    if _pick_cache["day"] != today_l(now):
        _refresh_load(now)
    cands = [disp for disp, cs in _pick_cache["clinics"].items() if clinic in cs]
    if not cands:
        return None
    best = min(cands, key=lambda disp: (_pick_cache["load"].get((disp, clinic), 0), disp))
    m = member_by_display(best)
    return best, m["practitioner_ids"][0]


# ---------------------------------------------------------------------------
# Outcomes, voucher, scorecard
# ---------------------------------------------------------------------------
def run_outcomes(now: datetime | None = None, log=print) -> dict:
    """Job 3a: mark who booked / attended since the campaign started."""
    now = now or datetime.now(LONDON)
    stats = Counter()
    start = f"{config.CAMPAIGN_START}T00:00:00Z"
    created = list(phase2.fetch_all("/individual_appointments", [("q[]", f"created_at:>={start}")]))
    started = list(phase2.fetch_all("/individual_appointments", [("q[]", f"starts_at:>={start}")]))
    booked, attended, mot_att = {}, {}, {}
    for a in created:
        if a.get("cancelled_at") or a.get("deleted_at"):
            continue
        pid = phase2.id_from_link(a["patient"])
        booked[pid] = min(booked.get(pid, "9"), (a.get("created_at") or "")[:10])
    for a in started:
        if a.get("cancelled_at") or a.get("deleted_at") or a.get("did_not_arrive"):
            continue
        s = phase2.parse_iso(a["starts_at"])
        if s > now - timedelta(hours=2):
            continue
        pid = phase2.id_from_link(a["patient"])
        attended[pid] = min(attended.get(pid, "9"), s.date().isoformat())
        if config.CAMPAIGN_MOT_TYPE_ID and phase2.id_from_link(a["appointment_type"]) == config.CAMPAIGN_MOT_TYPE_ID:
            mot_att[pid] = s.date().isoformat()
    # Cold leads with no Cliniko record yet: match new patients by phone.
    rows = read_rows()
    need = [r for r in rows if not r["Patient ID"] and r["Phone"]]
    if need:
        by_ph = {}
        for p in phase2.fetch_all("/patients", [("q[]", f"created_at:>={start}")]):
            for n in p.get("patient_phone_numbers") or []:
                by_ph[norm_phone(n.get("number"))] = str(p["id"])
        for r in need:
            pid = by_ph.get(norm_phone(r["Phone"]))
            if pid:
                update_cells(r["_row"], {"Patient ID": pid}); r["Patient ID"] = pid
                stats["matched_new"] += 1
    for r in rows:
        pid, cells = r["Patient ID"], {}
        if not pid:
            continue
        if pid in booked and not r["Booked"].startswith(booked[pid]):
            if not r["Booked"] or "(call)" in r["Booked"]:
                cells["Booked"] = booked[pid]
        if pid in attended and not r["Attended"]:
            cells["Attended"] = attended[pid]
        if pid in mot_att and not r["MOT Attended"]:
            cells["MOT Attended"] = mot_att[pid]
        if cells:
            update_cells(r["_row"], cells)
            stats["updated"] += 1
    log(f"  campaign outcomes: {dict(stats)} (booked {len(booked)}, attended {len(attended)})")
    return stats


def run_vouchers(now: datetime | None = None, log=print) -> dict:
    """Job 4: free Pilates class text after an attended MOT (needs type id + code + link)."""
    stats = Counter()
    link = os.environ.get("CAMPAIGN_PILATES_LINK", "").strip()
    if not (config.CAMPAIGN_MOT_TYPE_ID and config.CAMPAIGN_VOUCHER_CODE and link):
        return stats
    now = now or datetime.now(LONDON)
    if now.hour < config.CAMPAIGN_SEND_HOUR or now.hour >= 20:
        return stats
    if mode() == "live" and time.monotonic() - _PROCESS_STARTED < STARTUP_GRACE_SECONDS:
        return stats
    ledger = campaign_ledger()
    if ledger is None:
        return stats
    for r in read_rows():
        if not r["MOT Attended"] or r["Voucher Sent"] or not r["Patient ID"]:
            continue
        if mode() == "live" and ((str(r["Patient ID"]), "campaign_pilates_voucher") in ledger
                                 or (str(r["Patient ID"]), "Voucher Sent") in _seen_this_process):
            try:
                update_cells(r["_row"], {"Voucher Sent": "sent (from log)"})
            except Exception:
                pass
            continue
        _, _, mob, _ = _consent_now(r["Patient ID"])
        if not mob:
            stats["no_mobile"] += 1
            continue
        if _test_capped("vouchers"):
            continue
        bf = booked_by_deadline(r) and config.CAMPAIGN_VOUCHER_CODE_2FOR1
        body = (SMS_VOUCHER_BF if bf else SMS_VOUCHER).format(
            first=first_name(r["Name"]), code=config.CAMPAIGN_VOUCHER_CODE,
            code2=config.CAMPAIGN_VOUCHER_CODE_2FOR1, pilates_link=link,
            expiry=config.CAMPAIGN_VOUCHER_EXPIRY)
        stamp = now.strftime("%Y-%m-%d %H:%M")
        try:
            update_cells(r["_row"], {"Voucher Sent": f"sending {stamp}"})
        except Exception as exc:
            _alert(f"couldn't write to the Campaign tab (`{str(exc)[:120]}`) — vouchers paused.")
            return stats
        _seen_this_process.add((str(r["Patient ID"]), "Voucher Sent"))
        ok, info = send_sms(mob, body)
        _log(r, "campaign_pilates_voucher", "sms", "sent" if ok else f"failed: {info}")
        try:
            update_cells(r["_row"], {"Voucher Sent": stamp if ok else f"failed {stamp}"})
        except Exception:
            pass
        if ok:
            stats["sent"] += 1
        else:
            _alert(f"voucher text failed for patient {r['Patient ID']}: `{str(info)[:150]}`")
    log(f"  campaign vouchers: {dict(stats)}")
    return stats


def scorecard_text(rows: list[dict]) -> str:
    n = scorecard_numbers(rows)
    lines = [":bar_chart: *Pre-Christmas campaign — weekly scorecard*", "```"]
    lines.append(f"{'':18}{'list':>5}{'text':>6}{'call':>6}{'reply':>6}{'book':>6}{'attd':>6}{'£ est':>8}{'£ tgt':>8}")
    tot_e = tot_t = 0
    for g, lab in (("mot", "MOT"), ("club", "Club / GAA"), ("cold", "Cold leads"), ("rebook", "IA rebook")):
        x = n[g]
        tot_e += x["est_value"]; tot_t += x["target_value"]
        lines.append(f"{lab:18}{x['listed']:>5}{x['texted']:>6}{x['called']:>6}{x['replied']:>6}"
                     f"{x['booked']:>6}{x['attended']:>6}{x['est_value']:>8,}{x['target_value']:>8,}")
    lines.append(f"{'Total':18}{'':>35}{tot_e:>8,}{tot_t:>8,}")
    lines.append("```")
    lines.append("_£ est = booked × value per patient (MOT £180, GAA £99, new patient £286). "
                 "Not interested: " + str(sum(n[g]["not_interested"] for g in n)) + "._")
    return "\n".join(lines)


def run_scorecard(now: datetime | None = None, log=print):
    rows = read_rows()
    text = scorecard_text(rows)
    for email in config.CAMPAIGN_SCORECARD_EMAILS:
        if _test_capped("scorecard"):
            break
        dm(email, text)
    log("  campaign scorecard sent")


# ---------------------------------------------------------------------------
# List building (MOT + club) — weekly refresh, also used for the first seed
# ---------------------------------------------------------------------------
PHYSIO_TYPES = re.compile(r"initial|review|consultation|shockwave|injection|ultrasound|"
                          r"back to performance|injury update|gaelic groin|club follow", re.I)
CLUB_TYPES = re.compile(r"club", re.I)


PHI_TYPES = re.compile(r"private health insurance", re.I)


def invoice_index(since: datetime):
    """Cliniko invoices since `since`: {appointment id: total} and
    {(patient id, issue date): total} (fallback when not linked)."""
    by_appt, by_day = {}, {}
    for i in phase2.fetch_all("/invoices", [("q[]", f"issue_date:>={since.strftime('%Y-%m-%d')}")]):
        if i.get("deleted_at") or i.get("archived_at"):
            continue
        amt = float(i.get("total_amount") or 0)
        aid = phase2.id_from_link(i.get("appointment") or {})
        if aid:
            by_appt[aid] = by_appt.get(aid, 0) + amt
        pid = phase2.id_from_link(i.get("patient") or {})
        key = (pid, (i.get("issue_date") or "")[:10])
        by_day[key] = by_day.get(key, 0) + amt
    return by_appt, by_day


def last_visit_price(a: dict, by_appt: dict, by_day: dict, types: dict) -> float:
    """What the visit was invoiced at in Cliniko. No invoice → 0 (club or
    insurer billed through Xero), except private health insurance visits,
    which count as full rate."""
    aid = str(a["id"])
    if aid in by_appt:
        return by_appt[aid]
    key = (phase2.id_from_link(a["patient"]), a["starts_at"][:10])
    if key in by_day:
        return by_day[key]
    if PHI_TYPES.search(types.get(phase2.id_from_link(a["appointment_type"]), "")):
        return 999.0
    return 0.0


def build_mot_candidates(now: datetime | None = None, log=print) -> list[dict]:
    now = (now or datetime.now(LONDON)).astimezone(timezone.utc)
    types = {str(t["id"]): t["name"] for t in phase2.fetch_all("/appointment_types")}
    biz = _biz_names()
    # MOT (Martin 2026-11-14): last physio visit 1 Oct 2025 – 30 Sep 2026, at
    # least 3 physio sessions attended in that window, nothing since, nothing
    # booked, not an NPS passive or detractor. Club players keep the earlier
    # rule (2+ sessions, last seen 2 weeks – 6 months) until their campaign is
    # worked through.
    mot_from = datetime.fromisoformat(config.CAMPAIGN_MOT_LAST_SEEN_FROM).replace(tzinfo=LONDON)
    mot_to = datetime.fromisoformat(config.CAMPAIGN_MOT_LAST_SEEN_TO).replace(tzinfo=LONDON) + timedelta(days=1)
    # Look back to Jan 2025 so anyone who was ever a club player recently is
    # kept OFF the gen-pop MOT list (Martin 2026-11-14: MOT = gen pop only).
    fetch_from = min(mot_from.astimezone(timezone.utc), now - timedelta(days=260),
                     datetime(2025, 1, 1, tzinfo=timezone.utc))
    appts = [a for a in phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={fetch_from.strftime('%Y-%m-%dT00:00:00Z')}")])
        if not a.get("deleted_at") and not a.get("cancelled_at")]
    by_p = defaultdict(list)
    for a in appts:
        by_p[phase2.id_from_link(a["patient"])].append(a)
    inv_by_appt, inv_by_day = invoice_index(fetch_from)
    price_src = Counter()
    club_lo, club_hi = now - timedelta(days=183), now - timedelta(days=14)
    picked = []
    for pid, apps in by_p.items():
        phys = [a for a in apps if PHYSIO_TYPES.search(types.get(phase2.id_from_link(a["appointment_type"]), ""))]
        att = [a for a in phys if not a.get("did_not_arrive") and phase2.parse_iso(a["starts_at"]) <= now]
        if not att or any(phase2.parse_iso(a["starts_at"]) > now for a in apps):
            continue
        last = max(att, key=lambda a: a["starts_at"])
        ls = phase2.parse_iso(last["starts_at"])
        if any(not a.get("did_not_arrive") and ls < phase2.parse_iso(a["starts_at"]) <= now for a in apps):
            continue
        # Gen pop vs club follows what their MOST RECENT physio visit was
        # invoiced at (Martin 2026-11-14): £49+ = gen pop (MOT), under £49 =
        # club. No Cliniko invoice = club/insurer billed via Xero, except
        # private-health-insurance appointments (full rate via the insurer).
        price = last_visit_price(last, inv_by_appt, inv_by_day, types)
        club = price < config.CAMPAIGN_GENPOP_MIN_PRICE
        src = ("insurance (no invoice)" if price == 999.0 else
               "no Cliniko invoice" if price == 0 and str(last["id"]) not in inv_by_appt else
               f"invoiced {'£49+' if not club else 'under £49'}")
        price_src[src] += 1
        is_club_type = lambda a: bool(CLUB_TYPES.search(types.get(phase2.id_from_link(a["appointment_type"]), "")))
        if club:
            if len(att) < 2:
                continue
            if not (club_lo <= ls <= club_hi) and not (mot_from <= ls < mot_to):
                continue
        else:
            # 3+ non-club physio sessions in the window.
            in_win = [a for a in att if not is_club_type(a)
                      and mot_from <= phase2.parse_iso(a["starts_at"]) < mot_to]
            if not (mot_from <= ls < mot_to) or len(in_win) < config.CAMPAIGN_MOT_MIN_SESSIONS:
                continue
        m = member_by_practitioner(phase2.id_from_link(last["practitioner"]))
        picked.append(dict(pid=pid, last=ls, club=club,
                           physio=(m or {}).get("display", ""),
                           prac=phase2.id_from_link(last["practitioner"]),
                           clinic=biz.get(phase2.id_from_link(last["business"]), "")))
    # NPS (worst score wins) — detractors are left out.
    nps = {}
    sh = _retry(lambda: _gspread().open_by_key(config.MARKETING_SPREADSHEET_ID), "open marketing sheet")
    for tab in ("NPS - Raw Data", "NPS - Detractor Tracker"):
        for rec in _retry(lambda: sh.worksheet(tab).get_all_records(), f"read {tab}"):
            p = str(rec.get("Patient ID") or "").lstrip("'").strip()
            sc = str(rec.get("Score") or "").strip()
            if p:
                nps[p] = min(nps.get(p, 10), int(sc) if sc.isdigit() else 0)
    out = []
    staff = {n.lower() for m in config.TEAM for n in m["full_names"]}
    for c in picked:
        sc = nps.get(c["pid"])
        # MOT: promoters or no score only. Club: detractors left out.
        if sc is not None and sc <= (8 if not c["club"] else 6):
            continue
        p = next(iter(phase2.fetch_all("/patients", [("q[]", f"id:={c['pid']}")])), None)
        if not p or p.get("archived_at") or p.get("deleted_at") or p.get("deceased"):
            continue
        if f"{p.get('first_name', '')} {p.get('last_name', '')}".strip().lower() in staff:
            continue
        mob = next((x.get("number") for x in p.get("patient_phone_numbers") or []
                    if (x.get("phone_type") or "").lower() == "mobile"), "") or \
            next((x.get("number") for x in p.get("patient_phone_numbers") or []), "")
        owner = (member_by_display(c["physio"]) or {}).get("owner_consultant")
        days = (now - c["last"]).days
        # Under-18s: no marketing texts/emails; their physio rings a parent.
        minor = False
        try:
            dob = date.fromisoformat((p.get("date_of_birth") or "")[:10])
            minor = (date.today() - dob).days < 18 * 365
        except ValueError:
            pass
        if minor:
            p = dict(p, accepted_sms_marketing=False, accepted_email_marketing=False)
        out.append({
            "Key": f"{'club' if c['club'] else 'mot'}:{c['pid']}",
            "Group": "club" if c["club"] else "mot",
            "Wave": (("1 (stopped <3 months)" if days <= 92 else "2 (3–6 months)") if c["club"]
                     else ("1 (last seen Jul–Sep 2026)" if c["last"] >= datetime(2026, 7, 1, tzinfo=timezone.utc)
                           else "2 (last seen Oct 2025–Jun 2026)")),
            "Patient ID": c["pid"],
            "Name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "Phone": mob, "Email": p.get("email") or "",
            "Text OK": ("Under 18" if minor else "Yes" if p.get("accepted_sms_marketing")
                        else "Soft" if not c["club"] else "No"),
            "Email OK": ("Under 18" if minor else "Yes" if p.get("accepted_email_marketing")
                         else "Soft" if (not c["club"] and p.get("email")) else "No"),
            # Owners take no extra patients: their lapsed patients go to a senior.
            "Physio": "" if owner else c["physio"],
            "Practitioner ID": "" if owner else c["prac"],
            "Clinic": c["clinic"],
            "NPS": "" if sc is None else ("Promoter" if sc >= 9 else "Passive"),
            "Added": today_l().isoformat(),
            "Notes": f"Last seen {c['last'].astimezone(LONDON):%d %b} by {c['physio']}" +
                     (" (owner patient → senior physio)" if owner else ""),
        })
    log(f"  campaign MOT candidates: {len(out)} | last-visit price source: {dict(price_src)}")
    return out


def run_refresh(now: datetime | None = None, log=print) -> int:
    rows = read_rows()
    have = {r["Key"] for r in rows} | {f"mot:{r['Patient ID']}" for r in rows if r["Patient ID"]} \
        | {f"club:{r['Patient ID']}" for r in rows if r["Patient ID"]}
    new = [c for c in build_mot_candidates(now, log) if c["Key"] not in have]
    append_rows(new)
    log(f"  campaign refresh: {len(new)} new people added")
    return len(new)


# ---------------------------------------------------------------------------
# Scheduler (server.py thread)
# ---------------------------------------------------------------------------
_LOCK_PATH = "/tmp/campaign.lock"


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


def _due_today(job: str, hour: int, now: datetime, last: dict) -> bool:
    return now.hour >= hour and last.get(job) != today_l(now)


def run_forever(interval: int = 300):
    """Wake every 5 min; each job runs once a day (sends every wake in the window)."""
    last: dict[str, date] = {}
    while True:
        try:
            if mode() != "off":
                with _only_one_runner() as mine:
                    now = datetime.now(LONDON)
                    d = today_l(now)
                    if mine and d <= date.fromisoformat(config.CAMPAIGN_END) + timedelta(days=7):
                        if now.weekday() == 0 and _due_today("refresh", 6, now, last):
                            run_refresh(now); last["refresh"] = d
                        if _due_today("outcomes", config.CAMPAIGN_OUTCOME_HOUR, now, last):
                            run_outcomes(now); last["outcomes"] = d
                        if _due_today("calls", config.CAMPAIGN_CALL_LIST_HOUR, now, last):
                            run_call_lists(now); last["calls"] = d
                        if (now.weekday() == config.CAMPAIGN_SCORECARD_WEEKDAY
                                and _due_today("scorecard", config.CAMPAIGN_SCORECARD_HOUR, now, last)):
                            run_scorecard(now); last["scorecard"] = d
                        # Sends + vouchers every 30 min in the send window
                        # (each reads the tab once — spares the shared Sheets quota).
                        if time.monotonic() - last.get("_sends", 0) >= 1800:
                            run_sends(now)
                            run_vouchers(now)
                            last["_sends"] = time.monotonic()
        except Exception as exc:
            print(f"campaign loop error: {exc}")
            try:
                _alert(f"loop error `{str(exc)[:200]}` — will retry in 5 min.")
            except Exception:
                pass
        time.sleep(interval)


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(override=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"refresh": run_refresh, "outcomes": run_outcomes, "calls": run_call_lists,
     "sends": run_sends, "vouchers": run_vouchers, "scorecard": run_scorecard}[cmd]()
