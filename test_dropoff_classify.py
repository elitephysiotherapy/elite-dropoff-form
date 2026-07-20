"""Rule-tests for classify_dropoff's cancellation branch.

Regression cover for the bulk-cancel bug (Niamh O'Donnell, Jul 2026): a patient who
cancels a block of appointments but still holds nearer ones was flagged as a drop-off,
because the reschedule test asked "any booking after the CANCELLED SLOT?" instead of
"any booking still in the diary when they cancelled?".

Run: venv/bin/python test_dropoff_classify.py
"""
import sys
from datetime import datetime, timezone

import phase1_fetch as p1

# Pin "now" to 14 Jul 2026 (the morning Martin was DM'd for the 4th time) so these
# rule-tests don't rot as the real clock moves past the dates below.
p1._now_utc = lambda: datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)

FOLLOWUP = "999"          # a non-IA follow-up type
IA = None                 # filled from the real IA set below


def appt(id, starts, created="2025-12-03T20:08:00Z", cancelled=None, dna=False,
         type_id=FOLLOWUP):
    return {
        "id": id,
        "starts_at": starts,
        "created_at": created,
        "cancelled_at": cancelled,
        "did_not_arrive": dna,
        "appointment_type": {"links": {"self": f"https://x/appointment_types/{type_id}"}},
        "practitioner": {"links": {"self": "https://x/practitioners/1"}},
    }


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}\n        got={got!r} want={want!r}")
    return ok


results = []

# ---- 1. Niamh O'Donnell: bulk-cancelled a standing series, still booked in ----
# On 7 Jul 17:31 she cancelled 17 Jul, 31 Jul, 7 Aug, 14 Aug, 21 Aug (her 13:30 slots).
# She still held 16 Jul (booked the previous December) and rebooked 27 Jul + 10 Aug.
CANC = "2026-07-07T17:32:00Z"
attended = [appt("a1", "2026-07-04T09:00:00Z"), appt("a2", "2026-07-07T17:30:00Z")]
kept = [
    appt("k1", "2026-07-16T19:00:00Z"),                                   # held since Dec
    appt("k2", "2026-07-27T18:00:00Z", created="2026-07-07T17:30:00Z"),   # rebooked
    appt("k3", "2026-08-10T19:00:00Z", created="2026-07-08T10:37:00Z"),   # rebooked next day
]
bulk = [
    appt("c1", "2026-07-17T13:30:00Z", cancelled="2026-07-07T17:31:00Z"),
    appt("c2", "2026-07-31T13:30:00Z", cancelled=CANC),
    appt("c3", "2026-08-07T13:30:00Z", cancelled=CANC),
    appt("c4", "2026-08-14T13:30:00Z", cancelled=CANC),   # <- was flagged (session 60)
    appt("c5", "2026-08-21T13:30:00Z", cancelled=CANC),   # <- was flagged (session 61)
]
niamh = attended + kept + bulk
print("1. Bulk cancel while still booked in (Niamh O'Donnell) — none are drop-offs:")
for a in bulk:
    results.append(check(f"cancelled {a['starts_at'][:10]}",
                         p1.classify_dropoff(a, FOLLOWUP, niamh), None))

# ---- 2. Genuine drop-off: cancels the last thing in the diary ----
print("\n2. Genuine drop-off — cancels their only remaining booking:")
gone = appt("g1", "2026-07-20T10:00:00Z", cancelled="2026-07-10T09:00:00Z")
hist2 = [appt("p1", "2026-06-01T10:00:00Z"), appt("p2", "2026-06-15T10:00:00Z"), gone]
results.append(check("nothing left in diary", p1.classify_dropoff(gone, FOLLOWUP, hist2),
                     "cancelled"))

# ---- 2b. Cancelled, then rebooked days later: already back in the diary, no chase ----
print("\n2b. Cancelled 24 Jun with nothing left, rebooked later — no DM (already back):")
reb = appt("rb1", "2026-06-29T10:00:00Z", cancelled="2026-06-24T09:00:00Z")
hist2b = [appt("q1", "2026-06-01T10:00:00Z"), reb,
          appt("q2", "2026-09-07T10:00:00Z", created="2026-07-02T11:00:00Z")]
results.append(check("rebooked since — suppressed",
                     p1.classify_dropoff(reb, FOLLOWUP, hist2b), None))

# ---- 3. Ordinary near-term reschedule still suppressed ----
print("\n3. Ordinary reschedule — cancels 20 Jul, holds 25 Jul:")
resched = appt("r1", "2026-07-20T10:00:00Z", cancelled="2026-07-10T09:00:00Z")
hist3 = [appt("p1", "2026-06-01T10:00:00Z"), resched,
         appt("r2", "2026-07-25T10:00:00Z", created="2026-07-10T09:05:00Z")]
results.append(check("holds a later slot", p1.classify_dropoff(resched, FOLLOWUP, hist3), None))

# ---- 4. Cancels a future slot but keeps an EARLIER one (the exact bug shape) ----
print("\n4. Cancels 21 Aug but still holds 10 Aug — not a drop-off:")
far = appt("f1", "2026-08-21T13:30:00Z", cancelled="2026-07-07T17:32:00Z")
hist4 = [appt("p1", "2026-07-01T10:00:00Z"), far,
         appt("near", "2026-08-10T19:00:00Z", created="2026-07-01T10:00:00Z")]
results.append(check("earlier booking still held",
                     p1.classify_dropoff(far, FOLLOWUP, hist4), None))

# ---- 5. Bulk-cancel dedup survives across runs (no daily leak) ----
print("\n5. Bulk-cancel dedup — a row already logged by an earlier run is not re-added:")
rows = [
    {"patient": "Niamh O'Donnell", "cancellation_date": "2026-07-07 18:32",
     "appointment_date": "2026-08-14 14:30", "_patient_id": "1726"},
    {"patient": "Niamh O'Donnell", "cancellation_date": "2026-07-07 18:32",
     "appointment_date": "2026-08-21 14:30", "_patient_id": "1726"},
]
results.append(check("same run collapses to one row",
                     len(p1._dedup_same_day_cancellations(rows)), 1))
results.append(check("earlier run's row suppresses the rest",
                     len(p1._dedup_same_day_cancellations(
                         rows, already_logged_keys={("1726", "2026-07-07")})), 0))
# Keyed on patient id, never the name — duplicate patient records share names.
results.append(check("a same-named DIFFERENT patient is not suppressed",
                     len(p1._dedup_same_day_cancellations(
                         rows, already_logged_keys={("9999", "2026-07-07")})), 1))



# ---- 6. Peter McNicholl: only a diagnostic attended, no real IA (2026-07-20) ----
# Attended an Ultrasound Assessment with Julie on 9 Jul, then cancelled a Review
# on 21 Jul. is_ia_only_patient_at counted the ultrasound as "their IA", so the
# cancellation landed as a physio-owned IADNR against the booked-with physio.
# A diagnostic is not an assessment: this is a pre-IA IACNA.
ULTRASOUND = "1206575759565526893"   # 3. Ultrasound Assessment
REVIEW     = "382563815511823515"    # 2. Review Appointment
REAL_IA    = "382563815654429852"    # 1. Initial Appointment
SPORTS_MASSAGE = "752219543803270402"

us_attended = appt("US", "2026-07-09T13:00:00Z", type_id=ULTRASOUND)
rev_cancel  = appt("RV", "2026-07-21T11:00:00Z", cancelled="2026-07-17T12:07:00Z",
                   type_id=REVIEW)
peter = [us_attended, rev_cancel]
results.append(check("ultrasound-only patient cancels follow-up -> iacna, not iadnr",
                     p1.classify_dropoff(rev_cancel, REVIEW, peter), "iacna"))

# Same shape but DNA'd instead of cancelled -> iadna, not iadnr
rev_dna = appt("RV2", "2026-07-21T11:00:00Z", dna=True, type_id=REVIEW)
results.append(check("ultrasound-only patient DNAs follow-up -> iadna, not iadnr",
                     p1.classify_dropoff(rev_dna, REVIEW, [us_attended, rev_dna]), "iadna"))

# ---- 7. The IADNR hard rule is untouched: real IA attended, then dropped ----
# Rhonda Wilson: attended an Initial Appointment with Aoife on 3 Jul, then
# cancelled her ultrasound. She WAS assessed, so this stays an IADNR.
ia_attended = appt("IA", "2026-07-03T12:30:00Z", type_id=REAL_IA)
us_cancel   = appt("US2", "2026-07-16T13:00:00Z", cancelled="2026-07-14T09:00:00Z",
                   type_id=ULTRASOUND)
rhonda = [ia_attended, us_cancel]
results.append(check("real IA attended then cancels -> iadnr (hard rule intact)",
                     p1.classify_dropoff(us_cancel, ULTRASOUND, rhonda), "iadnr"))

# ---- 8. Established patient with no IA on record stays a plain review CNA ----
# Guards against the new IA gate over-firing: several attended visits means
# they're engaged in care, so a cancellation is an ordinary CNA, not pre-IA.
est = [appt("E1", "2026-06-01T09:00:00Z", type_id=REVIEW),
       appt("E2", "2026-06-08T09:00:00Z", type_id=REVIEW),
       appt("E3", "2026-06-15T09:00:00Z", type_id=REVIEW)]
est_cancel = appt("E4", "2026-06-22T09:00:00Z", cancelled="2026-06-21T09:00:00Z",
                  type_id=REVIEW)
results.append(check("established patient, no IA on record -> cancelled (not iacna)",
                     p1.classify_dropoff(est_cancel, REVIEW, est + [est_cancel]),
                     "cancelled"))

# ---- 9. Sports Massage is now classified into the sheet, not dropped ----
# Julieann Bell DNA'd a Sports Massage on 30 Jun having attended nothing since
# 2020. It belongs on the weekly list as IADNA; phase2 keeps it out of stats.
jb_old = appt("JB1", "2020-01-08T09:40:00Z", type_id=REAL_IA)
jb_dna = appt("JB2", "2026-06-30T14:20:00Z", dna=True, type_id=SPORTS_MASSAGE)
results.append(check("Sports Massage DNA after 6-year gap -> iadna (on the sheet)",
                     p1.classify_dropoff(jb_dna, SPORTS_MASSAGE, [jb_old, jb_dna]),
                     "iadna"))


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
