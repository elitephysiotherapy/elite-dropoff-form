"""Meta Ads funnel — what the Facebook/Instagram leads turn into.

Builds the "Meta Ads" tab on the drop-off sheet (Martin 2026-10-14):
  Total leads → Leads booked → IAs performed → IAs rebooked → Total appointments
  → Revenue, by month, by clinic and by ad, plus one row per lead.

Leads come from the bookings sheet Leads tab (Lead Source == "Meta Ad"; the
hourly meta_leads.py sync fills it). Each lead is linked to a Cliniko patient:
  1. phone — any patient created since the campaigns began whose phone matches
     (last 10 digits), then a last-name search across ALL patients, phone-checked;
  2. name — ONLY a brand-new patient (created on/after the lead date) with the
     exact same full name, and only if exactly one. Never name-only against old
     records: same name ≠ same person (two Martin Mallons).
Unmatched leads still count as leads; they just can't progress past "booked".

Stage definitions (everything counted from the lead date onwards):
  Booked          — the patient has any appointment created on/after the lead
                    date (cancelled ones included: they did book).
  IA              — first appointment starting on/after the lead date whose type
                    is an IA type (else simply the first appointment).
  IA performed    — that IA attended (not cancelled, not DNA, started 2h+ ago).
  IA rebooked     — IA performed AND a later appointment that isn't cancelled/DNA
                    (same rule as the funnel / IA Rebook Rate tabs).
  Total appts     — attended 1-to-1 appointments since the lead date, IA included
                    (Pilates classes are group bookings — not counted).
  Revenue £       — the patient's Cliniko invoices issued on/after the lead date.
A patient who came in through two forms is counted once, on the earliest lead.

"Ad Spend £" on the monthly table is typed in by hand and kept across rebuilds;
cost per lead / per IA and return are sheet formulas off it.

Runs daily 06:30 (Render elite-meta-funnel -> run_cloud.py meta_funnel).
  python meta_funnel.py            preview
  python meta_funnel.py --write    rebuild the tab
"""

import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import gspread

import config
import phase2
import meta_leads
import sheets_retry

LONDON = ZoneInfo("Europe/London")
TAB = "Meta Ads"
DROPOFF_SPREADSHEET_ID = "1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI"
ATTENDED_BUFFER_H = 2
IA_TYPE_IDS = set(config.NEW_PATIENT_TYPE_IDS) | set(getattr(config, "PHASE1_DROPOFF_IA_TYPE_IDS", set()))
SPEND_HEADER = "Ad Spend £ (enter)"


def _retry(call, label):
    return sheets_retry.gs_retry(call, label, prefix="  ")


def _parse_date(raw):
    raw = str(raw or "").strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Leads
# --------------------------------------------------------------------------

def load_meta_leads(gc):
    sh = _retry(lambda: gc.open_by_key(config.BOOKINGS_SPREADSHEET_ID), "open bookings")
    recs = _retry(lambda: sh.worksheet(meta_leads.LEADS_TAB).get_all_records(), "read Leads")
    leads = []
    for r in recs:
        if str(r.get("Lead Source") or "").strip() != "Meta Ad":
            continue
        d = _parse_date(r.get("Date"))
        if not d:
            continue
        leads.append({
            "date": d, "name": str(r.get("Patient") or "").strip(),
            "phone": str(r.get("Phone") or "").strip(),
            "clinic": str(r.get("Preferred Clinic") or "").strip() or "(not set)",
            "ad": str(r.get("Ad / Campaign") or "").strip() or "(unknown ad)",
            "tab_status": str(r.get("Status") or "").strip().lower(),
        })
    leads.sort(key=lambda l: l["date"])
    return leads


# --------------------------------------------------------------------------
# Cliniko matching
# --------------------------------------------------------------------------

def _phones(p):
    return {meta_leads._norm_phone(n.get("number"))
            for n in p.get("patient_phone_numbers") or []} - {""}


def _full_name(p):
    return meta_leads._norm_name(f"{p.get('first_name', '')} {p.get('last_name', '')}")


def match_patients(leads):
    """Sets lead['patient'] (Cliniko patient dict or None) and lead['match']."""
    since = min(l["date"] for l in leads) - timedelta(days=7)
    recent = list(phase2.fetch_all("/patients", [("q[]", f"created_at:>={since.isoformat()}T00:00:00Z")]))
    by_phone = {}
    for p in recent:
        for ph in _phones(p):
            by_phone.setdefault(ph, p)
    for l in leads:
        ph = meta_leads._norm_phone(l["phone"])
        p = by_phone.get(ph) if ph else None
        how = "phone" if p else ""
        if not p and ph:
            parts = l["name"].split()
            if len(parts) >= 2:
                last = re.sub(r"[^\w' -]", "", parts[-1])
                for c in phase2.fetch_all("/patients", [("q[]", f"last_name:~{last}")]):
                    if ph in _phones(c):
                        p, how = c, "phone"
                        break
        if not p:
            n = meta_leads._norm_name(l["name"])
            new_same = [c for c in recent if _full_name(c) == n
                        and (c.get("created_at") or "")[:10] >= l["date"].isoformat()]
            if n and len(new_same) == 1:
                p, how = new_same[0], "name (new patient)"
        l["patient"], l["match"] = p, how or "not found"


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------

def _attended(a, now):
    if a.get("cancelled_at") or a.get("did_not_arrive"):
        return False
    s = phase2.parse_iso(a.get("starts_at"))
    return bool(s) and s <= now - timedelta(hours=ATTENDED_BUFFER_H)


def _outcome(a, now):
    if a.get("cancelled_at"):
        return "Cancelled"
    if a.get("did_not_arrive"):
        return "DNA"
    return "Attended" if _attended(a, now) else "Upcoming"


def _type_id(a):
    return phase2.id_from_link(a.get("appointment_type"))


def score_lead(l, now, seen_patients):
    l.update(booked=False, ia_date="", ia_outcome="", performed=False,
             rebooked=False, appts=0, upcoming=0, revenue=0.0, existing=False,
             duplicate=False)
    p = l["patient"]
    if not p:
        return
    pid = str(p["id"])
    if pid in seen_patients:
        l["duplicate"] = True        # second form from the same person
        return
    seen_patients.add(pid)
    l["existing"] = (p.get("created_at") or "")[:10] < l["date"].isoformat()
    start_iso = datetime.combine(l["date"], datetime.min.time(), LONDON).astimezone(timezone.utc).isoformat()
    hist = phase2.fetch_patient_full_history(pid)
    since = [a for a in hist if (a.get("created_at") or "") >= start_iso[:10]]
    l["booked"] = bool(since)
    after = [a for a in hist if (a.get("starts_at") or "") >= start_iso[:10]]
    if not after:
        return
    ia = next((a for a in after if _type_id(a) in IA_TYPE_IDS), after[0])
    l["ia_date"] = (phase2.parse_iso(ia["starts_at"]).astimezone(LONDON).strftime("%d/%m/%y"))
    l["ia_outcome"] = _outcome(ia, now)
    l["performed"] = l["ia_outcome"] == "Attended"
    l["rebooked"] = l["performed"] and any(
        (a.get("starts_at") or "") > ia["starts_at"]
        and not a.get("cancelled_at") and not a.get("did_not_arrive") for a in after)
    l["appts"] = sum(1 for a in after if _attended(a, now))
    l["upcoming"] = sum(1 for a in after if _outcome(a, now) == "Upcoming")
    for inv in phase2.fetch_all("/invoices", [("q[]", f"patient_id:={pid}")]):
        if inv.get("archived_at") or inv.get("deleted_at"):
            continue
        if (inv.get("issue_date") or "")[:10] >= l["date"].isoformat():
            l["revenue"] += float(inv.get("total_amount") or 0)


# --------------------------------------------------------------------------
# Sheet
# --------------------------------------------------------------------------

STAGE_HDR = ["Total Leads", "Leads Booked", "Book %", "IAs Performed", "IA %",
             "IAs Rebooked", "Rebook %", "Total Appointments", "Upcoming Appts",
             "Revenue £", "Revenue / Lead £"]


def _pct(n, d):
    return f"{round(100 * n / d)}%" if d else "—"


def _stage_row(label, ls):
    ls = [l for l in ls if not l["duplicate"]]
    n = len(ls)
    b = sum(l["booked"] for l in ls)
    perf = sum(l["performed"] for l in ls)
    reb = sum(l["rebooked"] for l in ls)
    rev = round(sum(l["revenue"] for l in ls), 2)
    return [label, n, b, _pct(b, n), perf, _pct(perf, b), reb, _pct(reb, perf),
            sum(l["appts"] for l in ls), sum(l["upcoming"] for l in ls),
            rev, round(rev / n, 2) if n else 0]


def _old_spend(ws):
    """{month label: spend} typed into the existing tab — kept across rebuilds."""
    try:
        vals = ws.get_all_values()
    except Exception:
        return {}
    out, col = {}, None
    for row in vals:
        if SPEND_HEADER in row:
            col = row.index(SPEND_HEADER)
            continue
        if col is not None:
            if not row or not row[0].strip():
                break
            if col < len(row) and row[col].strip():
                out[row[0].strip()] = row[col].strip()
    return out


def build_rows(leads, now, spend):
    months = defaultdict(list)
    for l in leads:
        months[l["date"].strftime("%B %Y")].append(l)
    month_keys = sorted(months, key=lambda m: datetime.strptime(m, "%B %Y"), reverse=True)

    out = [["Meta Ads — Lead Value Tracker"],
           [f"Last updated: {now.astimezone(LONDON):%Y-%m-%d %H:%M}  ·  "
            f"{len(leads)} Meta leads since {leads[0]['date']:%d %b %Y}  ·  "
            f"{sum(1 for l in leads if l['patient'])} found in Cliniko"],
           [], ["BY MONTH (month the lead came in)"]]
    hdr = ["Month"] + STAGE_HDR + [SPEND_HEADER, "Cost / Lead £", "Cost / IA £",
                                   "Return (Revenue ÷ Spend)"]
    out.append(hdr)
    first = len(out) + 1
    for m in month_keys + ["TOTAL"]:
        row = _stage_row(m, leads if m == "TOTAL" else months[m])
        r = len(out) + 1
        if m == "TOTAL":
            sp = f"=IF(COUNT(M{first}:M{r - 1}),SUM(M{first}:M{r - 1}),\"\")"
        else:
            sp = spend.get(m, "")
        row += [sp,
                f'=IF(OR(M{r}="",B{r}=0),"",ROUND(M{r}/B{r},2))',
                f'=IF(OR(M{r}="",E{r}=0),"",ROUND(M{r}/E{r},2))',
                f'=IF(OR(M{r}="",M{r}=0),"",TEXT(K{r}/M{r},"0.0")&"x")']
        out.append(row)
    spend_rows = (first, len(out))   # 1-based, inclusive
    out.append([])

    for title, key in (("BY CLINIC (Preferred Clinic)", "clinic"), ("BY AD (campaign — ad)", "ad")):
        out.append([title])
        out.append([title.split(" (")[0].replace("BY ", "").title()] + STAGE_HDR)
        groups = defaultdict(list)
        for l in leads:
            groups[l[key]].append(l)
        for g in sorted(groups, key=lambda g: -len(groups[g])):
            out.append(_stage_row(g, groups[g]))
        out.append([])

    out.append(["LEAD DETAIL (newest first)"])
    out.append(["Lead Date", "Patient", "Clinic", "Ad / Campaign", "Leads Tab Status",
                "Cliniko Match", "Booked", "IA Date", "IA Outcome", "Rebooked",
                "Appts Attended", "Upcoming", "Revenue £", "Note"])
    yn = lambda b: "Yes" if b else "No"
    for l in reversed(leads):
        note = []
        if l["duplicate"]:
            note.append("same person as an earlier lead — counted once")
        if l["existing"]:
            note.append("existing patient (record pre-dates the lead)")
        if l["tab_status"] == "booked" and not l["booked"]:
            note.append("Leads tab says booked but no booking found in Cliniko — check name/phone")
        out.append([l["date"].strftime("%d/%m/%y"), l["name"], l["clinic"], l["ad"],
                    l["tab_status"], l["match"], yn(l["booked"]), l["ia_date"],
                    l["ia_outcome"], yn(l["rebooked"]), l["appts"], l["upcoming"],
                    round(l["revenue"], 2), "; ".join(note)])
    out += [[], ["Definitions:"],
            ["  Leads = rows on the bookings sheet Leads tab with Lead Source 'Meta Ad'. Linked to Cliniko by phone (or exact name for a brand-new patient)."],
            ["  Booked = any appointment made on/after the lead date. IA % = IAs performed ÷ booked. Rebook % = rebooked ÷ IAs performed."],
            ["  IA performed = first IA after the lead, attended. Rebooked = a later appointment that wasn't cancelled/DNA."],
            ["  Total Appointments = attended 1-to-1 appointments since the lead (Pilates classes not included). Revenue = Cliniko invoices since the lead date."],
            ["  Type monthly Meta spend into the Ad Spend column — it is kept when the tab refreshes, and the cost/return columns fill in."]]
    return out, spend_rows


def main():
    write = "--write" in sys.argv
    now = datetime.now(timezone.utc)
    gc = gspread.authorize(meta_leads._credentials())
    leads = load_meta_leads(gc)
    print(f"{len(leads)} Meta leads")
    match_patients(leads)
    seen = set()
    for l in leads:
        score_lead(l, now, seen)

    sh = _retry(lambda: gc.open_by_key(DROPOFF_SPREADSHEET_ID), "open drop-off sheet")
    try:
        ws = _retry(lambda: sh.worksheet(TAB), "open tab")
        spend = _old_spend(ws)
    except gspread.exceptions.WorksheetNotFound:
        ws, spend = None, {}
    rows, spend_rows = build_rows(leads, now, spend)
    for r in rows[:12]:
        print(r[:13])
    print(f"... {len(rows)} rows; spend kept: {spend}")
    if not write:
        print("\n(Preview only. Re-run with --write to rebuild the tab.)")
        return

    width = max(len(r) for r in rows)
    if ws is None:
        ws = _retry(lambda: sh.add_worksheet(title=TAB, rows=len(rows) + 50, cols=width), "create tab")
    if ws.col_count < width:
        _retry(lambda: ws.add_cols(width - ws.col_count), "widen")
    if ws.row_count < len(rows) + 10:
        _retry(lambda: ws.add_rows(len(rows) + 10 - ws.row_count), "lengthen")
    _retry(lambda: ws.clear(), "clear")
    _retry(lambda: ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED"), "write")
    # Bold the titles / header rows.
    bold = [i + 1 for i, r in enumerate(rows)
            if r and (str(r[0]).isupper() or r[0] in ("Month", "Clinic", "Ad", "Lead Date")
                      or str(r[0]).startswith(("BY ", "LEAD DETAIL", "Meta Ads")))]
    reqs = [{"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": r - 1, "endRowIndex": r},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold"}} for r in bold]
    spend_col = rows[4].index(SPEND_HEADER)
    reqs.append({"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": spend_rows[0] - 1,
                 "endRowIndex": spend_rows[1] - 1,
                 "startColumnIndex": spend_col, "endColumnIndex": spend_col + 1},
                 "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 0.95, "blue": 0.8}}},
                 "fields": "userEnteredFormat.backgroundColor"}})
    _retry(lambda: sh.batch_update({"requests": reqs}), "format")
    print("Meta Ads tab written.")


if __name__ == "__main__":
    main()
