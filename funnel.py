"""Conversion funnel(s) for the New Patient Bookings tracker.

Two funnels (Martin, 2026-06-26):
  • ALL IAs   — every IA the bookings tracker logs (config.BOOKINGS_IA_TYPE_IDS).
  • CORE IAs  — the four new-patient assessment types Martin tracks:
                1. Initial Appointment, 3. Club Initial Assessment,
                5. Private Health Insurance Initial Assessment, 7. ACL Initial Assessment.

Stages:
  ① Leads not booked      — manual Leads tab, status != booked (ALL funnel only)
     Total leads          = ① + IAs booked
  ② IAs booked            — IA bookings (New + Past). Reactivations excluded
                            (a returning patient, not a fresh booking).
  ③ Outcome per booked IA — Attended / Pending / CNA (cancelled) / DNA, from live
                            Cliniko status. "Pending" = appointment hasn't happened
                            yet. Attend% = Attended / (Attended + CNA + DNA): of the
                            IAs that have RESOLVED, how many were delivered (pending
                            excluded so a fresh week doesn't read artificially low).
  ④ Rebooked & attended   — attended IAs whose patient has a later non-DNA
                            appointment (same rule as the IA Rebook Rate tab).

Reactivation dedup: if a patient CNA'd/DNA'd their booked IA and then came back
(a Reactivation) and attended, the ORIGINAL booked IA is credited as Attended
(moved out of CNA/DNA). The reactivation is never a second booked/attended IA.

Computed fresh each run by joining the sheet's booking rows (which carry the
New/Past/Reactivation classification) to live Cliniko status — no schema change,
nothing goes stale. Heavy (pulls ~6 months of appointments), so it runs on the
DAILY refresh, not the 6x/day trawl.

Usage:
  python funnel.py            preview — prints both funnels, writes nothing
  python funnel.py --write    rebuild the "Funnel" tab on the bookings sheet
"""

import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv(override=True)

import gspread
import config
import phase2
import bookings_fetch as bf

LONDON = bf.LONDON
ATTENDED_BUFFER_H = 2     # treat an appt as "passed" this long after its start
FUNNEL_TAB = "Funnel"

CORE_IA_TYPE_IDS = {
    "382563815654429852",   # 1. Initial Appointment
    "392015278608749674",   # 3. Club Initial Assessment
    "1558530673046721630",  # 5. Private Health Insurance Initial Assessment
    "945551547020874765",   # 7. ACL Initial Assessment
}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Sheet booking rows + live Cliniko status
# --------------------------------------------------------------------------

def load_booked_rows(sh):
    rows = []
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            recs = ws.get_all_records()
        except Exception as e:
            print(f"  WARN couldn't read {ws.title}: {e}")
            continue
        for r in recs:
            aid = str(r.get("appointment_id") or "").strip()
            if not aid:
                continue
            booked = str(r.get("Date Booked") or "").strip()
            dt = None
            for fmt, n in (("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
                try:
                    dt = datetime.strptime(booked[:n], fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                continue
            rows.append({"appt_id": aid,
                         "klass": str(r.get("New / Past Patient") or "").strip(),
                         "week": bf.week_tab_name(dt),
                         "month": dt.strftime("%Y-%m")})
    return rows


def pull_appointments(start_dt, end_dt):
    s_iso, e_iso = _iso(start_dt), _iso(end_dt)
    live = list(phase2.fetch_all("/individual_appointments",
                [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}")]))
    cancelled = list(phase2.fetch_all("/individual_appointments",
                [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"),
                 ("q[]", "cancelled_at:?")]))
    by_id = {str(a["id"]): a for a in live}
    for a in cancelled:
        by_id[str(a["id"])] = a
    by_patient = {}
    for a in by_id.values():
        by_patient.setdefault(phase2.id_from_link(a.get("patient")), []).append(a)
    for pid in by_patient:
        by_patient[pid].sort(key=lambda a: a.get("starts_at") or "")
    return by_id, by_patient


def _attendance(appt, now):
    if appt is None:
        return "Unknown"
    if appt.get("cancelled_at"):
        return "CNA"
    if appt.get("did_not_arrive"):
        return "DNA"
    start = phase2.parse_iso(appt.get("starts_at"))
    if start is None:
        return "Unknown"
    return "Pending" if start > now - timedelta(hours=ATTENDED_BUFFER_H) else "Attended"


def _has_rebook(pid, anchor_iso, by_patient):
    for a in by_patient.get(pid, []):
        if (a.get("starts_at") or "") <= anchor_iso:
            continue
        if a.get("did_not_arrive") or a.get("cancelled_at"):
            continue
        return True
    return False


# --------------------------------------------------------------------------
# Compute (pull once, tally per funnel)
# --------------------------------------------------------------------------

def _blank():
    return {"booked": 0, "attended": 0, "pending": 0, "cna": 0, "dna": 0, "rebooked": 0}


def _context(sh):
    now = datetime.now(LONDON)
    booked_rows = load_booked_rows(sh)
    by_id, by_patient = pull_appointments(now - timedelta(days=200), now + timedelta(days=120))
    react_by_patient = {}
    for r in booked_rows:
        if r["klass"] != "Reactivation":
            continue
        a = by_id.get(r["appt_id"])
        if not a:
            continue
        pid = phase2.id_from_link(a.get("patient"))
        if pid and _attendance(a, now) == "Attended":
            react_by_patient.setdefault(pid, []).append((a.get("starts_at") or "", r["appt_id"]))
    return now, booked_rows, by_id, by_patient, react_by_patient


def _credit_state(a, react_by_patient, used_reacts, now):
    """Attendance state for a booked IA, applying reactivation credit-back."""
    pid = phase2.id_from_link(a.get("patient")) if a else None
    state = _attendance(a, now)
    anchor = (a.get("starts_at") or "") if a else ""
    if state in ("CNA", "DNA") and pid:
        for rstart, rid in sorted(react_by_patient.get(pid, [])):
            if rid in used_reacts or rstart <= anchor:
                continue
            used_reacts.add(rid)
            state = "Attended"
            break
    return state, pid, anchor


def _add(d, state):
    d["booked"] += 1
    if state == "Attended":
        d["attended"] += 1
    elif state in ("Pending", "Unknown"):
        d["pending"] += 1
    elif state == "CNA":
        d["cna"] += 1
    elif state == "DNA":
        d["dna"] += 1


def _tally(now, booked_rows, by_id, by_patient, react_by_patient, type_filter):
    used_reacts = set()
    weeks, months = {}, {}
    for r in booked_rows:
        if r["klass"] not in ("New", "Past"):
            continue
        a = by_id.get(r["appt_id"])
        type_id = phase2.id_from_link(a.get("appointment_type")) if a else None
        if type_filter is not None and type_id not in type_filter:
            continue
        state, pid, anchor = _credit_state(a, react_by_patient, used_reacts, now)
        for period in (weeks.setdefault(r["week"], _blank()),
                       months.setdefault(r["month"], _blank())):
            _add(period, state)
        if state == "Attended" and _has_rebook(pid, anchor, by_patient):
            weeks[r["week"]]["rebooked"] += 1
            months[r["month"]]["rebooked"] += 1
    return weeks, months


def _normalize_physio(name):
    """Merge a physio's secondary "… CS" practitioner record (a separate Cliniko
    calendar) into their main name, e.g. "Martin Loughran CS" -> "Martin Loughran"
    (Martin, 2026-06-26)."""
    n = name.strip()
    if n.endswith(" CS"):
        n = n[:-3].strip()
    return n


def _tally_by_physio(now, booked_rows, by_id, by_patient, react_by_patient, type_filter, pracs):
    """months[month_key][physio_name] -> outcome dict. Physio = the booked IA's
    practitioner."""
    used_reacts = set()
    months = {}
    for r in booked_rows:
        if r["klass"] not in ("New", "Past"):
            continue
        a = by_id.get(r["appt_id"])
        type_id = phase2.id_from_link(a.get("appointment_type")) if a else None
        if type_filter is not None and type_id not in type_filter:
            continue
        state, pid, anchor = _credit_state(a, react_by_patient, used_reacts, now)
        physio_id = phase2.id_from_link(a.get("practitioner")) if a else None
        phys = pracs.get(physio_id) or {}
        pname = _normalize_physio(f"{phys.get('first_name','')} {phys.get('last_name','')}".strip()
                                  or "Unknown")
        d = months.setdefault(r["month"], {}).setdefault(pname, _blank())
        _add(d, state)
        if state == "Attended" and _has_rebook(pid, anchor, by_patient):
            d["rebooked"] += 1
    return months


def compute_all(sh):
    ctx = _context(sh)
    allw, allm = _tally(*ctx, None)
    corew, corem = _tally(*ctx, CORE_IA_TYPE_IDS)
    pracs = phase2.all_practitioners()   # incl. inactive — leavers keep their name
    core_phys = _tally_by_physio(*ctx, CORE_IA_TYPE_IDS, pracs)
    leads_w, leads_m = bf._lead_period_counts(sh)
    return {"all": {"weeks": allw, "months": allm},
            "core": {"weeks": corew, "months": corem},
            "core_by_physio": core_phys,
            "leads_w": leads_w, "leads_m": leads_m}


# --------------------------------------------------------------------------
# Render — shared row builder for print + sheet
# --------------------------------------------------------------------------

def _pct(n, d):
    return f"{round(100*n/d)}%" if d else "—"


def _sorted_keys(pmap, is_week):
    if is_week:
        return sorted(pmap, key=lambda t: datetime.strptime(t[4:], "%d %b %Y"), reverse=True)
    return sorted(pmap, reverse=True)


def _header(with_leads):
    base = ["IAs Booked", "Attended", "Pending", "CNA", "DNA", "Rebooked", "Attend%", "Rebook%"]
    return ["Period"] + (["Leads", "Not Booked"] if with_leads else []) + base


def _period_row(key, d, lmap, with_leads):
    resolved = d["attended"] + d["cna"] + d["dna"]
    cells = [key]
    if with_leads:
        cells += [lmap.get(key, 0) + d["booked"], lmap.get(key, 0)]
    cells += [d["booked"], d["attended"], d["pending"], d["cna"], d["dna"],
              d["rebooked"], _pct(d["attended"], resolved), _pct(d["rebooked"], d["attended"])]
    return cells


def _month_label(mk):
    return datetime.strptime(mk + "-01", "%Y-%m-%d").strftime("%B %Y")


PHYSIO_HEADER = ["Physio", "IAs Booked", "Attended", "Pending", "CNA", "DNA",
                 "Rebooked", "Attend%", "Rebook%"]


def _physio_row(name, d):
    resolved = d["attended"] + d["cna"] + d["dna"]
    return [name, d["booked"], d["attended"], d["pending"], d["cna"], d["dna"],
            d["rebooked"], _pct(d["attended"], resolved), _pct(d["rebooked"], d["attended"])]


# --------------------------------------------------------------------------
# Write the Funnel tab
# --------------------------------------------------------------------------

def _funnel_block(out, title, pmap_weeks, pmap_months, leads_w, leads_m, with_leads):
    out.append([title])
    for sub, pmap, lmap, is_week in (("BY WEEK (Sun–Sat, by booking date)", pmap_weeks, leads_w, True),
                                     ("BY CALENDAR MONTH", pmap_months, leads_m, False)):
        out.append([sub])
        out.append(_header(with_leads))
        for k in _sorted_keys(pmap, is_week):
            out.append(_period_row(k, pmap.get(k, _blank()), lmap, with_leads))
        out.append([])


def write_funnel_tab(sh=None):
    """Rebuild the 'Funnel' tab with both funnels (ALL IAs + CORE IAs)."""
    if sh is None:
        sh = bf.open_spreadsheet()
    data = compute_all(sh)
    now = datetime.now(LONDON)
    out = [["New Patient Bookings — Conversion Funnel"],
           [f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}"],
           ["Attend% = attended ÷ resolved (pending excluded). "
            "Rebook% = of attended IAs, those with a later non-DNA appointment."],
           []]
    _funnel_block(out, "FUNNEL 1 — ALL IAs",
                  data["all"]["weeks"], data["all"]["months"],
                  data["leads_w"], data["leads_m"], with_leads=True)
    _funnel_block(out, "FUNNEL 2 — CORE IAs (Initial / Club IA / PHI IA / ACL IA)",
                  data["core"]["weeks"], data["core"]["months"], {}, {}, with_leads=False)

    # Per-physio breakdown of the core funnel, one block per month (recent first).
    out.append(["FUNNEL 2b — CORE IAs BY PHYSIO"])
    for mk in sorted(data["core_by_physio"], reverse=True)[:6]:
        phys = data["core_by_physio"][mk]
        out.append([_month_label(mk)])
        out.append(PHYSIO_HEADER)
        total = _blank()
        for pname in sorted(phys):
            d = phys[pname]
            for k in total:
                total[k] += d[k]
            out.append(_physio_row(pname, d))
        out.append(_physio_row("CLINIC TOTAL", total))
        out.append([])

    try:
        ws = bf._gs_retry(lambda: sh.worksheet(FUNNEL_TAB), "funnel read")
        bf._gs_retry(lambda w=ws: w.clear(), "funnel clear")
    except gspread.exceptions.WorksheetNotFound:
        ws = bf._gs_retry(lambda: sh.add_worksheet(title=FUNNEL_TAB, rows=200, cols=11),
                          "funnel create")
    bf._gs_retry(lambda w=ws: w.update(values=out, range_name="A1",
                                       value_input_option="RAW"), "funnel write")
    bf._gs_retry(lambda w=ws: w.format("A1", {"textFormat": {"bold": True, "fontSize": 12}}),
                 "funnel title bold")
    return data


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------

def _print_funnel(title, fdata, leads_w, leads_m, with_leads):
    print(f"\n{'='*96}\n{title}\n{'='*96}")
    for label, pmap, lmap, is_week in (("BY WEEK (booking date)", fdata["weeks"], leads_w, True),
                                       ("BY MONTH (booking date)", fdata["months"], leads_m, False)):
        hdr = _header(with_leads)
        widths = [14] + [10] * (len(hdr) - 1)
        print(f"\n{label}")
        print(" ".join(f"{c:>{w}}" for c, w in zip(hdr, widths)))
        print("-" * (sum(widths) + len(widths)))
        for k in _sorted_keys(pmap, is_week):
            row = _period_row(k, pmap.get(k, _blank()), lmap, with_leads)
            print(" ".join(f"{str(c):>{w}}" for c, w in zip(row, widths)))


def _print_physio(title, months_map, max_months=3):
    print(f"\n{'='*96}\n{title}\n{'='*96}")
    widths = [22] + [10] * (len(PHYSIO_HEADER) - 1)
    for mk in sorted(months_map, reverse=True)[:max_months]:
        phys = months_map[mk]
        print(f"\n{_month_label(mk)}")
        print(" ".join(f"{c:>{w}}" for c, w in zip(PHYSIO_HEADER, widths)))
        print("-" * (sum(widths) + len(widths)))
        total = _blank()
        for pname in sorted(phys):
            d = phys[pname]
            for k in total:
                total[k] += d[k]
            print(" ".join(f"{str(c):>{w}}" for c, w in zip(_physio_row(pname, d), widths)))
        print(" ".join(f"{str(c):>{w}}" for c, w in zip(_physio_row("CLINIC TOTAL", total), widths)))


def main():
    write = "--write" in sys.argv
    sh = bf.open_spreadsheet()
    print("Computing conversion funnels (sheet bookings → live Cliniko status)…", flush=True)
    if write:
        data = write_funnel_tab(sh)
        print(f"\nFunnel tab rebuilt on '{config.BOOKINGS_SPREADSHEET_ID}'.")
    else:
        data = compute_all(sh)
    _print_funnel("FUNNEL 1 — ALL IAs", data["all"], data["leads_w"], data["leads_m"], True)
    _print_funnel("FUNNEL 2 — CORE IAs (Initial / Club IA / PHI IA / ACL IA)",
                  data["core"], {}, {}, False)
    _print_physio("FUNNEL 2b — CORE IAs BY PHYSIO", data["core_by_physio"])
    if not write:
        print("\n(Preview only — re-run with --write to build the Funnel tab.)")


if __name__ == "__main__":
    main()
