"""Phase 2 — enrichment functions for drop-off rows.

Provides:
  - episode detection (IA-anchor with 180-day gap fallback)
  - session number computation
  - IA Rebook Rate calculation (per physio + clinic-wide)
  - body-area + pathology categorisation via Claude API (added later)

Imported by phase1_fetch.py; can also be run standalone for ad-hoc analysis.
"""

import os, re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import requests
import anthropic

load_dotenv(override=True)
BASE = f"https://api.{os.environ['CLINIKO_SHARD']}.cliniko.com/v1"
SESSION = requests.Session()
SESSION.auth = (os.environ["CLINIKO_API_KEY"], "")
SESSION.headers.update({"User-Agent": os.environ["CLINIKO_USER_AGENT"], "Accept": "application/json"})
ID_RE = re.compile(r"/(\d+)(?:/[^/]*)?/?$")

# Broader list — used to find the *start* of the current episode for note review.
# Includes one-and-done consultation types Martin flagged on 2026-05-11.
PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS = {
    "382563815654429852",   # 1. Initial Appointment
    "392015278608749674",   # 3. Club Initial Assessment
    "1558530673046721630",  # 5. Private Health Insurance Initial Assessment
    "945551547020874765",   # 7. ACL Initial Assessment
    "1521627460095973060",  # 2. Sports & MSK Clinical Consultation
    "1118674052857206233",  # Mummy MOT Initial Assessment
    "1194028405859816854",  # Pelvic Health Assessment
    "1396206071189608060",  # Club Consultation
}

# Strict 4 — used only for IA Rebook Rate denominator (must be a "real IA"
# that expects a follow-up; one-and-done types are excluded).
STRICT_IA_TYPE_IDS = {
    "382563815654429852", "392015278608749674",
    "1558530673046721630", "945551547020874765",
}

GAP_DAYS_FOR_NEW_EPISODE = 180


def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def id_from_link(rel):
    if not rel:
        return None
    self_url = (rel.get("links") or {}).get("self") or ""
    m = ID_RE.search(self_url)
    return m.group(1) if m else None


import time


_LAST_REQ_TIME = [0.0]
MIN_INTERVAL_S = 0.35  # ~3 req/s, stays under Cliniko's 200/min limit


def fetch_all(path, params=None):
    url = f"{BASE}{path}"
    qp = list(params or []) + [("per_page", 100)]
    first = True
    while url:
        elapsed = time.time() - _LAST_REQ_TIME[0]
        if elapsed < MIN_INTERVAL_S:
            time.sleep(MIN_INTERVAL_S - elapsed)
        r = None
        for attempt in range(12):
            _LAST_REQ_TIME[0] = time.time()
            try:
                r = SESSION.get(url, params=qp if first else None, timeout=30)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                # Transient network/DNS blip (e.g. wifi just woke) — back off and retry
                wait = min(5 * (attempt + 1), 60)
                print(f"  network error ({type(e).__name__}) on {path}, "
                      f"retry {attempt + 1}/12 in {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "10"))
                time.sleep(wait + 2)
                continue
            break
        first = False
        if r is None:
            raise RuntimeError(f"Network failed after 12 retries on {url}")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} on {r.url}: {r.text[:200]}")
        data = r.json()
        coll = next(k for k, v in data.items() if isinstance(v, list))
        for item in data[coll]:
            yield item
        url = (data.get("links") or {}).get("next")


_ALL_PRACS_CACHE = [None]


def all_practitioners():
    """Every practitioner keyed by str(id) — ACTIVE **AND** INACTIVE.

    Cliniko's default /practitioners listing returns only active staff, and
    GET /practitioners/<id> 404s once someone is deactivated. Their appointments
    are NOT deleted — they still come back from /individual_appointments — but
    without the inactive records here, a leaver's practitioner_id can't be
    resolved to a name, so their work collapses into a "?" bucket and their
    named row on every rebuilt tab goes empty. That's what happened when Daire
    McKenna was deactivated (2026-07-13).

    Always use this for practitioner_id -> name lookups, never a bare
    fetch_all("/practitioners"), so deactivating a leaver in Cliniko (which
    stops their per-practitioner billing) never blanks historical data.

    Note this is a LOOKUP table, not a roster: callers build per-physio rows
    from appointments that actually exist in the period, so past staff only
    ever appear where they genuinely worked.
    """
    if _ALL_PRACS_CACHE[0] is None:
        pracs = {str(p["id"]): p for p in fetch_all("/practitioners")}
        for p in fetch_all("/practitioners", [("q[]", "active:=false")]):
            pracs.setdefault(str(p["id"]), p)
        _ALL_PRACS_CACHE[0] = pracs
    return _ALL_PRACS_CACHE[0]


def fetch_patient_full_history(patient_id):
    """Patient's full appointment history (live + cancelled), sorted ascending."""
    live = list(fetch_all("/individual_appointments", [("q[]", f"patient_id:={patient_id}")]))
    cancelled = list(fetch_all("/individual_appointments", [
        ("q[]", f"patient_id:={patient_id}"),
        ("q[]", "cancelled_at:?"),
    ]))
    by_id = {a["id"]: a for a in live}
    for a in cancelled:
        by_id[a["id"]] = a
    return sorted(by_id.values(), key=lambda a: a.get("starts_at") or "")


def find_episode(all_appts, gap_days=GAP_DAYS_FOR_NEW_EPISODE):
    """Determine the current episode boundary.

    Strategy:
      1. Most recent appointment whose type is in PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS.
      2. Fallback: most recent gap ≥ gap_days; episode starts at appt after the gap.
      3. If no gaps at all, treat entire history as one episode.

    Returns: (anchor_appt, episode_appts, anchor_reason).
    anchor_reason ∈ {'IA', 'GAP', 'WHOLE'} or None for empty input.
    """
    if not all_appts:
        return None, [], None

    # Try IA anchor (walk from most recent backwards)
    for a in reversed(all_appts):
        if id_from_link(a.get("appointment_type")) in PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS:
            idx = all_appts.index(a)
            return a, all_appts[idx:], "IA"

    # Gap heuristic
    gap_thresh = timedelta(days=gap_days)
    episode_start_idx = 0
    for i in range(1, len(all_appts)):
        prev = parse_iso(all_appts[i - 1].get("starts_at"))
        curr = parse_iso(all_appts[i].get("starts_at"))
        if prev and curr and (curr - prev) >= gap_thresh:
            episode_start_idx = i
    reason = "GAP" if episode_start_idx > 0 else "WHOLE"
    return all_appts[episode_start_idx], all_appts[episode_start_idx:], reason


def session_number_for(appt_id, episode_appts):
    """1-indexed position of appt_id in the episode (None if not present)."""
    for i, a in enumerate(episode_appts, start=1):
        if str(a["id"]) == str(appt_id):
            return i
    return None


# ---------------- Notes fetch & extraction ----------------

def fetch_episode_notes(patient_id, anchor_dt):
    """Treatment notes finalized/created on or after anchor_dt (the episode start)."""
    boundary = anchor_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    relevant = []
    for n in fetch_all(f"/patients/{patient_id}/treatment_notes"):
        if n.get("deleted_at") or n.get("archived_at"):
            continue
        when = n.get("finalized_at") or n.get("created_at") or ""
        if when >= boundary:
            relevant.append(n)
    return sorted(relevant, key=lambda n: n.get("finalized_at") or n.get("created_at") or "")


def extract_note_text(note):
    """Flatten a treatment note's structured Q/A content into clean text (HTML stripped)."""
    parts = []
    title = note.get("title", "") or "(untitled)"
    date = (note.get("finalized_at") or note.get("created_at") or "")[:10]
    author = note.get("author_name", "?")
    parts.append(f"=== Note ({date}, {author}, {title}) ===")
    for section in note.get("content", {}).get("sections", []) or []:
        sec_name = section.get("name") or ""
        for q in section.get("questions", []) or []:
            ans = q.get("answer")
            if ans in (None, "", [], {}):
                continue
            if isinstance(ans, str):
                clean = re.sub(r"<[^>]+>", " ", ans)
                clean = re.sub(r"\s+", " ", clean).strip()
            else:
                clean = str(ans)
            if clean:
                qname = q.get("name") or ""
                label = f"{sec_name} / {qname}".strip(" /")
                parts.append(f"  [{label}] {clean}")
    return "\n".join(parts)


def build_episode_notes_text(patient_id, anchor_dt, max_chars=20000):
    """Concatenate episode notes. If current episode has no notes, fall back to the
    patient's 5 most recent prior notes (the Odhrain case: patient with old ACL
    notes from a previous episode whose current episode is unsporadic).

    Returns: (text, n_notes, fallback_used).
    """
    notes = fetch_episode_notes(patient_id, anchor_dt)
    fallback_used = False
    if not notes:
        all_notes = [n for n in fetch_all(f"/patients/{patient_id}/treatment_notes")
                     if not n.get("deleted_at") and not n.get("archived_at")]
        all_notes.sort(key=lambda n: n.get("finalized_at") or n.get("created_at") or "",
                       reverse=True)
        notes = all_notes[:5]
        fallback_used = bool(notes)

    body = "\n\n".join(extract_note_text(n) for n in notes)
    if len(body) > max_chars:
        body = "[…earlier notes truncated…]\n\n" + body[-max_chars:]
    return body, len(notes), fallback_used


# ---------------- AI body-area categorisation ----------------

BODY_AREA_CATEGORIES = [
    "Foot & Ankle", "Knee", "Quadriceps", "Hamstring",
    "Hip & Groin", "Lumbar Spine", "Thoracic Spine", "Neck",
    "Shoulder", "Elbow", "Wrist & Hand",
]

CATEGORY_GUIDE = """- Foot & Ankle: foot, ankle, toe, calf, shin splints, tibial stress syndrome
- Knee: all knee injuries including ACL (pre/post-op)
- Quadriceps: quadriceps injuries and tears
- Hamstring: hamstring injuries and tears
- Hip & Groin: OA hip, pubic pain, inguinal pain, adductor, FAI/CAM, hernias, FAI
- Lumbar Spine: low back, lumbar, lumbar nerve root incl. sciatic referral
- Thoracic Spine: thoracic, rib cage
- Neck: cervical spine, cervical nerve root, neck
- Shoulder: shoulder, scapular, rotator cuff, glenohumeral, AC joint, biceps LH
- Elbow: elbow, forearm
- Wrist & Hand: wrist, hand, fingers, thumb, MCP
"""

CATEGORISATION_PROMPT = f"""You categorise UK physiotherapy clinical notes for AGGREGATE TREND ANALYSIS. \
The output drives team-level dashboards (e.g. "is the clinic struggling with knee patients this month?"), \
not individual clinical decisions.

Read the SOAP-style notes from ONE patient's current episode of care. Identify the PRIMARY presenting complaint.

CATEGORIES (pick exactly one for body_area):
{CATEGORY_GUIDE}

RULES

1. **ROOT CAUSE OVER SYMPTOM LOCATION — HARD RULE.** If notes use ANY of these descriptors — \
"neural", "nerve-related", "nerve pain", "referred", "radicular", "sciatic", "sciatica", "shooting", \
"radiating", "tingling/numbness in [limb]" — classify by the SPINAL ORIGIN, not where the pain is felt:
   - Neural / sciatic / referred pain in posterior thigh, glute, calf, foot → **Lumbar Spine** (NOT Hamstring/Knee/Foot & Ankle)
   - Neural / referred pain down the arm, forearm, hand, fingers → **Neck** (NOT Shoulder/Elbow/Wrist & Hand)
   - Femoral nerve referral to anterior thigh / quad area → **Lumbar Spine** (NOT Quadriceps/Hip & Groin)
   - Thoracic outlet / brachial plexus referral → **Neck**
   Only classify at the symptom site if notes describe a TRUE local tissue injury (muscle strain, tendon tear, joint sprain, \
contusion, bursitis) WITHOUT neural/referred descriptors.

2. **Multi-site:** pick the DOMINANT body area (mentioned/treated most often across the notes).

3. **Post-op** patients categorised by body area (ACL → Knee; rotator cuff repair → Shoulder).

4. **Physio shorthand:** "lsp"=lumbar spine, "tsp"=thoracic spine, "csp"=cervical spine, "rv"=review, \
"AROM/PROM"=active/passive range of motion, "WBAT"=weight bear as tolerated, "STM"=soft tissue mobilisation, \
"MWM"=mobilisation-with-movement, "RICE"=rest/ice/compression/elevation, "ROM"=range of motion.

OUTPUT — return EXACTLY two lines, no preamble or explanation:
body_area: <one category from the list>
pathology: <one short clinical description (≤80 chars)>
"""

_ANTHROPIC_CLIENT = None
def _client():
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        _ANTHROPIC_CLIENT = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _ANTHROPIC_CLIENT


# ---------------- Weekly Stats ----------------

def weekly_clinic_stats(start_utc, end_utc):
    """Compute the weekly KPI snapshot for the given window."""
    import config
    s_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # All appointments starting in window — live + cancelled merged.
    live = list(fetch_all("/individual_appointments",
                          [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}")]))
    cancelled = list(fetch_all("/individual_appointments",
                               [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"),
                                ("q[]", "cancelled_at:?")]))
    by_id = {a["id"]: a for a in live}
    for a in cancelled:
        by_id[a["id"]] = a
    appts = list(by_id.values())

    # CNA/DNA counts: non-IA = "review appointment" cancellations; IA = pre-IA drop-offs
    n_ias_performed = 0           # NPs: broader 13 types (matches Martin's tracker)
    n_strict_ias_performed = 0    # strict 4 IA types (used for IA Rebook %)
    n_cnas_review = 0             # cancellations of non-IA appts (excl reschedules)
    n_dnas_review = 0             # DNAs of non-IA appts
    n_iacnas = 0                  # cancellations of any new-patient appt (CNA-1st, excl reschedules)
    n_iadnas = 0                  # DNAs of any new-patient appt (DNA-1st)
    seen_appts = []
    strict_ias_attended = []      # strict 4 only — for IA Rebook % numerator
    used_minutes = 0

    # Cache patient histories for reschedule + DNA filter
    history_cache = {}
    def _get_history(pid):
        if pid not in history_cache:
            try:
                history_cache[pid] = fetch_patient_full_history(pid)
            except Exception:
                history_cache[pid] = []
        return history_cache[pid]

    def _is_reschedule(appt):
        pid = id_from_link(appt.get("patient"))
        if not pid:
            return False
        appt_start = appt.get("starts_at") or ""
        appt_id = str(appt.get("id"))
        return any(
            (h.get("starts_at") or "") > appt_start
            and not h.get("cancelled_at")
            and str(h.get("id")) != appt_id
            for h in _get_history(pid)
        )

    # DNAs that count = not rescheduled + first unresolved DNA per patient
    candidates = []
    for a in appts:
        if not a.get("did_not_arrive") or a.get("cancelled_at"):
            continue
        t_id = id_from_link(a.get("appointment_type"))
        if t_id in config.EXCLUDED_FROM_TOTAL_APPTS:
            continue
        if t_id in config.EXCLUDED_FROM_DROPOFF_STATS:
            continue  # Sports Massage never counts toward clinic/physio drop-off stats
        if _is_reschedule(a):
            continue
        candidates.append(a)
    by_pat = {}
    for a in candidates:
        pid = id_from_link(a.get("patient"))
        by_pat.setdefault(pid, []).append(a)
    true_dna_ids = set()
    for pid, plist in by_pat.items():
        if not pid:
            true_dna_ids.update(p["id"] for p in plist)
            continue
        psorted = sorted(plist, key=lambda x: x.get("starts_at") or "")
        history = _get_history(pid)
        prev = None
        for a in psorted:
            a_start = a.get("starts_at") or ""
            if prev is None:
                true_dna_ids.add(a["id"])
                prev = a_start
                continue
            attended_between = any(
                prev < (h.get("starts_at") or "") < a_start
                and not h.get("cancelled_at")
                and not h.get("did_not_arrive")
                for h in history
            )
            if attended_between:
                true_dna_ids.add(a["id"])
            prev = a_start

    for a in appts:
        type_id = id_from_link(a.get("appointment_type"))
        is_excluded = type_id in config.EXCLUDED_FROM_TOTAL_APPTS
        is_cancelled = bool(a.get("cancelled_at"))
        is_dna = bool(a.get("did_not_arrive"))

        # Service hours: ALL non-cancelled appointments block physio time, including
        # classes/workshops. Cancelled slots are freed → excluded.
        if not is_cancelled:
            start_dt = parse_iso(a.get("starts_at"))
            end_dt = parse_iso(a.get("ends_at"))
            if start_dt and end_dt:
                used_minutes += (end_dt - start_dt).total_seconds() / 60

        # Classes/workshops don't count toward patient throughput metrics
        if is_excluded:
            continue

        is_np = type_id in config.NEW_PATIENT_TYPE_IDS  # broader 13
        is_strict_ia = type_id in config.PHASE1_DROPOFF_IA_TYPE_IDS  # strict 4
        is_sports_massage = type_id in config.EXCLUDED_FROM_DROPOFF_STATS
        is_attended = not is_cancelled and not is_dna

        if is_cancelled:
            if is_sports_massage:
                pass  # Sports Massage never counts as a drop-off (clinic/physio stats)
            elif _is_reschedule(a):
                pass  # reschedule — excluded from CNA/IACNA counts
            elif is_np:
                n_iacnas += 1
            else:
                n_cnas_review += 1
        elif is_dna:
            if is_sports_massage:
                pass  # Sports Massage never counts as a drop-off (clinic/physio stats)
            elif a["id"] not in true_dna_ids:
                pass  # rescheduled OR duplicate DNA — excluded
            elif is_np:
                n_iadnas += 1
            else:
                n_dnas_review += 1
        else:
            seen_appts.append(a)

        # IAs Performed = NPs (broader 13) — matches Martin's tracker count
        if is_np and is_attended:
            n_ias_performed += 1
        # IA Rebook %: only the strict 4 IA types (those with genuine follow-up
        # expectations — excludes Ultrasound, Profiling, Injury Update Testing which
        # are diagnostic / one-and-done by design)
        if is_strict_ia and is_attended:
            n_strict_ias_performed += 1
            strict_ias_attended.append(a)

    def _has_any_appt(patient_id, after_iso, strict_gt=False):
        """Throttled / retried check via fetch_all generator. Early-exits on first item."""
        op = ">" if strict_gt else ">="
        for _ in fetch_all("/individual_appointments", [
            ("q[]", f"patient_id:={patient_id}"),
            ("q[]", f"starts_at:{op}{after_iso}"),
        ]):
            return True
        return False

    # IAs rebooked: strict 4 IAs where the patient ATTENDED a later appointment
    # OR has an active (non-cancelled) future booking. A later appointment that
    # was DNA'd does NOT count — a booked-then-no-showed 2nd visit is a drop-off,
    # not a rebook. This matches ia_rebook_rate_for_window and lets the weekly
    # IA Rebook % MATURE: a 2nd visit booked in the IA week counts as rebooked
    # (provisionally high), but if it's later CNA'd (cancelled → excluded by the
    # default query) or DNA'd (filtered out below), the daily recompute drops it.
    n_ias_rebooked = 0
    for ia in strict_ias_attended:
        patient_id = id_from_link(ia.get("patient"))
        ia_starts = ia.get("starts_at")
        if not (patient_id and ia_starts):
            continue
        later = list(fetch_all("/individual_appointments", [
            ("q[]", f"patient_id:={patient_id}"),
            ("q[]", f"starts_at:>{ia_starts}"),
        ]))
        if any(not a.get("did_not_arrive") for a in later):
            n_ias_rebooked += 1

    # Group appointments (classes) — fetch and add to service hours
    try:
        group_appts = list(fetch_all("/group_appointments", [
            ("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"),
        ]))
    except Exception:
        group_appts = []
    for g in group_appts:
        if g.get("cancelled_at") or g.get("archived_at") or g.get("deleted_at"):
            continue
        s_dt = parse_iso(g.get("starts_at"))
        e_dt = parse_iso(g.get("ends_at"))
        if s_dt and e_dt:
            used_minutes += (e_dt - s_dt).total_seconds() / 60

    # Clinic Rebook %: unique attended-patients in window with any future booking after week end.
    patient_ids = {id_from_link(a.get("patient")) for a in seen_appts}
    patient_ids.discard(None)
    n_with_future = sum(1 for pid in patient_ids if _has_any_appt(pid, e_iso))

    used_hours = used_minutes / 60
    util_pct = (used_hours / config.CLINIC_WEEKLY_HOURS) * 100 if config.CLINIC_WEEKLY_HOURS else None
    # IA Rebook % uses strict 4 IAs only — diagnostic types are excluded
    ia_rebook_pct = (n_ias_rebooked / n_strict_ias_performed * 100) if n_strict_ias_performed else None
    clinic_rebook_pct = (n_with_future / len(patient_ids) * 100) if patient_ids else None

    total_seen = len(seen_appts)
    review_appts = total_seen - n_ias_performed
    cna_pct = (n_cnas_review / review_appts * 100) if review_appts else None
    dna_pct = (n_dnas_review / review_appts * 100) if review_appts else None
    combined_pct = ((n_cnas_review + n_dnas_review) / review_appts * 100) if review_appts else None
    cna_dna_1st_pct = ((n_iacnas + n_iadnas) / n_ias_performed * 100) if n_ias_performed else None

    return {
        "window_start_local": start_utc,
        "window_end_local": end_utc,
        "ias_performed": n_ias_performed,          # broader 13 (NPs)
        "strict_ias_performed": n_strict_ias_performed,  # strict 4 (for IA Rebook % only)
        "ias_rebooked": n_ias_rebooked,
        "ia_rebook_pct": ia_rebook_pct,
        "cnas_review": n_cnas_review,
        "dnas_review": n_dnas_review,
        "iacnas": n_iacnas,
        "iadnas": n_iadnas,
        "total_appts_seen": total_seen,
        "review_appts": review_appts,
        "cna_pct": cna_pct,
        "dna_pct": dna_pct,
        "combined_pct": combined_pct,
        "cna_dna_1st_pct": cna_dna_1st_pct,
        "unique_patients_seen": len(patient_ids),
        "patients_with_future": n_with_future,
        "clinic_rebook_pct": clinic_rebook_pct,
        "used_hours": round(used_hours, 1),
        "capacity_hours": config.CLINIC_WEEKLY_HOURS,
        "utilization_pct": util_pct,
        "n_group_appts": len(group_appts),
    }


# ---------------- Per-Physio Weekly Stats (Weekly Team Stats tab) ----------------

def weekly_stats_per_physio(start_utc, end_utc):
    """Per-physio weekly Utilisation % and Clinic Rebook % for the week window.

    A lean companion to weekly_clinic_stats (which is clinic-wide only). Returns a
    dict keyed by display_name → {used_hours, available_hours, util_pct,
    unique_patients_seen, patients_with_future, clinic_rebook_pct}.

    - Utilisation % = non-cancelled appointment hours (individual + classes, the
      same basis as the clinic figure) ÷ that physio's weekly available hours
      (config.PHYSIO_WEEKLY_HOURS).
    - Clinic Rebook % = of the unique patients that physio ATTENDED this week, the
      share with any future booking after week end — the per-physio version of the
      clinic-wide clinic_rebook_pct in weekly_clinic_stats.
    """
    import config
    s_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    live = list(fetch_all("/individual_appointments",
                          [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}")]))
    cancelled = list(fetch_all("/individual_appointments",
                               [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"),
                                ("q[]", "cancelled_at:?")]))
    by_id = {a["id"]: a for a in live}
    for a in cancelled:
        by_id[a["id"]] = a
    appts = list(by_id.values())
    try:
        group_appts = list(fetch_all("/group_appointments",
                                    [("q[]", f"starts_at:>={s_iso}"),
                                     ("q[]", f"starts_at:<{e_iso}")]))
    except Exception:
        group_appts = []

    pracs = all_practitioners()   # incl. inactive — leavers keep their name

    def _display(prac_id):
        prac = pracs.get(prac_id) or {}
        full = f"{prac.get('first_name','?')} {prac.get('last_name','')}".strip()
        return config.PRACTITIONER_DISPLAY_NAME.get(full, full)

    physios = {}

    def _slot(display):
        return physios.setdefault(display, {
            "display": display, "used_minutes": 0.0, "attended_patients": set(),
        })

    for a in appts:
        prac_id = id_from_link(a.get("practitioner"))
        if not prac_id:
            continue
        s = _slot(_display(prac_id))
        if not a.get("cancelled_at"):
            s_dt = parse_iso(a.get("starts_at"))
            e_dt = parse_iso(a.get("ends_at"))
            if s_dt and e_dt:
                s["used_minutes"] += (e_dt - s_dt).total_seconds() / 60
        type_id = id_from_link(a.get("appointment_type"))
        is_attended = not a.get("cancelled_at") and not a.get("did_not_arrive")
        if is_attended and type_id not in config.EXCLUDED_FROM_TOTAL_APPTS:
            pid = id_from_link(a.get("patient"))
            if pid:
                s["attended_patients"].add(pid)

    for g in group_appts:
        if g.get("cancelled_at") or g.get("archived_at") or g.get("deleted_at"):
            continue
        prac_id = id_from_link(g.get("practitioner"))
        if not prac_id:
            continue
        s = _slot(_display(prac_id))
        s_dt = parse_iso(g.get("starts_at"))
        e_dt = parse_iso(g.get("ends_at"))
        if s_dt and e_dt:
            s["used_minutes"] += (e_dt - s_dt).total_seconds() / 60

    def _has_future(patient_id):
        # Any non-cancelled appointment from week-end onward (default query hides
        # cancelled), matching weekly_clinic_stats' clinic_rebook check.
        for _ in fetch_all("/individual_appointments", [
            ("q[]", f"patient_id:={patient_id}"),
            ("q[]", f"starts_at:>={e_iso}"),
        ]):
            return True
        return False

    future_cache = {}
    for s in physios.values():
        used_hrs = s["used_minutes"] / 60
        avail = config.PHYSIO_WEEKLY_HOURS.get(s["display"])
        s["used_hours"] = round(used_hrs, 1)
        s["available_hours"] = avail
        s["util_pct"] = (used_hrs / avail * 100) if avail else None
        pats = s.pop("attended_patients")
        n_future = 0
        for pid in pats:
            if pid not in future_cache:
                future_cache[pid] = _has_future(pid)
            if future_cache[pid]:
                n_future += 1
        s["unique_patients_seen"] = len(pats)
        s["patients_with_future"] = n_future
        s["clinic_rebook_pct"] = (n_future / len(pats) * 100) if pats else None

    return physios


# ---------------- Per-Physio Monthly Stats (Performance Dashboard) ----------------

def monthly_stats_per_physio(start_utc, end_utc):
    """Compute KPIs per practitioner for the given month window.

    Returns: dict keyed by display_name → {nps, total_apts, cnas_review, dnas_review,
        iacnas, iadnas, used_hours, available_hours, dna_pct, cna_pct, combined_pct,
        cna_dna_1st_pct, pva, gen_pop_pva, util_pct, ...}
    """
    import config
    s_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Pull all individual + group appointments in window
    live = list(fetch_all("/individual_appointments",
                          [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}")]))
    cancelled = list(fetch_all("/individual_appointments",
                               [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"),
                                ("q[]", "cancelled_at:?")]))
    by_id = {a["id"]: a for a in live}
    for a in cancelled:
        by_id[a["id"]] = a
    appts = list(by_id.values())
    try:
        group_appts = list(fetch_all("/group_appointments",
                                    [("q[]", f"starts_at:>={s_iso}"),
                                     ("q[]", f"starts_at:<{e_iso}")]))
    except Exception:
        group_appts = []

    pracs = all_practitioners()   # incl. inactive — leavers keep their name

    def _practitioner_display(prac_id):
        prac = pracs.get(prac_id) or {}
        full = f"{prac.get('first_name','?')} {prac.get('last_name','')}".strip()
        return config.PRACTITIONER_DISPLAY_NAME.get(full, full), full

    def _new(display, full):
        return {
            "display": display, "full_names": {full},
            "total_apts": 0, "nps": 0,
            "cnas_review": 0, "dnas_review": 0, "iacnas": 0, "iadnas": 0,
            "iadnrs": 0,
            "used_minutes": 0,
            "gen_pop_initial": 0, "gen_pop_review": 0,
        }

    physios = {}
    # Cache patient histories — used for reschedule check + DNA dedup.
    history_cache = {}

    def _get_history(pid):
        if pid not in history_cache:
            try:
                history_cache[pid] = fetch_patient_full_history(pid)
            except Exception:
                history_cache[pid] = []
        return history_cache[pid]

    def _is_reschedule(appt):
        pid = id_from_link(appt.get("patient"))
        if not pid:
            return False
        appt_start = appt.get("starts_at") or ""
        appt_id = str(appt.get("id"))
        return any(
            (h.get("starts_at") or "") > appt_start
            and not h.get("cancelled_at")
            and str(h.get("id")) != appt_id
            for h in _get_history(pid)
        )

    def _build_true_dna_set(appts_list):
        """DNA counts only if patient hasn't subsequently attended/rebooked
        AND it's the first unresolved DNA for that patient in window."""
        # Exclude rescheduled DNAs (patient has booking afterwards)
        candidates = []
        for a in appts_list:
            if not a.get("did_not_arrive") or a.get("cancelled_at"):
                continue
            t_id = id_from_link(a.get("appointment_type"))
            if t_id in config.EXCLUDED_FROM_TOTAL_APPTS:
                continue
            if t_id in config.EXCLUDED_FROM_DROPOFF_STATS:
                continue  # Sports Massage — never counted toward physio drop-off stats
            if _is_reschedule(a):
                continue
            candidates.append(a)
        # Dedup per patient: keep first DNA unless an attended appt sits between two DNAs
        by_patient = {}
        for a in candidates:
            pid = id_from_link(a.get("patient"))
            by_patient.setdefault(pid, []).append(a)
        keep = set()
        for pid, plist in by_patient.items():
            if not pid:
                keep.update(p["id"] for p in plist)
                continue
            psorted = sorted(plist, key=lambda x: x.get("starts_at") or "")
            history = _get_history(pid)
            prev = None
            for a in psorted:
                a_start = a.get("starts_at") or ""
                if prev is None:
                    keep.add(a["id"])
                    prev = a_start
                    continue
                attended_between = any(
                    prev < (h.get("starts_at") or "") < a_start
                    and not h.get("cancelled_at")
                    and not h.get("did_not_arrive")
                    for h in history
                )
                if attended_between:
                    keep.add(a["id"])
                prev = a_start
        return keep

    def _build_true_cna_set(appts_list):
        """CNA counts only once per patient per month — mirrors the DNA dedup.

        Without this, a persistent canceller (e.g. Peter Kennedy 3x in May)
        hits the responsible physio with 3 CNAs even though it's the same
        patient. Per-patient dedup keeps the first cancellation; later ones
        only count if the patient attended an appointment in between (i.e.
        genuinely re-engaged and dropped off again).
        """
        candidates = []
        for a in appts_list:
            if not a.get("cancelled_at"):
                continue
            t_id = id_from_link(a.get("appointment_type"))
            if t_id in config.EXCLUDED_FROM_TOTAL_APPTS:
                continue
            if t_id in config.EXCLUDED_FROM_DROPOFF_STATS:
                continue  # Sports Massage — never counted toward physio drop-off stats
            if _is_reschedule(a):
                continue
            candidates.append(a)
        by_patient = {}
        for a in candidates:
            pid = id_from_link(a.get("patient"))
            by_patient.setdefault(pid, []).append(a)
        keep = set()
        for pid, plist in by_patient.items():
            if not pid:
                keep.update(p["id"] for p in plist)
                continue
            psorted = sorted(plist, key=lambda x: x.get("starts_at") or "")
            history = _get_history(pid)
            prev = None
            for a in psorted:
                a_start = a.get("starts_at") or ""
                if prev is None:
                    keep.add(a["id"])
                    prev = a_start
                    continue
                attended_between = any(
                    prev < (h.get("starts_at") or "") < a_start
                    and not h.get("cancelled_at")
                    and not h.get("did_not_arrive")
                    for h in history
                )
                if attended_between:
                    keep.add(a["id"])
                prev = a_start
        return keep

    true_dna_ids = _build_true_dna_set(appts)
    true_cna_ids = _build_true_cna_set(appts)

    # Per-patient IADNR dedup — one lost patient = one IADNR, regardless of how
    # many drop-off events they generate in the month. The IA event "owns" the
    # patient (chronologically first), so iterating appts in date order means
    # the IA physio gets credited first; subsequent drop-off events for the
    # same patient are skipped.
    iadnr_patients_counted = set()
    appts.sort(key=lambda a: a.get("starts_at") or "")

    def _is_iadnr_event(a):
        """True if this non-IA cancellation/DNA is an IADNR — same definition
        as phase1_fetch.classify_dropoff's "iadnr" branch: patient is in their
        current episode with at most their IA attended (no follow-ups), no
        future booking (already established by the reschedule check upstream).

        Episode-relative `<= 1` (the original rule). The never-attended-ever
        pre-IA case is filtered upstream by _no_attendance_this_episode, so it
        never reaches here."""
        pid = id_from_link(a.get("patient"))
        if not pid:
            return False
        history = _get_history(pid)
        _, episode, _ = find_episode(history)
        if not episode:
            return False
        a_start = a.get("starts_at") or ""
        attended_before = sum(
            1 for h in episode
            if (h.get("starts_at") or "") < a_start
            and not h.get("cancelled_at")
            and not h.get("did_not_arrive")
        )
        return attended_before <= 1

    def _no_attendance_this_episode(a):
        """True if the patient has NOT attended anything in their current episode
        of care before this event. A gap of more than 60 days since the last
        attended visit starts a new episode (Martin 2026-07), so a never-attended
        patient OR a long-gap return that cancels/DNAs before attending is pre-IA
        (IACNA/IADNA) — not a physio-responsible IADNR or review CNA/DNA."""
        pid = id_from_link(a.get("patient"))
        if not pid:
            return False  # unknown patient — keep existing (treat as not pre-IA)
        a_start = a.get("starts_at") or ""
        attended = [h for h in (_get_history(pid) or [])
                    if (h.get("starts_at") or "") < a_start
                    and not h.get("cancelled_at")
                    and not h.get("did_not_arrive")]
        if not attended:
            return True  # never attended anything, ever
        last = max(attended, key=lambda h: h.get("starts_at") or "")
        ev, ls = parse_iso(a.get("starts_at")), parse_iso(last.get("starts_at"))
        if ev is None or ls is None:
            return False
        return (ev - ls).days > 60

    def _responsible_prac_id(a):
        """For a CNA/DNA on a NON-IA (review) appointment, return the physio
        who most recently ATTENDED the patient before this event. Matches the
        drop-off tracker's responsible-physio rule — Aoife shouldn't be hit
        on her CNA % for a patient that only ever saw Molaí. For IACNA/IADNA
        (cancelling/DNA-ing the first IA), there's no prior attending physio
        so we keep the scheduled physio."""
        pid = id_from_link(a.get("patient"))
        if not pid:
            return id_from_link(a.get("practitioner"))
        a_start = a.get("starts_at") or ""
        history = _get_history(pid)
        attended_before = [h for h in (history or [])
                           if (h.get("starts_at") or "") < a_start
                           and not h.get("cancelled_at")
                           and not h.get("did_not_arrive")]
        if attended_before:
            attended_before.sort(key=lambda x: x.get("starts_at") or "")
            prev = id_from_link(attended_before[-1].get("practitioner"))
            if prev:
                return prev
        return id_from_link(a.get("practitioner"))

    for a in appts:
        type_id = id_from_link(a.get("appointment_type"))
        is_excluded = type_id in config.EXCLUDED_FROM_TOTAL_APPTS
        is_cancelled = bool(a.get("cancelled_at"))
        is_dna = bool(a.get("did_not_arrive"))
        is_attended = not is_cancelled and not is_dna

        prac_id = id_from_link(a.get("practitioner"))
        if not prac_id:
            continue
        display, full = _practitioner_display(prac_id)
        stats = physios.setdefault(display, _new(display, full))
        stats["full_names"].add(full)

        # Service hours: ALL non-cancelled appointments (incl classes — physio time blocked)
        if not is_cancelled:
            s_dt = parse_iso(a.get("starts_at"))
            e_dt = parse_iso(a.get("ends_at"))
            if s_dt and e_dt:
                stats["used_minutes"] += (e_dt - s_dt).total_seconds() / 60

        if is_excluded:
            continue  # classes don't count for patient-throughput metrics

        # Performance Dashboard NPs / IACNA / IADNA all use the WIDER 8-type
        # IA set (strict 4 + Club Consultation, Sports & MSK Consult, Mummy MOT,
        # Pelvic Health) so the IACNA/IADNA buckets match the drop-off sheet's
        # labels — a cancelled Club Consultation is IACNA in both places, and
        # CNA-1st % (= (IACNAs + IADNAs) / NPs) has a consistent numerator and
        # denominator. (Martin 2026-05-28.)
        is_np = type_id in config.PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS

        if is_cancelled:
            if _is_reschedule(a):
                continue  # patient rebooked elsewhere — not a true CNA
            if a["id"] not in true_cna_ids:
                continue  # duplicate cancellation from the same patient — already counted
            if is_np:
                stats["iacnas"] += 1   # IACNA stays with the scheduled (IA) physio
            else:
                # Non-IA cancellation: attribute to the most-recently attending physio.
                # IADNR (patient lost early after IA) goes in its own bucket so the
                # plain CNA % only reflects review cancellations from patients still
                # engaged in care — same split Martin uses in his manual tracker.
                # Per-patient dedup on IADNR — if this patient already got counted
                # as an IADNR (typically via their attended-IA earlier in the
                # month), don't count a second time. The later CNA event still
                # exists in the sheet as a record, but the dashboard count = 1
                # lost patient.
                resp_disp, resp_full = _practitioner_display(_responsible_prac_id(a))
                target = physios.setdefault(resp_disp, _new(resp_disp, resp_full))
                pid = id_from_link(a.get("patient"))
                if _no_attendance_this_episode(a):
                    pass  # pre-IA (never attended this episode) — not physio-responsible
                elif _is_iadnr_event(a):
                    if pid and pid not in iadnr_patients_counted:
                        target["iadnrs"] += 1
                        iadnr_patients_counted.add(pid)
                    # else: same patient already counted via their IA event — skip
                else:
                    target["cnas_review"] += 1
        elif is_dna:
            if a["id"] not in true_dna_ids:
                continue  # rescheduled OR duplicate DNA — excluded
            if is_np:
                stats["iadnas"] += 1   # IADNA stays with the scheduled (IA) physio
            else:
                resp_disp, resp_full = _practitioner_display(_responsible_prac_id(a))
                target = physios.setdefault(resp_disp, _new(resp_disp, resp_full))
                pid = id_from_link(a.get("patient"))
                if _no_attendance_this_episode(a):
                    pass  # pre-IA (never attended this episode) — not physio-responsible
                elif _is_iadnr_event(a):
                    if pid and pid not in iadnr_patients_counted:
                        target["iadnrs"] += 1
                        iadnr_patients_counted.add(pid)
                else:
                    target["dnas_review"] += 1
        else:  # attended
            stats["total_apts"] += 1
            if is_np:
                stats["nps"] += 1
                # ATTENDED IA + NO FUTURE BOOKING = IADNR — but ONLY for the
                # strict 4 IA types that expect a follow-up. Mummy MOT, Pelvic
                # Health Assessment, Sports & MSK Consult and Club Consultation
                # are one-and-done by design, so flagging them as "no rebook =
                # IADNR" would unfairly penalise practitioners doing those
                # (e.g. Marty's 9 false IADNRs in May 2026). Strict-4-only
                # mirrors the IA Rebook Rate tab's STRICT_IA_TYPE_IDS gate.
                is_strict_ia = type_id in config.PHASE1_DROPOFF_IA_TYPE_IDS
                pid = id_from_link(a.get("patient")) if is_strict_ia else None
                # Only an IA that has ALREADY happened can be an IADNR. A future
                # booked IA looks "attended" to this code (not cancelled, not
                # DNA'd) and would otherwise be flagged as a drop-off purely
                # because no follow-up is on the books yet — but the patient
                # hasn't been seen, so there's nothing to rebook from. This
                # inflated every physio's mid-month IADNR count (Shannagh: 9→4,
                # July 2026). No-op for a completed past month, where every IA
                # start is already <= now. (Martin 2026-07-13.)
                ia_dt = parse_iso(a.get("starts_at"))
                ia_occurred = ia_dt is not None and ia_dt <= datetime.now(timezone.utc)
                if pid and ia_occurred and pid not in iadnr_patients_counted:
                    a_start = a.get("starts_at") or ""
                    history = _get_history(pid)
                    has_later_valid = any(
                        (h.get("starts_at") or "") > a_start
                        and not h.get("cancelled_at")
                        and not h.get("did_not_arrive")
                        for h in (history or [])
                    )
                    if not has_later_valid:
                        stats["iadnrs"] += 1
                        iadnr_patients_counted.add(pid)
            if type_id == config.GENPOP_INITIAL_TYPE_ID:
                stats["gen_pop_initial"] += 1
            elif type_id == config.GENPOP_REVIEW_TYPE_ID:
                stats["gen_pop_review"] += 1

    # Group appointments — add hours to the relevant practitioner
    for g in group_appts:
        if g.get("cancelled_at") or g.get("archived_at") or g.get("deleted_at"):
            continue
        prac_id = id_from_link(g.get("practitioner"))
        if not prac_id:
            continue
        display, full = _practitioner_display(prac_id)
        stats = physios.setdefault(display, _new(display, full))
        s_dt = parse_iso(g.get("starts_at"))
        e_dt = parse_iso(g.get("ends_at"))
        if s_dt and e_dt:
            stats["used_minutes"] += (e_dt - s_dt).total_seconds() / 60

    # Derived metrics
    for stats in physios.values():
        review = stats["total_apts"] - stats["nps"]
        stats["review_appts"] = review
        stats["dna_pct"] = (stats["dnas_review"] / review * 100) if review else None
        stats["cna_pct"] = (stats["cnas_review"] / review * 100) if review else None
        stats["combined_pct"] = ((stats["dnas_review"] + stats["cnas_review"]) / review * 100) if review else None
        stats["cna_dna_1st_pct"] = ((stats["iacnas"] + stats["iadnas"]) / stats["nps"] * 100) if stats["nps"] else None
        stats["pva"] = (stats["total_apts"] / stats["nps"]) if stats["nps"] else None
        # IA Rebook % per physio — Martin's formula: (NPs − IADNRs) / NPs
        stats["ia_rebook_pct"] = ((stats["nps"] - stats["iadnrs"]) / stats["nps"] * 100) if stats["nps"] else None
        # Drop off % per physio — Total Drop offs / (Total Drop offs + Reviews)
        total_drops = stats["cnas_review"] + stats["dnas_review"] + stats["iadnrs"]
        stats["total_dropoffs"] = total_drops
        denom = total_drops + review
        stats["dropoff_pct"] = (total_drops / denom * 100) if denom else None
        stats["gen_pop_pva"] = ((stats["gen_pop_initial"] + stats["gen_pop_review"]) / stats["gen_pop_initial"]) if stats["gen_pop_initial"] else None
        used_hrs = stats["used_minutes"] / 60
        stats["used_hours"] = round(used_hrs, 2)
        avail = config.PHYSIO_MONTHLY_HOURS.get(stats["display"])
        stats["available_hours"] = avail
        stats["util_pct"] = (used_hrs / avail * 100) if avail else None

    return physios


# ---------------- IA Rebook Rate ----------------

def ia_rebook_rate_for_window(start_dt_utc, end_dt_utc):
    """Compute IA Rebook Rate per physio and clinic-wide for IAs in [start, end).

    A patient counts as REBOOKED only if they attended (i.e. non-cancelled, non-DNA)
    at least one appointment AFTER the IA. IACNAs (cancelled IAs) and IADNAs (DNA'd
    IAs) are excluded entirely — physio never saw the patient.

    Uses STRICT_IA_TYPE_IDS (the 4 real-IA types), not the broader episode-anchor list.
    """
    s_iso = start_dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = end_dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Pull all appointments in window (live + cancelled merged)
    live = list(fetch_all("/individual_appointments",
                          [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}")]))
    cancelled = list(fetch_all("/individual_appointments",
                               [("q[]", f"starts_at:>={s_iso}"), ("q[]", f"starts_at:<{e_iso}"),
                                ("q[]", "cancelled_at:?")]))
    by_id = {a["id"]: a for a in live}
    for a in cancelled:
        by_id[a["id"]] = a

    ias = [a for a in by_id.values()
           if id_from_link(a.get("appointment_type")) in STRICT_IA_TYPE_IDS]
    iacna = [a for a in ias if a.get("cancelled_at")]
    iadna = [a for a in ias if a.get("did_not_arrive") and not a.get("cancelled_at")]
    attended_ias = [a for a in ias if not a.get("cancelled_at") and not a.get("did_not_arrive")]

    pracs = all_practitioners()   # incl. inactive — leavers keep their name

    per_physio = {}
    for ia in attended_ias:
        patient_id = id_from_link(ia.get("patient"))
        physio_id = id_from_link(ia.get("practitioner"))
        ia_starts = ia.get("starts_at")
        # Looser rule (Martin 2026-05-11): rebooked = attended past OR has active future booking.
        # Cancelled appts already filtered out by Cliniko default query, so any non-DNA result
        # is either an attended past appointment or a still-live future one. Both count.
        # If a future booking is later cancelled, Phase 1 catches it as IADNR on that day.
        later = list(fetch_all("/individual_appointments", [
            ("q[]", f"patient_id:={patient_id}"),
            ("q[]", f"starts_at:>{ia_starts}"),
        ]))
        valid_followups = [a for a in later if not a.get("did_not_arrive")]
        rebooked = len(valid_followups) > 0

        phys = pracs.get(physio_id) or {}
        name = f"{phys.get('first_name','?')} {phys.get('last_name','')}".strip()
        if physio_id not in per_physio:
            per_physio[physio_id] = {"name": name, "ias": 0, "rebooked": 0}
        per_physio[physio_id]["ias"] += 1
        if rebooked:
            per_physio[physio_id]["rebooked"] += 1

    for s in per_physio.values():
        s["rate"] = (s["rebooked"] / s["ias"]) if s["ias"] else None

    clinic_ias = sum(s["ias"] for s in per_physio.values())
    clinic_rebooked = sum(s["rebooked"] for s in per_physio.values())
    return {
        "window": {"start": start_dt_utc, "end": end_dt_utc},
        "per_physio": per_physio,
        "clinic": {
            "ias": clinic_ias,
            "rebooked": clinic_rebooked,
            "rate": (clinic_rebooked / clinic_ias) if clinic_ias else None,
        },
        "iacna_count": len(iacna),
        "iadna_count": len(iadna),
    }


def categorise_episode_notes(notes_text, model="claude-sonnet-4-5"):
    """Return (body_area, pathology) for the given (de-identified) notes text."""
    if not notes_text.strip():
        return None, None
    msg = _client().messages.create(
        model=model,
        max_tokens=200,
        system=[{"type": "text", "text": CATEGORISATION_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": notes_text}],
    )
    text = msg.content[0].text.strip()
    body_area, pathology = None, None
    for line in text.splitlines():
        if line.lower().startswith("body_area:"):
            body_area = line.split(":", 1)[1].strip()
        elif line.lower().startswith("pathology:"):
            pathology = line.split(":", 1)[1].strip()
    usage = getattr(msg, "usage", None)
    return body_area, pathology, usage


# ---------------- Self-test runner ----------------

def _test_session_counts_for_yesterdays_dropoffs():
    """Compute session counts for yesterday's W/C 04 May 2026 drop-offs as a sanity check."""
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo("Europe/London")
    types_by_id = {str(t["id"]): t.get("name", "?") for t in fetch_all("/appointment_types")}

    now = datetime.now(LONDON)
    # Last 8 days, cancelled only — mirrors what Phase 1 wrote yesterday
    start = (now - timedelta(days=8)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    yesterdays = [a for a in fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={start}"),
        ("q[]", f"starts_at:<{end}"),
        ("q[]", "cancelled_at:?"),
    ])]

    # Just the 5 patients we actually wrote to the sheet (cancelled non-reschedules on 2026-05-08)
    target_names = {"Marian Conway", "Ronan O'Kane", "John Conway", "Joey Devlin", "Gabhan McIvor"}
    candidates = [a for a in yesterdays
                  if a.get("patient_name") in target_names
                  and a.get("starts_at", "").startswith("2026-05-08")]

    print(f"{'patient':<22}  {'appt_type':<35}  {'anchor':<12}  {'anchor_date':<11}  {'episode':<8}  session")
    print("-" * 110)
    for a in candidates:
        patient_id = id_from_link(a.get("patient"))
        history = fetch_patient_full_history(patient_id)
        anchor_appt, episode, reason = find_episode(history)
        n = session_number_for(a["id"], episode)
        anchor_date = (anchor_appt.get("starts_at") or "")[:10] if anchor_appt else "—"
        appt_type = types_by_id.get(id_from_link(a.get("appointment_type")), "?")
        print(f"{a['patient_name']:<22}  {appt_type[:35]:<35}  {reason:<12}  "
              f"{anchor_date:<11}  {len(episode):<8}  {n}")


if __name__ == "__main__":
    _test_session_counts_for_yesterdays_dropoffs()
