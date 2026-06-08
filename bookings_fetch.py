"""New Patient Bookings tracker.

Trawls Cliniko for newly-booked initial assessments and logs them to the
"Elite Physio - New Patient Bookings" Google Sheet — one weekly tab per
Sunday-Saturday week, plus a Dashboard and a manual Leads tab.

Run by launchd via bookings_poll.sh at 06:00, 09:00, 12:00, 15:00, 18:00, 20:45.

Modes:
  (no flag)   preview — prints the bookings table, writes nothing
  --summary   prints counts only (no patient names) — used for safe testing
  --write     commit to the Sheet + refresh Dashboard + DM reception on Slack

Idempotent: re-runs skip any appointment already in the sheet (matched by
appointment_id), so the 6 daily trawls never double-log a booking.
"""

import os
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

import phase2          # reuse the Cliniko client (fetch_all, parse_iso, id_from_link, …)
import config

load_dotenv(override=True)

LONDON = ZoneInfo("Europe/London")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "service_account.json")
CLINIKO_WEB_DOMAIN = "elite-physiotherapy.uk1.cliniko.com"

BOOKINGS_LOOKBACK_DAYS = 2   # rolling window; dedup handles the overlap

# Weekly-tab columns. The last two are hidden helper columns.
COLUMNS = [
    "date_booked", "appointment_date", "patient", "clinic",
    "new_or_past", "appointment_type", "booking_source",
    "referrer", "body_area", "insurer", "auth_code", "booking_notes",
    "appointment_id", "pulled_at",
]
HIDDEN = ("appointment_id", "pulled_at")
HEADERS = {
    "date_booked": "Date Booked",
    "appointment_date": "Appointment Date",
    "patient": "Patient",
    "clinic": "Clinic",
    "new_or_past": "New / Past Patient",
    "appointment_type": "Appointment Type",
    "booking_source": "Booking Source",
    "referrer": "Referrer",
    "body_area": "Body Area",
    "insurer": "Insurer",
    "auth_code": "Auth Code",
    "booking_notes": "Notes",
    "appointment_id": "appointment_id",
    "pulled_at": "pulled_at",
}

LEADS_TAB = "Leads"
LEADS_HEADERS = ["Date", "Patient", "Clinic", "Body Area", "Referrer",
                 "Reason Not Booked", "Callback Needed (by who)", "Notes"]
DASHBOARD_TAB = "Dashboard"


# ---------------- helpers ----------------

def created_window(lookback_days, since_date=None):
    """UTC ISO window — the last `lookback_days` days, or from `since_date`
    (YYYY-MM-DD, clinic-local) if given. `since_date` drives one-off backfills."""
    end = datetime.now(timezone.utc)
    if since_date:
        start = datetime.strptime(since_date, "%Y-%m-%d").replace(
            tzinfo=LONDON).astimezone(timezone.utc)
    else:
        start = end - timedelta(days=lookback_days)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def fmt_local(ts):
    dt = phase2.parse_iso(ts)
    return dt.astimezone(LONDON).strftime("%Y-%m-%d %H:%M") if dt else ""


def week_tab_name(dt_local):
    """Sunday-anchored weekly tab name, e.g. 'W/C 17 May 2026'."""
    days_since_sunday = (dt_local.weekday() + 1) % 7   # Mon=0 … Sun=6
    sunday = dt_local - timedelta(days=days_since_sunday)
    return f"W/C {sunday.strftime('%d %b %Y')}"


def clinic_for(appt):
    bid = phase2.id_from_link(appt.get("business"))
    return config.CLINIKO_BUSINESS_TO_CLINIC.get(str(bid), config.DEFAULT_CLINIC)


def _split_auth(value):
    """'AXA 12345' -> ('AXA', '12345'); unrecognised insurer -> ('', whole)."""
    v = (value or "").strip()
    for ins in config.KNOWN_INSURERS:
        if v.lower().startswith(ins.lower()):
            return ins, v[len(ins):].strip(" -:")
    return "", v


def parse_booking_note(note):
    """Parse 'Ref: Lavey | Area: hamstring | Auth: AXA 12345' into fields.
    Anything not matching a known key is kept as free-text notes."""
    out = {"referrer": "", "body_area": "", "insurer": "", "auth_code": "",
           "leftover": ""}
    if not note:
        return out
    leftover = []
    for seg in str(note).split("|"):
        seg = seg.strip()
        if not seg:
            continue
        if ":" in seg:
            key, val = seg.split(":", 1)
            k, v = key.strip().lower(), val.strip()
            if k in ("ref", "referrer", "referral"):
                out["referrer"] = v
            elif k in ("area", "body area", "pathology", "injury"):
                out["body_area"] = v
            elif k in ("auth", "authorisation", "authorization"):
                out["insurer"], out["auth_code"] = _split_auth(v)
            else:
                leftover.append(seg)
        else:
            leftover.append(seg)
    out["leftover"] = " | ".join(leftover)
    return out


# ---------------- collection ----------------

def collect_bookings(lookback_days=BOOKINGS_LOOKBACK_DAYS, skip_ids=None,
                     since_date=None):
    """Return a list of booking row dicts for IA appointments created recently
    (or since `since_date` for a backfill)."""
    skip_ids = skip_ids or set()
    s_iso, e_iso = created_window(lookback_days, since_date)
    pulled_at = datetime.now(LONDON).strftime("%Y-%m-%d %H:%M")

    types_by_id = {str(t["id"]): t.get("name", "?")
                   for t in phase2.fetch_all("/appointment_types")}

    appts = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"created_at:>={s_iso}"),
        ("q[]", f"created_at:<{e_iso}"),
    ]))
    window_desc = (f"since {since_date}" if since_date
                   else f"in the last {lookback_days} days")
    print(f"Cliniko: {len(appts)} appointments created {window_desc}; "
          f"already in sheet: {len(skip_ids)}")

    history_cache = {}
    rows = []
    for a in appts:
        type_id = phase2.id_from_link(a.get("appointment_type"))
        if type_id not in config.BOOKINGS_IA_TYPE_IDS:
            continue   # not an initial assessment — ignore
        if str(a.get("id")) in skip_ids:
            continue   # already logged on a previous trawl
        if a.get("cancelled_at"):
            continue   # booking already cancelled — not a live new booking

        patient_id = phase2.id_from_link(a.get("patient"))
        if patient_id and patient_id not in history_cache:
            try:
                history_cache[patient_id] = phase2.fetch_patient_full_history(patient_id)
            except Exception as e:
                print(f"  WARN history fetch failed ({patient_id}): {e} — skipping, "
                      f"next trawl retries")
                history_cache[patient_id] = None
        history = history_cache.get(patient_id)
        if history is None:
            continue

        this_start = a.get("starts_at") or ""
        prior = any(
            str(h.get("id")) != str(a.get("id"))
            and (h.get("starts_at") or "") < this_start
            for h in (history or [])
        )
        note = parse_booking_note(a.get("notes"))
        rows.append({
            "date_booked": fmt_local(a.get("created_at")),
            "appointment_date": fmt_local(a.get("starts_at")),
            "patient": (a.get("patient_name") or "?").strip(),
            "clinic": clinic_for(a),
            "new_or_past": "Past" if prior else "New",
            "appointment_type": types_by_id.get(type_id, "?"),
            "booking_source": "Online" if a.get("booking_ip_address") else "",
            "referrer": note["referrer"] or ("Online" if a.get("booking_ip_address") else ""),
            "body_area": note["body_area"],
            "insurer": note["insurer"],
            "auth_code": note["auth_code"],
            "booking_notes": note["leftover"],
            "appointment_id": str(a.get("id")),
            "pulled_at": pulled_at,
            "_patient_id": patient_id,
        })
    rows.sort(key=lambda r: r["date_booked"])
    return rows


# ---------------- Google Sheets ----------------

def _sheets_credentials():
    """Prefer the SERVICE_ACCOUNT_JSON env var (cloud / Render); fall back to
    the local service_account.json file for running on the Mac."""
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=SHEETS_SCOPES)
    return Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SHEETS_SCOPES)


def open_spreadsheet():
    if not config.BOOKINGS_SPREADSHEET_ID:
        raise RuntimeError("config.BOOKINGS_SPREADSHEET_ID is not set — create the "
                           "bookings sheet, share it with the service account, and "
                           "paste the ID into config.py")
    return gspread.authorize(_sheets_credentials()).open_by_key(config.BOOKINGS_SPREADSHEET_ID)


def _gs_retry(call, label, attempts=6):
    """Retry a gspread call on transient errors.

    Mon 8 Jun 2026 12:00 BST poll failed with HTTP 429 (Sheets quota: 60
    reads/min/user) because the team-email cron was retrying at the same
    time. Adding a backoff layer here so future quota brushes self-heal.

    Retries: ConnectionError, Timeout, ConnectionReset, HTTP 429/5xx.
    Backoff: 2, 4, 8, 16, 32 seconds.
    """
    import time as _t, socket
    import requests as _req
    delay = 2
    for i in range(attempts):
        try:
            return call()
        except (_req.exceptions.ConnectionError,
                _req.exceptions.Timeout,
                ConnectionResetError,
                socket.timeout) as e:
            if i == attempts - 1:
                raise
            print(f"  {label}: transient {type(e).__name__}, "
                  f"retry {i+1}/{attempts-1} in {delay}s", flush=True)
            _t.sleep(delay); delay *= 2
        except gspread.exceptions.APIError as e:
            # gspread wraps the HTTP response; extract status code
            code = None
            try:
                code = e.response.status_code  # type: ignore[attr-defined]
            except Exception:
                pass
            if code in (429, 500, 502, 503, 504) and i < attempts - 1:
                print(f"  {label}: Sheets API {code}, "
                      f"retry {i+1}/{attempts-1} in {delay}s", flush=True)
                _t.sleep(delay); delay *= 2
                continue
            raise


def get_or_create_week_tab(sh, tab_name):
    try:
        return sh.worksheet(tab_name), False
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=300, cols=len(COLUMNS))
        ws.append_row([HEADERS[c] for c in COLUMNS], value_input_option="RAW")
        ws.hide_columns(COLUMNS.index(HIDDEN[0]), len(COLUMNS))
        return ws, True


def ensure_leads_tab(sh):
    try:
        sh.worksheet(LEADS_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=LEADS_TAB, rows=300, cols=len(LEADS_HEADERS))
        ws.append_row(LEADS_HEADERS, value_input_option="RAW")
        ws.format("A1:H1", {"textFormat": {"bold": True}})


def cell_for(row, col):
    val = row.get(col, "") or ""
    if col == "patient" and row.get("_patient_id"):
        name = str(val).replace('"', '""')
        return (f'=HYPERLINK("https://{CLINIKO_WEB_DOMAIN}/patients/'
                f'{row["_patient_id"]}", "{name}")')
    return str(val)


def existing_appointment_ids(sh):
    col = COLUMNS.index("appointment_id") + 1
    ids = set()
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            vals = _gs_retry(lambda c=col, w=ws: w.col_values(c),
                             f"col_values({ws.title})")
            ids.update(v for v in vals if v and v != "appointment_id")
        except Exception as e:
            print(f"  WARN couldn't read ids from {ws.title}: {e}")
    return ids


def write_to_sheet(sh, rows):
    by_tab = {}
    for r in rows:
        dt = datetime.strptime(r["date_booked"], "%Y-%m-%d %H:%M")
        by_tab.setdefault(week_tab_name(dt), []).append(r)
    appt_col = COLUMNS.index("appointment_id") + 1
    all_new = []
    for tab_name, tab_rows in sorted(by_tab.items()):
        ws, created = get_or_create_week_tab(sh, tab_name)
        existing = (set() if created
                    else set(_gs_retry(lambda c=appt_col, w=ws: w.col_values(c),
                                       f"col_values({tab_name})")))
        new = [r for r in tab_rows if r["appointment_id"] not in existing]
        if new:
            payload = [[cell_for(r, c) for c in COLUMNS] for r in new]
            _gs_retry(lambda p=payload, w=ws: w.append_rows(
                p, value_input_option="USER_ENTERED"),
                f"append_rows({tab_name})")
        all_new += new
        flag = "(NEW TAB)" if created else "(existing)"
        print(f"  Tab '{tab_name}' {flag}: appended {len(new)} of {len(tab_rows)}")
    return all_new


# ---------------- Dashboard ----------------

def _lead_period_counts(sh):
    """Per-week / per-month counts of leads that have NOT been booked, from the
    Leads tab. A lead with Status == "booked" is excluded (it converted);
    everything else (pending / declined / lost / blank) counts as not booked."""
    weekly, monthly = {}, {}
    try:
        ws = sh.worksheet(LEADS_TAB)
        records = ws.get_all_records()
    except Exception:
        return weekly, monthly
    for rec in records:
        # Skip leads that have been booked — they are not "not booked".
        if str(rec.get("Status") or "").strip().lower() == "booked":
            continue
        raw = str(rec.get("Date") or "").strip()
        dt = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d %b %Y", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt)
                break
            except ValueError:
                continue
        if not dt:
            continue
        weekly[week_tab_name(dt)] = weekly.get(week_tab_name(dt), 0) + 1
        monthly[dt.strftime("%Y-%m")] = monthly.get(dt.strftime("%Y-%m"), 0) + 1
    return weekly, monthly


def write_dashboard(sh):
    """Rebuild the Dashboard tab — booking counts by week and by calendar month."""
    all_rows = []
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            all_rows += ws.get_all_records()
        except Exception as e:
            print(f"  WARN couldn't read {ws.title}: {e}")

    def bucket(rows):
        return {
            "total": len(rows),
            "new": sum(1 for r in rows if str(r.get("New / Past Patient")) == "New"),
            "past": sum(1 for r in rows if str(r.get("New / Past Patient")) == "Past"),
            "online": sum(1 for r in rows if str(r.get("Booking Source")) == "Online"),
        }

    weekly, monthly = {}, {}
    for r in all_rows:
        booked = str(r.get("Date Booked") or "").strip()
        # Normally "YYYY-MM-DD HH:MM", but a hand-edited cell can be date-only
        # ("YYYY-MM-DD"). Parse tolerantly so one odd row can't crash the whole
        # Dashboard refresh (and fail the cron with exit 1).
        dt = None
        for fmt, n in (("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
            try:
                dt = datetime.strptime(booked[:n], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            continue
        weekly.setdefault(week_tab_name(dt), []).append(r)
        monthly.setdefault(dt.strftime("%Y-%m"), []).append(r)

    lead_w, lead_m = _lead_period_counts(sh)
    now = datetime.now(LONDON)
    out = [["New Patient Bookings — Dashboard"],
           [f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"], []]
    hdr = ["Period", "Total IAs", "Brand New", "Past Patient",
           "Online", "Phone / Walk-in", "Leads (not booked)"]

    out.append(["BY WEEK (Sunday-Saturday)"])
    out.append(hdr)
    for tab in sorted(weekly, key=lambda t: datetime.strptime(t[4:], "%d %b %Y"),
                      reverse=True):
        b = bucket(weekly[tab])
        out.append([tab, b["total"], b["new"], b["past"], b["online"],
                    b["total"] - b["online"], lead_w.get(tab, 0)])
    out.append([])

    # One weekly section per clinic — new clinics appear automatically once
    # they are added to config.CLINIKO_BUSINESS_TO_CLINIC.
    chdr = ["Period", "Total IAs", "Brand New", "Past Patient"]
    weeks_desc = sorted(weekly, key=lambda t: datetime.strptime(t[4:], "%d %b %Y"),
                        reverse=True)
    for clinic in sorted(set(config.CLINIKO_BUSINESS_TO_CLINIC.values())):
        out.append([f"BY WEEK — {clinic.upper()}"])
        out.append(chdr)
        for tab in weeks_desc:
            crows = [r for r in weekly[tab] if str(r.get("Clinic")) == clinic]
            b = bucket(crows)
            out.append([tab, b["total"], b["new"], b["past"]])
        out.append([])

    out.append(["BY CALENDAR MONTH"])
    out.append(hdr)
    for mk in sorted(monthly, reverse=True):
        b = bucket(monthly[mk])
        label = datetime.strptime(mk + "-01", "%Y-%m-%d").strftime("%B %Y")
        out.append([label, b["total"], b["new"], b["past"], b["online"],
                    b["total"] - b["online"], lead_m.get(mk, 0)])

    try:
        ws = sh.worksheet(DASHBOARD_TAB)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=DASHBOARD_TAB, rows=200, cols=8)
    ws.update(values=out, range_name="A1", value_input_option="RAW")


# ---------------- Slack ----------------

def notify_reception(rows):
    """DM the reception Slack profile a summary of the bookings just logged."""
    if not rows:
        return
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("  WARN SLACK_BOT_TOKEN not set — skipping reception DM")
        return
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    lines = [f"🗓️ *{len(rows)} new patient booking(s) logged*"]
    for r in rows:
        bits = [r["patient"], r["clinic"], r["appointment_type"],
                f"appt {r['appointment_date'][:10]}"]
        if r["referrer"]:
            bits.append(f"ref: {r['referrer']}")
        lines.append("• " + " — ".join(bits))
    text = "\n".join(lines)

    client = WebClient(token=token)
    try:
        if config.SLACK_SAFE_MODE:
            uid = client.users_lookupByEmail(
                email=config.CEO_SLACK_EMAIL)["user"]["id"]
            text = "*[TEST → would have gone to the reception Slack profile]*\n\n" + text
        else:
            uid = config.BOOKINGS_SLACK_USER_ID
            if not uid:
                print("  WARN config.BOOKINGS_SLACK_USER_ID not set — skipping DM")
                return
        client.chat_postMessage(channel=uid, text=text, unfurl_links=False)
        print("  Slack DM sent to reception")
    except SlackApiError as e:
        print(f"  WARN Slack DM failed: {e.response.get('error')}")


# ---------------- output ----------------

def print_summary(rows):
    by_clinic, by_type, by_np = {}, {}, {}
    for r in rows:
        by_clinic[r["clinic"]] = by_clinic.get(r["clinic"], 0) + 1
        by_type[r["appointment_type"]] = by_type.get(r["appointment_type"], 0) + 1
        by_np[r["new_or_past"]] = by_np.get(r["new_or_past"], 0) + 1
    online = sum(1 for r in rows if r["booking_source"] == "Online")
    print(f"\nNew bookings: {len(rows)}  (New: {by_np.get('New',0)}  "
          f"Past: {by_np.get('Past',0)}  Online: {online})")
    print("  By clinic: " + ", ".join(f"{k}={v}" for k, v in sorted(by_clinic.items())))
    print("  By type:   " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))


def print_preview(rows):
    print_summary(rows)
    if not rows:
        return
    visible = [c for c in COLUMNS if c not in HIDDEN]
    widths = {c: max(len(HEADERS[c]), max(len(str(r[c])) for r in rows)) for c in visible}
    print()
    print("  " + "  ".join(HEADERS[c].ljust(widths[c]) for c in visible))
    print("  " + "  ".join("-" * widths[c] for c in visible))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(widths[c]) for c in visible))


# ---------------- main ----------------

def main():
    write_mode = "--write" in sys.argv
    summary_mode = "--summary" in sys.argv
    since_date = None
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            since_date = sys.argv[i + 1]

    skip_ids = set()
    if write_mode:
        try:
            skip_ids = existing_appointment_ids(open_spreadsheet())
        except Exception as e:
            print(f"  WARN couldn't read existing ids: {e}")

    rows = collect_bookings(skip_ids=skip_ids, since_date=since_date)

    if summary_mode:
        print_summary(rows)
    else:
        print_preview(rows)

    if not write_mode:
        print("\n(Preview only. Re-run with --write to update the Sheet.)")
        return

    print("\nWriting to the New Patient Bookings sheet…")
    sh = open_spreadsheet()
    ensure_leads_tab(sh)
    new_rows = write_to_sheet(sh, rows)
    print(f"  {len(new_rows)} new booking(s) written.")
    print("Refreshing Dashboard…")
    write_dashboard(sh)
    if new_rows:
        print("Notifying reception on Slack…")
        notify_reception(new_rows)
    print("Done.")


if __name__ == "__main__":
    main()
