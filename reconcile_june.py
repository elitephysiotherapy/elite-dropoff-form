"""One-off: reconcile Martin's manual June drop-off sheet (203 rows) against the
engine's classification, event-by-event, to explain the 203 vs 125 gap."""
import json, collections
from datetime import datetime, timezone, timedelta
import config
import phase2 as p2
from phase2 import fetch_all, fetch_patient_full_history, id_from_link, find_episode

BST = timezone(timedelta(hours=1))  # June = BST

# ---- 1. Load manual rows -------------------------------------------------
MF = "/Users/martinloughran/.claude/projects/-Users-martinloughran/c0f5ec42-16a3-483d-9958-6dd7a236e296/tool-results/mcp-ee6774a4-a473-4d84-8603-72f737300851-read_file_content-1782921455129.txt"
c = json.load(open(MF))["fileContent"]
lines = [l for l in c.split("\n") if l.startswith("|")]
hdr = [h.strip() for h in lines[0].strip("|").split("|")]
valid = {"cancelled", "iadnr", "did_not_attend"}
manual = []
for l in lines[1:]:
    cells = [x.strip().replace("\\", "") for x in l.strip("|").split("|")]
    if len(cells) < len(hdr):
        continue
    r = dict(zip(hdr, cells))
    if r["Drop-off Type"] not in valid:
        continue
    # normalise datetime "2026-06-04 13:00" -> key
    dt = r["Appointment Date"].strip()
    manual.append({"name": r["Patient Name"].strip(), "dt": dt,
                   "physio": r["Physio"].strip(), "type": r["Drop-off Type"]})
# DEDUPE — the sheet read returned multiple tabs; collapse identical rows
_seen = {}
for m in manual:
    _seen[(m["name"].lower(), m["dt"], m["type"])] = m
manual = list(_seen.values())
print("Manual UNIQUE rows:", len(manual))

FULL2SHORT = {
    "Molaí Smith": "Molaí", "Daire McKenna": "Daire", "Shannagh Conwell": "Shannagh",
    "Aoife O'Kane": "Aoife", "Sinead McGill": "Sinead", "Erin McNicholl": "Erin",
    "Martin Loughran": "Marty", "Julie McVey": "Julie",
}
for m in manual:
    m["short"] = FULL2SHORT.get(m["physio"], m["physio"])

# ---- 2. Fetch June appts (engine window) --------------------------------
s_iso = "2026-05-31T23:00:00Z"; e_iso = "2026-06-30T23:00:00Z"
live = list(fetch_all("/individual_appointments", [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}")]))
canc = list(fetch_all("/individual_appointments", [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"), ("q[]", "cancelled_at:?")]))
by_id = {a["id"]: a for a in live}
for a in canc: by_id[a["id"]] = a
appts = list(by_id.values())
pracs = p2.all_practitioners()   # incl. inactive — leavers keep their name
print("June appts:", len(appts))

def disp(pid):
    p = pracs.get(pid) or {}
    full = f"{p.get('first_name','?')} {p.get('last_name','')}".strip()
    return config.PRACTITIONER_DISPLAY_NAME.get(full, full)

hist_cache = {}
def hist(pid):
    if pid not in hist_cache:
        try: hist_cache[pid] = fetch_patient_full_history(pid)
        except Exception: hist_cache[pid] = []
    return hist_cache[pid]

def is_reschedule(a):
    pid = id_from_link(a.get("patient"));
    if not pid: return False
    st = a.get("starts_at") or ""; aid = str(a.get("id"))
    return any((h.get("starts_at") or "") > st and not h.get("cancelled_at") and str(h.get("id")) != aid for h in hist(pid))

def is_iadnr_event(a):
    pid = id_from_link(a.get("patient"))
    if not pid: return False
    _, ep, _ = find_episode(hist(pid))
    if not ep: return False
    st = a.get("starts_at") or ""
    att = sum(1 for h in ep if (h.get("starts_at") or "") < st and not h.get("cancelled_at") and not h.get("did_not_arrive"))
    return att <= 1

def responsible(a):
    pid = id_from_link(a.get("patient"))
    if not pid: return id_from_link(a.get("practitioner"))
    st = a.get("starts_at") or ""
    ab = [h for h in hist(pid) if (h.get("starts_at") or "") < st and not h.get("cancelled_at") and not h.get("did_not_arrive")]
    if ab:
        ab.sort(key=lambda x: x.get("starts_at") or "")
        return id_from_link(ab[-1].get("practitioner")) or id_from_link(a.get("practitioner"))
    return id_from_link(a.get("practitioner"))

# ---- 3. Classify each cancelled / DNA appt ------------------------------
IA_SET = config.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS
iadnr_counted = set()
# sort so IADNR dedup is deterministic by time
events = sorted([a for a in appts if a.get("cancelled_at") or a.get("did_not_arrive")],
                key=lambda x: x.get("starts_at") or "")
verdict = {}  # appt_id -> (status, reason, attributed_physio)
for a in events:
    tid = id_from_link(a.get("appointment_type"))
    st = p2.parse_iso(a.get("starts_at"))
    local = st.astimezone(BST).strftime("%Y-%m-%d %-H:%M") if st else "?"
    sched = disp(id_from_link(a.get("practitioner")))
    key = (local, sched)
    if tid in config.EXCLUDED_FROM_TOTAL_APPTS:
        verdict[a["id"]] = ("EXCLUDED", "class/group", sched, key); continue
    if tid in config.EXCLUDED_FROM_DROPOFF_STATS:
        verdict[a["id"]] = ("EXCLUDED", "sports-massage", sched, key); continue
    if tid in IA_SET:
        verdict[a["id"]] = ("IACNA/IADNA", "initial-assessment (own bucket, excluded from CNA/DNA)", sched, key); continue
    if is_reschedule(a):
        verdict[a["id"]] = ("EXCLUDED", "has-later-kept-appt (rebook/future booking)", sched, key); continue
    resp = disp(responsible(a))
    pid = id_from_link(a.get("patient"))
    if is_iadnr_event(a):
        if pid and pid in iadnr_counted:
            verdict[a["id"]] = ("EXCLUDED", "iadnr-already-counted-this-patient", resp, key); continue
        if pid: iadnr_counted.add(pid)
        verdict[a["id"]] = ("COUNTED", "iadnr", resp, key); continue
    verdict[a["id"]] = ("COUNTED", "cna/dna-review", resp, key)

# per-patient CNA/DNA dedup (a patient counted once per month for review drop)
# mirror true_cna/true_dna: collapse repeat review drops of same patient
seen_pat = collections.defaultdict(int)
for a in events:
    v = verdict[a["id"]]
    if v[0] == "COUNTED" and v[1] == "cna/dna-review":
        pid = id_from_link(a.get("patient"))
        seen_pat[pid] += 1
        if pid and seen_pat[pid] > 1:
            verdict[a["id"]] = ("EXCLUDED", "duplicate-review-drop-same-patient", v[2], v[3])

# ---- 4. Join to manual rows ---------------------------------------------
eng_by_key = collections.defaultdict(list)
for a in events:
    eng_by_key[verdict[a["id"]][3]].append(a["id"])

# fetch patient NAME for each event patient (robust join key)
import re
import time as _t
from phase2 import SESSION, BASE
pat_name = {}
def get_name(pid):
    if pid not in pat_name:
        pj = {}
        for _ in range(8):
            _t.sleep(0.55)
            try:
                r = SESSION.get(f"{BASE}/patients/{pid}", timeout=30)
            except Exception:
                continue
            if r.status_code == 429:
                _t.sleep(int(r.headers.get("Retry-After", "8")) + 1); continue
            if r.status_code == 200:
                pj = r.json()
            break
        pat_name[pid] = pj
    return pat_name[pid]

def norm(s):
    s = s.lower().strip()
    s = re.sub(r"\(.*?\)", "", s)          # drop "(Kildress)" etc
    s = re.sub(r"[^a-z ]", " ", s)         # drop apostrophes/hyphens
    return re.sub(r"\s+", " ", s).strip()

def nkey(s):                                # spelling-insensitive membership key
    return re.sub(r"[^a-z]", "", s.lower())

eng_by_name = collections.defaultdict(list)
for a in events:
    pid = id_from_link(a.get("patient"))
    if not pid:
        continue
    pj = get_name(pid)
    nm = norm(f"{pj.get('first_name','')} {pj.get('last_name','')}")
    eng_by_name[nm].append(a["id"])

reason_tally = collections.Counter()
matched = 0; unmatched = []
used = set()
for m in manual:
    cands = [i for i in eng_by_name.get(norm(m["name"]), []) if i not in used]
    # prefer the event whose local date matches the manual appt date
    day = m["dt"][:10]
    same_day = [i for i in cands if verdict[i][3][0].startswith(day)]
    pick = (same_day or cands)
    if pick:
        i = pick[0]; used.add(i); matched += 1
        st, reason, resp, _ = verdict[i]
        tag = "" if same_day else " [date-diff]"
        reason_tally[f"{st}: {reason}{tag}"] += 1
        continue
    unmatched.append(m); reason_tally["NO-MATCH (patient has NO cancelled/DNA in June)"] += 1

print("\n=== SAMPLE UNMATCHED MANUAL ROWS (first 30) ===")
for m in unmatched[:30]:
    print(f"  {m['dt']:17} {m['short']:9} {m['type']:14} {m['name']}")

print("\n=== MANUAL ROW → ENGINE VERDICT ===")
for r, n in reason_tally.most_common():
    print(f"  {n:3d}  {r}")
print(f"\nMatched {matched} / {len(manual)} manual rows")

# engine-counted events NOT present in manual sheet (spelling-insensitive)
manual_keys_name = set(nkey(m["name"]) for m in manual)
manual_short_by_name = {nkey(m["name"]): m["short"] for m in manual}
extra = []
for a in events:
    if verdict[a["id"]][0] != "COUNTED":
        continue
    pj = get_name(id_from_link(a.get("patient")))
    full = f"{pj.get('first_name','')} {pj.get('last_name','')}".strip()
    if nkey(full) not in manual_keys_name:
        extra.append((a, full))
print(f"\n=== ENGINE COUNTED but patient NOT on your sheet: {len(extra)} ===")
for a, full in sorted(extra, key=lambda x: x[0].get('starts_at') or ''):
    v = verdict[a["id"]]
    reason = "attended-IA no rebook (IADNR)" if v[1] == "iadnr" else "review cancel/DNA not logged"
    print(f"  {v[3][0]:16} | engine→{v[2]:9} | {reason:30} | {full}")

# reattribution: matched rows where engine's treating physio != your assigned physio
print(f"\n=== ON your sheet but engine attributes to a DIFFERENT physio ===")
for a in events:
    v = verdict[a["id"]]
    pj = get_name(id_from_link(a.get("patient")))
    full = f"{pj.get('first_name','')} {pj.get('last_name','')}".strip()
    k = nkey(full)
    if k in manual_short_by_name:
        your = manual_short_by_name[k]
        sched = disp(id_from_link(a.get("practitioner")))
        if v[2] != your and v[0] == "COUNTED":
            print(f"  {v[3][0]:16} | you={your:9} booked-with={sched:9} engine→{v[2]:9} | {full}")

# totals
eng_counts = collections.Counter()
for a in events:
    if verdict[a["id"]][0] == "COUNTED":
        eng_counts[verdict[a["id"]][2]] += 1
print("\n=== UNMATCHED manual rows split by YOUR drop-off type ===")
ut = collections.Counter(m["type"] for m in unmatched)
for t, n in ut.most_common():
    print(f"  {n:3d}  {t}")
blank = sum(1 for pid in pat_name if not pat_name[pid].get("first_name"))
print(f"  (patient-name lookups that returned blank: {blank} of {len(pat_name)})")

# ---- dump reviewable JSON for the sheet write -------------------------
type_names = {str(t["id"]): t.get("name", "") for t in fetch_all("/appointment_types")}
biz_names = {str(b["id"]): b.get("business_name") or b.get("name", "") for b in fetch_all("/businesses")}
def dropoff_type(a, reason):
    if reason == "iadnr":
        return "iadnr"
    return "cancelled" if a.get("cancelled_at") else ("did_not_attend" if a.get("did_not_arrive") else "")

extras_out = []
for a, full in sorted(extra, key=lambda x: x[0].get("starts_at") or ""):
    if nkey(full) == "luciaroseloane":   # same person as Lucy Rose Loane already on sheet
        continue
    v = verdict[a["id"]]
    st = p2.parse_iso(a.get("starts_at"))
    tid = id_from_link(a.get("appointment_type"))
    reason = ("Attended their initial assessment and never rebooked = IADNR. There is no cancellation, "
              "so it never shows as a drop-off 'event' to log manually.") if v[1] == "iadnr" else \
             ("Review cancellation/DNA in June that wasn't on your list.")
    extras_out.append({
        "appt_date": st.astimezone(BST).strftime("%Y-%m-%d %H:%M") if st else "",
        "cancel_date": (p2.parse_iso(a.get("cancelled_at")).astimezone(BST).strftime("%Y-%m-%d %H:%M") if a.get("cancelled_at") else ""),
        "name": full,
        "engine_physio": v[2],
        "clinic": biz_names.get(id_from_link(a.get("business")), ""),
        "appt_type": type_names.get(tid, ""),
        "dropoff_type": dropoff_type(a, v[1]),
        "note": f"Not on your list. Engine attributes to {v[2]}. {reason}",
    })

reattrib_out = []
for a in events:
    v = verdict[a["id"]]
    if v[0] != "COUNTED":
        continue
    pj = get_name(id_from_link(a.get("patient")))
    full = f"{pj.get('first_name','')} {pj.get('last_name','')}".strip()
    k = nkey(full)
    if k in manual_short_by_name:
        your = manual_short_by_name[k]
        if v[2] != your:
            reattrib_out.append({
                "name": full, "your_physio": your, "engine_physio": v[2],
                "note": f"Should be attributed to {v[2]} — the last physio to actually treat this "
                        f"patient — not {your}. (You logged it under {your}.)",
            })

import json as _json
OUT = "/private/tmp/claude-501/-Users-martinloughran/c0f5ec42-16a3-483d-9958-6dd7a236e296/scratchpad/june_recon.json"
_json.dump({"extras": extras_out, "reattrib": reattrib_out}, open(OUT, "w"), indent=2)
print(f"\nWROTE {OUT}: {len(extras_out)} extras, {len(reattrib_out)} reattribution rows")

print("\n=== ALL UNMATCHED (name | dt | your-type | your-physio) ===")
for m in sorted(unmatched, key=lambda x: x["dt"]):
    print(f"  {m['name']:28} {m['dt']:16} {m['type']:14} {m['short']}")

print("\n=== ENGINE COUNTED drop-offs per responsible physio ===")
for p, n in sorted(eng_counts.items(), key=lambda x: -x[1]):
    print(f"  {n:3d}  {p}")
print("  TOTAL", sum(eng_counts.values()))
