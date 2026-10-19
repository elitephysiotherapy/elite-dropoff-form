"""Meta Ads funnel — what the Facebook/Instagram leads turn into.

Builds the "Meta Ads" tab on the drop-off sheet (Martin 2026-10-14):
  Total leads → Leads booked → IAs performed → IAs rebooked → Total appointments
  → Revenue, by month, by clinic and by ad, plus one row per lead.

Leads come from the bookings sheet Leads tab (Lead Source == "Meta Ad"; the
hourly meta_leads.py sync fills it). Each lead is linked to a Cliniko patient:
  1. phone — any patient created since the campaigns began whose phone matches
     (last 10 digits), then a last-name search across ALL patients, phone-checked;
  0. manual — config.META_LEAD_PATIENT_LINKS (hand-confirmed by Martin);
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

Meta spend is pulled live from the Marketing API (META_ACCESS_TOKEN, read-only
System User token) — by month, clinic and ad — giving cost per lead / booking /
IA and return. No token or an API failure -> spend columns blank, rest unchanged.

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
IA_TYPE_IDS = set(config.COUNTED_IA_TYPE_IDS)


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
            "meta_id": str(r.get("Meta Lead ID") or "").strip(),
        })
    ids = {l["key"]: l for l in meta_leads.read_source_leads(gc)}
    for l in leads:
        src = ids.get(l["meta_id"], {})
        l["ad_id"], l["campaign_id"] = src.get("ad_id", ""), src.get("campaign_id", "")
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
    links = getattr(config, "META_LEAD_PATIENT_LINKS", {})
    for l in leads:
        if l["meta_id"] in links:
            p = next(iter(phase2.fetch_all("/patients", [("q[]", f"id:={links[l['meta_id']]}")])), None)
            if p:
                l["patient"], l["match"] = p, "manual link"
                continue
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
SPEND_HDR = ["Meta Spend £", "Meta-Reported Leads", "Cost / Lead £",
             "Cost / Booking £", "Cost / IA £", "Return (Revenue ÷ Spend)"]


# --------------------------------------------------------------------------
# Meta spend (Marketing API, read-only ads_read token — System User
# "EliteReporting" in the Elite Physiotherapy business, set up 2026-10-15)
# --------------------------------------------------------------------------

GRAPH = "https://graph.facebook.com/v23.0"


def fetch_meta_spend(since):
    """Daily spend + Meta-counted leads per ad since `since` (date), or None
    when there's no token / the API fails (the tab then shows no spend)."""
    import json
    import os
    import urllib.parse
    import urllib.request
    token = os.environ.get("META_ACCESS_TOKEN")
    acct = os.environ.get("META_AD_ACCOUNT_ID", "164830011")
    if not token:
        print("  WARN no META_ACCESS_TOKEN — spend columns left blank")
        return None
    q = {"level": "ad", "time_increment": 1, "limit": 500,
         "time_range": json.dumps({"since": since.isoformat(),
                                   "until": datetime.now(LONDON).date().isoformat()}),
         "fields": "date_start,campaign_id,campaign_name,ad_id,ad_name,spend,actions",
         "access_token": token}
    url = f"{GRAPH}/act_{acct}/insights?" + urllib.parse.urlencode(q)
    rows = []
    try:
        while url:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = json.load(r)
            rows += data.get("data", [])
            url = (data.get("paging") or {}).get("next")
    except Exception as e:      # never let Meta break the Cliniko side of the tab
        msg = getattr(e, "read", lambda: b"")()[:200]
        print(f"  WARN Meta spend fetch failed: {e} {msg}")
        return None
    out = []
    for x in rows:
        leads = sum(int(a.get("value", 0)) for a in x.get("actions", [])
                    if a.get("action_type") == "lead")
        out.append({"date": datetime.strptime(x["date_start"], "%Y-%m-%d").date(),
                    "campaign_id": x["campaign_id"], "campaign": x["campaign_name"],
                    "ad_id": x["ad_id"], "ad": x["ad_name"],
                    "spend": float(x.get("spend") or 0), "meta_leads": leads})
    print(f"  Meta: {len(out)} ad-days, £{sum(r['spend'] for r in out):.2f} spend")
    return out


def _pct(n, d):
    return f"{round(100 * n / d)}%" if d else "—"


def _money(n, d):
    return round(n / d, 2) if d else ""


def _stage_row(label, ls, spend=None, meta_leads=None):
    ls = [l for l in ls if not l["duplicate"]]
    n = len(ls)
    b = sum(l["booked"] for l in ls)
    perf = sum(l["performed"] for l in ls)
    reb = sum(l["rebooked"] for l in ls)
    rev = round(sum(l["revenue"] for l in ls), 2)
    row = [label, n, b, _pct(b, n), perf, _pct(perf, b), reb, _pct(reb, perf),
           sum(l["appts"] for l in ls), sum(l["upcoming"] for l in ls),
           rev, round(rev / n, 2) if n else 0]
    if spend is None:
        return row + [""] * len(SPEND_HDR)
    spend = round(spend, 2)
    return row + [spend, meta_leads, _money(spend, n), _money(spend, b),
                  _money(spend, perf), f"{rev / spend:.1f}x" if spend else ""]


def _campaign_clinic(spend_rows, leads):
    """campaign_id -> clinic: the Preferred Clinic most of its leads chose,
    else the clinic named in the campaign, else 'Other'."""
    votes = defaultdict(lambda: defaultdict(int))
    for l in leads:
        if l.get("campaign_id"):
            votes[l["campaign_id"]][l["clinic"]] += 1
    out = {}
    for r in spend_rows or []:
        cid = r["campaign_id"]
        if cid in out:
            continue
        if votes.get(cid):
            out[cid] = max(votes[cid], key=votes[cid].get)
        else:
            name = r["campaign"].lower()
            out[cid] = next((c for c in ("Maghera", "Omagh", "Cookstown") if c.lower() in name), "Other")
    return out


def build_rows(leads, now, spend_rows):
    have_spend = spend_rows is not None
    spend_rows = spend_rows or []
    months = defaultdict(list)
    for l in leads:
        months[l["date"].strftime("%B %Y")].append(l)
    m_spend, m_mleads = defaultdict(float), defaultdict(int)
    for r in spend_rows:
        k = r["date"].strftime("%B %Y")
        m_spend[k] += r["spend"]
        m_mleads[k] += r["meta_leads"]
    month_keys = sorted(set(months) | {k for k, v in m_spend.items() if v},
                        key=lambda m: datetime.strptime(m, "%B %Y"), reverse=True)
    tot_spend = sum(r["spend"] for r in spend_rows)

    out = [["Meta Ads — Lead Value Tracker"],
           [f"Last updated: {now.astimezone(LONDON):%Y-%m-%d %H:%M}  ·  "
            f"{len(leads)} Meta leads since {leads[0]['date']:%d %b %Y}  ·  "
            f"{sum(1 for l in leads if l['patient'])} found in Cliniko  ·  "
            + (f"Meta spend £{tot_spend:,.2f} (live from Meta)" if have_spend
               else "⚠ Meta spend unavailable this run")],
           [], ["BY MONTH (month the lead came in / month the money was spent)"],
           ["Month"] + STAGE_HDR + SPEND_HDR]
    sp = (lambda v: v) if have_spend else (lambda v: None)
    for m in month_keys:
        out.append(_stage_row(m, months.get(m, []), sp(m_spend[m]), m_mleads[m]))
    out.append(_stage_row("TOTAL", leads, sp(tot_spend), sum(m_mleads.values())))
    out.append([])

    # By clinic — spend follows the clinic its campaign serves.
    camp_clinic = _campaign_clinic(spend_rows, leads)
    c_spend, c_mleads = defaultdict(float), defaultdict(int)
    for r in spend_rows:
        c_spend[camp_clinic[r["campaign_id"]]] += r["spend"]
        c_mleads[camp_clinic[r["campaign_id"]]] += r["meta_leads"]
    groups = defaultdict(list)
    for l in leads:
        groups[l["clinic"]].append(l)
    out += [["BY CLINIC (Preferred Clinic; spend by the clinic each campaign serves)"],
            ["Clinic"] + STAGE_HDR + SPEND_HDR]
    for g in sorted(set(groups) | {k for k, v in c_spend.items() if v},
                    key=lambda g: (-len(groups.get(g, [])), -c_spend.get(g, 0))):
        out.append(_stage_row(g, groups.get(g, []), sp(c_spend.get(g, 0.0)), c_mleads.get(g, 0)))
    out.append([])

    # By ad — keyed on Meta's ad id, so renaming a campaign/ad doesn't split it.
    a_spend, a_mleads, a_name = defaultdict(float), defaultdict(int), {}
    for r in sorted(spend_rows, key=lambda r: r["date"]):
        a_spend[r["ad_id"]] += r["spend"]
        a_mleads[r["ad_id"]] += r["meta_leads"]
        a_name[r["ad_id"]] = f"{r['campaign']} — {r['ad']}"   # latest name wins
    agroups = defaultdict(list)
    for l in leads:
        aid = l.get("ad_id") or ""
        agroups[aid].append(l)
        a_name.setdefault(aid, l["ad"] if aid else "(ad not known)")
    out += [["BY AD (campaign — ad, current names)"], ["Ad"] + STAGE_HDR + SPEND_HDR]
    for aid in sorted(set(agroups) | {k for k, v in a_spend.items() if v},
                      key=lambda a: (-len(agroups.get(a, [])), -a_spend.get(a, 0))):
        out.append(_stage_row(a_name[aid], agroups.get(aid, []),
                              sp(a_spend.get(aid, 0.0)) if aid else None, a_mleads.get(aid, 0)))
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
            ["  Meta Spend = live from Meta (ad account 164830011), every campaign incl. ones that don't collect leads (e.g. Omagh Launch). Cost / Lead uses OUR lead count; Meta-Reported Leads is Meta's own count, for comparison."],
            ["  Return = revenue so far ÷ spend. It grows as treatment courses complete, so recent months always look lower."]]
    return out


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

    spend = fetch_meta_spend(min(l["date"] for l in leads).replace(day=1))
    sh = _retry(lambda: gc.open_by_key(DROPOFF_SPREADSHEET_ID), "open drop-off sheet")
    try:
        ws = _retry(lambda: sh.worksheet(TAB), "open tab")
    except gspread.exceptions.WorksheetNotFound:
        ws = None
    rows = build_rows(leads, now, spend)
    for r in rows[:9]:
        print([c for i, c in enumerate(r) if i in (0, 1, 2, 4, 10, 12, 14, 16, 17)])
    print(f"... {len(rows)} rows")
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
    # Clear the old hand-typed-spend highlight (pre-API layout).
    reqs.append({"repeatCell": {"range": {"sheetId": ws.id},
                 "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat.backgroundColor"}})
    _retry(lambda: sh.batch_update({"requests": reqs}), "format")
    print("Meta Ads tab written.")


if __name__ == "__main__":
    main()
