"""Copy Meta (Facebook/Instagram) ad leads into the bookings sheet's Leads tab.

SINCE 16 NOV 2026 leads are read DIRECT from Meta's lead forms (read_api_leads,
META_LEADS_TOKEN) — every form on the Page, so new campaigns/forms need no setup.
Each lead is also saved to the "Meta Leads (raw)" tab on the bookings sheet,
because Meta deletes leads after 90 days. The Online Leads sheet described below
is only a cross-check/fallback until config.META_LEADS_SHEET_CROSSCHECK_UNTIL.

Meta's lead forms land in the "Online Leads" Google Sheet (one tab per clinic:
"Maghera Leads", "Omagh Leads"). Reception used to retype each one into the
New Patient Bookings sheet's Leads tab by hand. This runs hourly 07:00–21:00
Mon–Fri (Render cron elite-meta-leads -> run_cloud.py meta_leads) and appends
any lead not already there.

Rules
  - A lead is keyed by its Meta lead id (column "id", e.g. "l:1117…"), stored
    in the Leads tab's "Meta Lead ID" column. Rows without an id (typed in by
    hand on the Online Leads sheet) key on their phone number.
  - Already on the Leads tab = same Meta Lead ID, OR same phone (last 10 digits),
    OR same name. That catches the leads reception had already copied by hand,
    so nothing is duplicated; on --write the match gets the id stamped on it.
  - Leads created before META_LEADS_START_DATE are ignored — older ones were
    handled before this sync existed, and adding them would restate past weeks'
    "Leads not booked" on the Dashboard and EOD.
  - Each lead lands with Lead Source "Meta Ad", Preferred Clinic = the
    clinic whose campaign it came through (Maghera/Omagh tab), Referrer =
    Facebook/Instagram, and the campaign + ad name in "Ad / Campaign".
  - Status on insert: booked / declined if the Online Leads sheet already says
    so, else pending. Afterwards a synced row's Status is only ever moved OFF
    pending/blank (to booked or declined) when the Online Leads sheet says so —
    a status reception sets on the Leads tab is never overwritten.
  - Meta test leads ("<test lead: …>") are skipped.

Run alone:  python meta_leads.py           (dry run — prints what it would do)
            python meta_leads.py --write
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gspread
import net_timeouts  # noqa: F401  (hard Sheets timeouts — see module)
from google.oauth2.service_account import Credentials

import config
import sheets_retry

# Self-contained on purpose: importing bookings_fetch pulls in phase2, which
# needs the Cliniko API key just to import — this job only needs Google.
LEADS_TAB = "Leads"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "service_account.json")

LONDON = ZoneInfo("Europe/London")

META_ID_HEADER = "Meta Lead ID"
LEAD_SOURCE = "Meta Ad"   # Lead Source dropdown value for these leads

# Source tab -> clinic name on the Leads tab.
SOURCE_TABS = {"Maghera Leads": "Maghera", "Omagh Leads": "Omagh"}

# Standard Meta lead-form export columns. The clinic's own question (the
# "main issue" free text) is whatever other column sits before full_name.
_META_FIELDS = {"id", "created_time", "ad_id", "ad_name", "adset_id",
                "adset_name", "campaign_id", "campaign_name", "form_id",
                "form_name", "is_organic", "platform"}


def _credentials():
    """SERVICE_ACCOUNT_JSON env var on Render; local file on the Mac."""
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)


def _retry(call, label):
    return sheets_retry.gs_retry(call, label, prefix="  ")


def _norm_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def _norm_name(raw):
    return re.sub(r"\s+", " ", str(raw or "")).strip().lower()


def _source_status(cells):
    """booked / declined if any status-ish cell on the source row says so."""
    text = " ".join(c.lower() for c in cells)
    if "booked" in text:
        return "booked"
    if "declined" in text:
        return "declined"
    return ""


def read_sheet_leads(gc):
    """Every real lead on the Online Leads sheet, oldest first."""
    sh = gc.open_by_key(config.META_LEADS_SPREADSHEET_ID)
    ranges = [f"'{t}'" for t in SOURCE_TABS]
    batch = sh.values_batch_get(ranges)
    leads = []
    for tab, vr in zip(SOURCE_TABS, batch.get("valueRanges", [])):
        vals = vr.get("values") or []
        if not vals:
            continue
        hdr = [h.strip() for h in vals[0]]
        col = {h.lower(): i for i, h in enumerate(hdr) if h}
        name_i = col.get("full_name")
        if name_i is None:
            print(f"  WARN {tab}: no full_name column — skipped")
            continue
        issue_i = next((i for i, h in enumerate(hdr[:name_i])
                        if h and h.lower() not in _META_FIELDS), None)
        notes_i = col.get("admin notes")
        # Status lives in the unlabelled column after phone_number and/or
        # lead_status — the two tabs use different ones, so read both.
        status_is = [i for i, h in enumerate(hdr)
                     if i > name_i and (not h or h.lower() == "lead_status")]

        def cell(row, i):
            return row[i].strip() if i is not None and i < len(row) else ""

        for row in vals[1:]:
            name = cell(row, name_i)
            phone = cell(row, col.get("phone_number"))
            if not name or "<test lead" in name.lower():
                continue
            lead_id = cell(row, col.get("id"))
            key = lead_id or (f"phone:{_norm_phone(phone)}" if _norm_phone(phone) else "")
            if not key:
                continue
            created = cell(row, col.get("created_time"))
            try:
                dt = datetime.fromisoformat(created).astimezone(LONDON)
            except ValueError:
                dt = None
            leads.append({
                "key": key, "tab": tab, "clinic": SOURCE_TABS[tab], "dt": dt,
                "name": name, "phone": re.sub(r"^p:\s*", "", phone, flags=re.I),
                "issue": cell(row, issue_i), "notes": cell(row, notes_i),
                "status": _source_status([cell(row, i) for i in status_is]),
                "platform": {"fb": "Facebook", "ig": "Instagram"}.get(
                    cell(row, col.get("platform")).lower(), ""),
                "ad_id": re.sub(r"^ag:", "", cell(row, col.get("ad_id"))),
                "campaign_id": re.sub(r"^c:", "", cell(row, col.get("campaign_id"))),
                "ad": " — ".join(x for x in (cell(row, col.get("campaign_name")),
                                             cell(row, col.get("ad_name"))) if x),
            })
    leads.sort(key=lambda l: l["dt"] or datetime.max.replace(tzinfo=LONDON))
    return leads


def _start_date():
    return datetime.strptime(config.META_LEADS_START_DATE, "%Y-%m-%d").date()


# --------------------------------------------------------------------------
# Direct from Meta (since 16 Nov 2026)
# --------------------------------------------------------------------------

GRAPH = "https://graph.facebook.com/v23.0"
_CLINICS = ("Cookstown", "Maghera", "Omagh")
_STD_FIELDS = {"full_name", "first_name", "last_name", "phone_number", "email",
               "city", "post_code", "zip_code", "date_of_birth"}
_forms_cache = {"at": 0.0, "forms": []}


def _graph(path, token, params=None):
    q = dict(params or {}, access_token=token)
    url = f"{GRAPH}/{path}?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message")
        except ValueError:
            msg = str(e)
        raise RuntimeError(f"Meta API {path.split('?')[0]}: {msg}") from None


def _lead_forms(token):
    """Every lead form on the Page (cached 30 min). Listing forms needs a Page
    token, which the system-user token hands out via me/accounts."""
    if _forms_cache["forms"] and time.time() - _forms_cache["at"] < 1800:
        return _forms_cache["forms"]
    pages = _graph("me/accounts", token, {"fields": "id,access_token"}).get("data", [])
    page = next((p for p in pages if p["id"] == config.META_PAGE_ID), None)
    if not page:
        raise RuntimeError("Meta API: token has no access to the Elite Physiotherapy Page")
    forms, after = [], None
    while True:
        q = {"fields": "id,name,status", "limit": 100}
        if after:
            q["after"] = after
        r = _graph(f"{config.META_PAGE_ID}/leadgen_forms", page["access_token"], q)
        forms += r.get("data", [])
        after = r.get("paging", {}).get("cursors", {}).get("after") if r.get("paging", {}).get("next") else None
        if not after:
            break
    _forms_cache.update(at=time.time(), forms=forms)
    return forms


def _clinic_for(campaign, form_id, form_name):
    for text in (campaign,):
        for c in _CLINICS:
            if c.lower() in (text or "").lower():
                return c
    if form_id in config.META_FORM_CLINIC:
        return config.META_FORM_CLINIC[form_id]
    for c in _CLINICS:
        if c.lower() in (form_name or "").lower():
            return c
    print(f"  WARN lead form {form_id} ({form_name}) / campaign '{campaign}' has no "
          f"clinic — using {config.META_LEADS_DEFAULT_CLINIC}. Put the clinic name in "
          f"the campaign name, or add the form to config.META_FORM_CLINIC.")
    return config.META_LEADS_DEFAULT_CLINIC


def read_api_leads(since=None):
    """Every lead on every lead form on the Page since `since` (a date; default
    META_LEADS_START_DATE), oldest first — same shape as read_sheet_leads."""
    token = os.environ.get("META_LEADS_TOKEN")
    if not token:
        raise RuntimeError("META_LEADS_TOKEN not set")
    since = since or _start_date()
    ts = int(datetime.combine(since, datetime.min.time(), LONDON).timestamp())
    fields = ("id,created_time,ad_id,ad_name,adset_name,campaign_id,campaign_name,"
              "form_id,is_organic,platform,field_data")
    leads = []
    for form in _lead_forms(token):
        after = None
        while True:
            q = {"fields": fields, "limit": 500,
                 "filtering": json.dumps([{"field": "time_created",
                                           "operator": "GREATER_THAN", "value": ts}])}
            if after:
                q["after"] = after
            r = _graph(f"{form['id']}/leads", token, q)
            for d in r.get("data", []):
                fd = {f["name"]: (f.get("values") or [""])[0] for f in d.get("field_data", [])}
                name = (fd.get("full_name") or " ".join(
                    x for x in (fd.get("first_name"), fd.get("last_name")) if x)).strip()
                if not name or "<test lead" in name.lower() or "dummy data" in name.lower():
                    continue
                issue = next((v for k, v in fd.items() if k not in _STD_FIELDS), "")
                campaign = d.get("campaign_name", "")
                leads.append({
                    "key": f"l:{d['id']}", "tab": f"form {form['id']}",
                    "clinic": _clinic_for(campaign, form["id"], form.get("name")),
                    "dt": datetime.fromisoformat(d["created_time"].replace("+0000", "+00:00")).astimezone(LONDON),
                    "name": name, "phone": fd.get("phone_number", ""),
                    "issue": issue, "notes": "", "status": "",
                    "platform": {"fb": "Facebook", "ig": "Instagram"}.get(
                        (d.get("platform") or "").lower(), ""),
                    "ad_id": d.get("ad_id", ""), "campaign_id": d.get("campaign_id", ""),
                    "ad": " — ".join(x for x in (campaign, d.get("ad_name", "")) if x)
                          or ("Organic (Page form)" if d.get("is_organic") else ""),
                })
            nxt = r.get("paging", {})
            after = nxt.get("cursors", {}).get("after") if nxt.get("next") else None
            if not after:
                break
    leads.sort(key=lambda l: l["dt"])
    return leads


ARCHIVE_TAB = "Meta Leads (raw)"
ARCHIVE_COLS = ["Meta Lead ID", "Created", "Clinic", "Name", "Phone", "Issue",
                "Platform", "Campaign / Ad", "Ad ID", "Campaign ID", "Form ID"]


def read_archive(sh):
    """Every lead ever pulled from Meta (Meta deletes them after 90 days)."""
    try:
        ws = sh.worksheet(ARCHIVE_TAB)
    except gspread.WorksheetNotFound:
        return []
    out = []
    for r in ws.get_all_records(expected_headers=ARCHIVE_COLS):
        try:
            dt = datetime.fromisoformat(str(r["Created"]))
        except ValueError:
            dt = None
        out.append({"key": str(r["Meta Lead ID"]), "tab": "archive", "clinic": r["Clinic"],
                    "dt": dt, "name": r["Name"], "phone": str(r["Phone"]),
                    "issue": r["Issue"], "notes": "", "status": "",
                    "platform": r["Platform"], "ad_id": str(r["Ad ID"]),
                    "campaign_id": str(r["Campaign ID"]), "ad": r["Campaign / Ad"]})
    return out


def archive_leads(sh, leads, retry=None):
    """Append any Meta-API lead not yet on the archive tab."""
    retry = retry or _retry
    try:
        ws = sh.worksheet(ARCHIVE_TAB)
    except gspread.WorksheetNotFound:
        ws = retry(lambda: sh.add_worksheet(ARCHIVE_TAB, rows=1000, cols=len(ARCHIVE_COLS)),
                   "add archive tab")
        retry(lambda: ws.update([ARCHIVE_COLS], "A1"), "archive header")
    have = set(retry(lambda: ws.col_values(1), "read archive ids"))
    new = [[l["key"], l["dt"].isoformat(), l["clinic"], l["name"], l["phone"], l["issue"],
            l["platform"], l["ad"], l["ad_id"], l["campaign_id"], l["tab"].replace("form ", "")]
           for l in leads if l["tab"].startswith("form ") and l["key"] not in have]
    if new:
        retry(lambda: ws.append_rows(new, value_input_option="RAW", table_range="A1"),
              "append archive")
    print(f"  archive: {len(new)} new lead(s) saved to '{ARCHIVE_TAB}'")
    return len(new)


def _crosscheck_active():
    return date.today() <= datetime.strptime(
        config.META_LEADS_SHEET_CROSSCHECK_UNTIL, "%Y-%m-%d").date()


def read_source_leads(gc=None, since=None, archive_sh=None):
    """All Meta leads, oldest first. Direct from Meta, plus (when archive_sh is
    given) the archive tab for leads Meta has already deleted (90-day limit).
    During the cross-check window the Online Leads sheet is merged in too
    (hand-typed rows, anything the API missed, booked/declined statuses) and
    used as a fallback if Meta fails."""
    try:
        leads = read_api_leads(since)
        if archive_sh is not None:
            have = {l["key"] for l in leads}
            leads += [a for a in _retry(lambda: read_archive(archive_sh), "read archive")
                      if a["key"] not in have]
    except Exception as e:
        if not _crosscheck_active() or gc is None:
            raise
        print(f"  WARN Meta API failed ({e}) — falling back to the Online Leads sheet")
        return read_sheet_leads(gc)
    if not _crosscheck_active() or gc is None:
        return leads
    try:
        sheet = read_sheet_leads(gc)
    except Exception as e:  # cross-check only — never block on Julie's sheet
        print(f"  WARN Online Leads cross-check skipped ({e})")
        return leads
    by_key = {l["key"]: l for l in leads}
    extra = []
    for s in sheet:
        a = by_key.get(s["key"])
        if a:
            a["status"] = a["status"] or s["status"]
            a["notes"] = a["notes"] or s["notes"]
        elif s["key"].startswith("l:") and s["dt"] and s["dt"].date() >= (since or _start_date()):
            print(f"  WARN on Online Leads sheet but NOT from Meta API: {s['key']} {s['name']}")
            extra.append(s)
        elif not s["key"].startswith("l:"):
            extra.append(s)   # hand-typed row on the sheet
    out = leads + extra
    out.sort(key=lambda l: l["dt"] or datetime.max.replace(tzinfo=LONDON))
    return out


def sync_meta_leads(sh, write=False, gc=None, retry=None):
    """Append new Meta leads to sh's Leads tab. Returns the list of leads added
    (or that would be added, in a dry run)."""
    retry = retry or _retry
    gc = gc or gspread.authorize(_credentials())
    source = read_source_leads(gc, archive_sh=sh)
    if write:
        archive_leads(sh, source, retry)

    ws = retry(lambda: sh.worksheet(LEADS_TAB), "open Leads tab")
    vals = retry(lambda: ws.get_all_values(), "read Leads tab")
    hdr = vals[0] if vals else []
    col = {h.strip(): i for i, h in enumerate(hdr)}
    for need in ("Date", "Patient", "Status"):
        if need not in col:
            raise RuntimeError(f"Leads tab has no '{need}' column — not syncing")

    # Make sure the id column exists (appended after the last header).
    id_i = col.get(META_ID_HEADER)
    if id_i is None:
        id_i = len(hdr)
        if write:
            if ws.col_count < id_i + 1:
                retry(lambda: ws.add_cols(id_i + 1 - ws.col_count), "add id col")
            retry(lambda: ws.update_cell(1, id_i + 1, META_ID_HEADER), "id header")

    by_id, by_phone, by_name = {}, {}, {}
    # "Phone" since the 14 Oct 2026 restructure; phones used to be typed
    # into "Callback Needed (by who)".
    phone_i = col.get("Phone", col.get("Callback Needed (by who)"))
    for r, row in enumerate(vals[1:], start=2):
        get = lambda i: row[i] if i is not None and i < len(row) else ""
        if get(id_i):
            by_id[get(id_i).strip()] = r
        p = _norm_phone(get(phone_i))
        if p:
            by_phone.setdefault(p, r)
        n = _norm_name(get(col["Patient"]))
        if n:
            by_name.setdefault(n, r)

    start = _start_date()
    headers = [h.strip() for h in hdr] + ([META_ID_HEADER] if META_ID_HEADER not in col else [])
    to_add, stamps, status_moves = [], [], []
    seen_phones, seen_names = set(), set()
    for lead in source:
        r = by_id.get(lead["key"])
        if r:
            cur = (vals[r - 1][col["Status"]] if col["Status"] < len(vals[r - 1]) else "").strip().lower()
            if lead["status"] and cur in ("", "pending") and lead["status"] != cur:
                status_moves.append((r, lead))
            continue
        r = by_phone.get(_norm_phone(lead["phone"])) or by_name.get(_norm_name(lead["name"]))
        if r:
            # Reception already copied it by hand — claim the row, unless another
            # lead (same person, second form) already has.
            if not (id_i < len(vals[r - 1]) and vals[r - 1][id_i].strip()):
                stamps.append((r, lead))
            continue
        if lead["dt"] is None or lead["dt"].date() < start:
            continue
        # Same person filling in two forms (e.g. Maghera AND Omagh) = one lead.
        p, n = _norm_phone(lead["phone"]), _norm_name(lead["name"])
        if (p and p in seen_phones) or (n and n in seen_names):
            continue
        seen_phones.add(p)
        seen_names.add(n)
        values = {
            "Date": lead["dt"].strftime("%d/%m/%y"),
            "Patient": lead["name"],
            "Phone": lead["phone"],
            "Preferred Clinic": lead["clinic"],
            "Lead Source": LEAD_SOURCE,
            "Referrer": lead["platform"],
            "Body Area": lead["issue"],
            "Status": lead["status"] or "pending",
            "Reason Not Booked": lead["notes"],
            "Ad / Campaign": lead["ad"],
            META_ID_HEADER: lead["key"],
        }
        to_add.append((lead, [values.get(h, "") for h in headers]))

    for lead, _ in to_add:
        print(f"  + {lead['dt']:%d/%m/%y} {lead['clinic']:<8} {lead['name']} "
              f"({lead['status'] or 'pending'})")
    for r, lead in status_moves:
        print(f"  ~ row {r} {lead['name']}: status -> {lead['status']}")
    print(f"  {len(to_add)} new lead(s), {len(stamps)} already on the tab "
          f"(by phone/name), {len(status_moves)} status update(s).")

    if not write:
        return [l for l, _ in to_add]

    col_letter = lambda i: gspread.utils.rowcol_to_a1(1, i + 1)[:-1]
    updates = [{"range": f"{col_letter(id_i)}{r}", "values": [[lead["key"]]]}
               for r, lead in stamps]
    updates += [{"range": f"{col_letter(col['Status'])}{r}", "values": [[lead["status"]]]}
                for r, lead in status_moves]
    if updates:
        retry(lambda: ws.batch_update(updates, value_input_option="RAW"), "stamp leads")
    if to_add:
        # RAW so Sheets keeps "+44…" phones and dd/mm/yy dates as typed text,
        # same as reception's rows (the date parsers read text).
        retry(lambda: ws.append_rows([v for _, v in to_add],
                                     value_input_option="RAW",
                                     table_range="A1"), "append leads")
    return [l for l, _ in to_add]


if __name__ == "__main__":
    write = "--write" in sys.argv
    gc = gspread.authorize(_credentials())
    sh = _retry(lambda: gc.open_by_key(config.BOOKINGS_SPREADSHEET_ID), "open bookings sheet")
    sync_meta_leads(sh, write=write, gc=gc)
    if not write:
        print("\n(Dry run. Re-run with --write to update the Leads tab.)")
