"""Copy Meta (Facebook/Instagram) ad leads into the bookings sheet's Leads tab.

Meta's lead forms land in the "Online Leads" Google Sheet (one tab per clinic:
"Maghera Leads", "Omagh Leads"). Reception used to retype each one into the
New Patient Bookings sheet's Leads tab by hand. This runs on every bookings
poll (bookings_fetch.py --write, 6x/day) and appends any lead not already there.

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
  - Status on insert: booked / declined if the Online Leads sheet already says
    so, else pending. Afterwards a synced row's Status is only ever moved OFF
    pending/blank (to booked or declined) when the Online Leads sheet says so —
    a status reception sets on the Leads tab is never overwritten.
  - Meta test leads ("<test lead: …>") are skipped.

Run alone:  python meta_leads.py           (dry run — prints what it would do)
            python meta_leads.py --write
"""

import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread

import config

LONDON = ZoneInfo("Europe/London")

META_ID_HEADER = "Meta Lead ID"
REFERRER = "FB Ad"   # what reception already used for these leads

# Source tab -> clinic name on the Leads tab.
SOURCE_TABS = {"Maghera Leads": "Maghera", "Omagh Leads": "Omagh"}

# Standard Meta lead-form export columns. The clinic's own question (the
# "main issue" free text) is whatever other column sits before full_name.
_META_FIELDS = {"id", "created_time", "ad_id", "ad_name", "adset_id",
                "adset_name", "campaign_id", "campaign_name", "form_id",
                "form_name", "is_organic", "platform"}


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


def read_source_leads(gc):
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
            })
    leads.sort(key=lambda l: l["dt"] or datetime.max.replace(tzinfo=LONDON))
    return leads


def _start_date():
    return datetime.strptime(config.META_LEADS_START_DATE, "%Y-%m-%d").date()


def sync_meta_leads(sh, write=False, gc=None, retry=None):
    """Append new Meta leads to sh's Leads tab. Returns the list of leads added
    (or that would be added, in a dry run)."""
    import bookings_fetch
    retry = retry or (lambda call, label: call())
    gc = gc or gspread.authorize(bookings_fetch._sheets_credentials())
    source = retry(lambda: read_source_leads(gc), "read Online Leads")

    ws = retry(lambda: sh.worksheet(bookings_fetch.LEADS_TAB), "open Leads tab")
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
    phone_i = col.get("Callback Needed (by who)")
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
            "Clinic": lead["clinic"],
            "Body Area": lead["issue"],
            "Referrer": REFERRER,
            "Reason Not Booked": lead["notes"],
            "Callback Needed (by who)": lead["phone"],
            "Notes": "",
            "Status": lead["status"] or "pending",
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
    import bookings_fetch
    write = "--write" in sys.argv
    sync_meta_leads(bookings_fetch.open_spreadsheet(), write=write,
                    retry=bookings_fetch._gs_retry)
    if not write:
        print("\n(Dry run. Re-run with --write to update the Leads tab.)")
