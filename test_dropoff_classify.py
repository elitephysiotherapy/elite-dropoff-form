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

def appt_with(prac, **kw):
    a = appt(**kw)
    a["practitioner"] = {"links": {"self": f"https://x/practitioners/{prac}"}}
    return a

JULIE, AOIFE, MARTIN = "111", "222", "333"

# Peter McNicholl: Ultrasound attended with JULIE, Review cancelled with AOIFE.
# Aoife never had him in front of her -> IACNA, off her clinical stats.
us_julie   = appt_with(JULIE, id="US", starts="2026-07-09T13:00:00Z", type_id=ULTRASOUND)
rev_aoife  = appt_with(AOIFE, id="RV", starts="2026-07-21T11:00:00Z",
                       cancelled="2026-07-17T12:07:00Z", type_id=REVIEW)
results.append(check("cancels with a physio who never saw them -> iacna",
                     p1.classify_dropoff(rev_aoife, REVIEW, [us_julie, rev_aoife]), "iacna"))

rev_aoife_dna = appt_with(AOIFE, id="RV2", starts="2026-07-21T11:00:00Z", dna=True,
                          type_id=REVIEW)
results.append(check("DNAs with a physio who never saw them -> iadna",
                     p1.classify_dropoff(rev_aoife_dna, REVIEW, [us_julie, rev_aoife_dna]),
                     "iadna"))

# Aidan Hughes: Ultrasound attended with MARTIN, Injection cancelled with MARTIN.
# Structurally identical to Peter EXCEPT the physio saw him -> IADNR.
# An earlier gate asked "was an IA *type* attended" and got this one wrong.
INJECTION = "1206575759565526894"
us_martin  = appt_with(MARTIN, id="US3", starts="2026-03-10T09:10:00Z", type_id=ULTRASOUND)
inj_cancel = appt_with(MARTIN, id="INJ", starts="2026-06-02T09:10:00Z",
                       cancelled="2026-06-01T09:00:00Z", type_id=INJECTION)
results.append(check("cancels with the physio who DID see them (diagnostic) -> iadnr",
                     p1.classify_dropoff(inj_cancel, INJECTION, [us_martin, inj_cancel]),
                     "iadnr"))

# ---- 10. Episode gap: 180 days, not 60 (Martin 2026-07-20) ----
# Ciaran Moran (118d), Aileen Wilson (91d), Peter Scullion (65d) are real IADNRs
# that the old 60-day gate wrongly demoted to pre-IA. Paddy Kelly (286d) and
# Julieann Bell (2358d) are genuine returners and stay pre-IA.
ia_120 = appt_with(MARTIN, id="G1", starts="2026-02-11T09:00:00Z", type_id=REAL_IA)
cx_120 = appt_with(MARTIN, id="G2", starts="2026-06-11T09:00:00Z",
                   cancelled="2026-06-10T09:00:00Z", type_id=REVIEW)
results.append(check("120-day gap, same physio -> still iadnr (was iacna at 60d)",
                     p1.classify_dropoff(cx_120, REVIEW, [ia_120, cx_120]), "iadnr"))

ia_286 = appt_with(MARTIN, id="G3", starts="2025-08-27T09:00:00Z", type_id=REAL_IA)
cx_286 = appt_with(MARTIN, id="G4", starts="2026-06-09T09:00:00Z",
                   cancelled="2026-06-08T09:00:00Z", type_id=REVIEW)
results.append(check("286-day gap -> iacna (episode has genuinely ended)",
                     p1.classify_dropoff(cx_286, REVIEW, [ia_286, cx_286]), "iacna"))

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


# ---- 10. Bulk-cancel keys seeded from the SHEET, not the fetch window ----
# Dara McKenna cancelled three pre-booked Club Follow Ups in one action on 27 Jul.
# The window-derived key set only suppressed siblings while the cancellation was
# still inside DAILY_LOOKBACK_DAYS, so the 11 Aug and 18 Aug appointments were
# written as fresh IADNRs on 30 Jul and 4 Aug — one lost patient tripled.
print("\n10. Bulk-cancel suppression is seeded from the sheet (window-independent):")
dara = [
    {"patient": "Dara McKenna", "cancellation_date": "2026-07-27 21:46",
     "appointment_date": "2026-08-11 18:00", "_patient_id": "1904"},
    {"patient": "Dara McKenna", "cancellation_date": "2026-07-27 21:46",
     "appointment_date": "2026-08-18 18:00", "_patient_id": "1904"},
]
# The 4 Aug sibling has long since aged out of the window, so the run's own scan
# contributes nothing — only the sheet-derived seed can suppress these.
results.append(check("sibling outside the lookback window is still suppressed",
                     len(p1._dedup_same_day_cancellations(
                         dara, already_logged_keys={("1904", "2026-07-27")})), 0))
results.append(check("without the seed the leak reappears",
                     len(p1._dedup_same_day_cancellations(dara)), 1))


# ---- 11. Fallback physio on a session>1 row is re-checked, not trusted ----
# A row cannot be both "session 7" and "no IA anchor": the session number is
# derived from the episode, so its existence contradicts the fallback. Connor
# Monaghan (s7) was filed against Shannagh, who had never attended him — Daire
# did the IA and every visit after.
print("\n11. No-episode physio fallback is re-checked when the session number denies it:")
DAIRE, SHANNAGH = "1501275397424158535", "1818200739135100480"
cm_ia = appt("CM1", "2026-03-23T11:00:00Z", type_id=REAL_IA)
cm_ia["practitioner"] = {"links": {"self": f"/practitioners/{DAIRE}"}}
cm_last = appt("CM2", "2026-06-29T08:10:00Z", type_id=REVIEW)
cm_last["practitioner"] = {"links": {"self": f"/practitioners/{DAIRE}"}}
cm_drop = appt("CM3", "2026-08-20T12:00:00Z", cancelled="2026-08-18T07:53:00Z",
               type_id=REVIEW)
cm_drop["practitioner"] = {"links": {"self": f"/practitioners/{SHANNAGH}"}}
cm_hist = [cm_ia, cm_last, cm_drop]
# The rule itself, given a COMPLETE history, already lands on Daire.
results.append(check("complete history attributes to the treating physio",
                     str(p1.responsible_physio_id(cm_drop, cm_hist)), DAIRE))
# Truncated history (the 06:00 failure mode) loses the anchor and falls back.
trunc = {}
results.append(check("truncated history falls back to booked-with",
                     str(p1.responsible_physio_id(cm_drop, [cm_drop], trunc)), SHANNAGH))
results.append(check("...and the fallback branch is recorded for the tripwire",
                     trunc.get("branch"), "B:no-ia-anchor"))
# A genuine pre-IA patient is session 1, so the guard must leave it alone.
results.append(check("session 1 fallback is legitimate, not a contradiction",
                     p1.verify_fallback_physios(
                         [{"patient": "Pre-IA", "physio": "Booked Physio",
                           "session_number": "1", "_physio_branch": "B:no-ia-anchor",
                           "_patient_id": "1", "appointment_id": "CM3"}]), []))


# ---- 12. One row per patient per lapse (Martin 2026-09-13) ----
# A patient appears twice only if they were reactivated in between (rebooked on a
# later day, then dropped that slot) or came back and attended in between.
print("\n12. One row per lapse — a second row needs a reactivation in between:")
# Martin Fasko: IA attended 7 Sep, Review booked at the IA, cancelled 10 Sep.
mf_ia = appt("MF1", "2026-09-07T15:00:00Z", created="2026-09-03T16:30:00Z", type_id=REAL_IA)
mf_rv = appt("MF2", "2026-09-14T15:40:00Z", created="2026-09-07T15:36:00Z",
             cancelled="2026-09-10T09:55:00Z", type_id=REVIEW)
mf = [mf_ia, mf_rv]
results.append(check("IA no-rebook + follow-up booked at the IA, cancelled -> same lapse",
                     p1.is_same_lapse(mf_ia, mf_rv, mf), True))
# Peter Kennedy: two Reviews booked on 21 May, cancelled 13:45 and 18:15 on 26 May.
pk_ia = appt("PK0", "2026-05-21T14:10:00Z", type_id=REAL_IA)
pk1 = appt("PK1", "2026-05-28T13:00:00Z", created="2026-05-21T15:09:00Z",
           cancelled="2026-05-26T12:45:00Z", type_id=REVIEW)
pk2 = appt("PK2", "2026-06-02T10:10:00Z", created="2026-05-21T15:11:00Z",
           cancelled="2026-05-26T17:15:00Z", type_id=REVIEW)
results.append(check("two pre-booked slots cancelled hours apart -> same lapse",
                     p1.is_same_lapse(pk1, pk2, [pk_ia, pk1, pk2]), True))
# Graham McCabe: Review cancelled 9 Jun, NEW Review booked 10 Jun, cancelled 12 Jun.
gm_ia = appt("GM0", "2026-06-02T14:20:00Z", type_id=REAL_IA)
gm1 = appt("GM1", "2026-06-11T14:00:00Z", created="2026-06-02T15:00:00Z",
           cancelled="2026-06-09T16:26:00Z", type_id=REVIEW)
gm2 = appt("GM2", "2026-06-15T07:00:00Z", created="2026-06-10T09:25:00Z",
           cancelled="2026-06-12T07:30:00Z", type_id=REVIEW)
results.append(check("reactivated (rebooked next day) then cancelled again -> two rows",
                     p1.is_same_lapse(gm1, gm2, [gm_ia, gm1, gm2]), False))
# Martin Mallon: Review cancelled 17 Aug; rebooked + attended 18 Aug; next cancelled 19 Aug.
mm1 = appt("MM1", "2026-08-25T15:00:00Z", created="2026-08-12T07:37:00Z",
           cancelled="2026-08-17T16:26:00Z", type_id=REVIEW)
mm_att = appt("MM2", "2026-08-18T18:20:00Z", created="2026-08-18T16:33:00Z", type_id=REVIEW)
mm3 = appt("MM3", "2026-08-26T07:00:00Z", created="2026-08-18T18:55:00Z",
           cancelled="2026-08-19T18:05:00Z", type_id=REVIEW)
results.append(check("came back and attended between two drops -> two rows",
                     p1.is_same_lapse(mm1, mm3, [mm1, mm_att, mm3]), False))
# Same-day rebook is a reschedule, not a reactivation.
sd1 = appt("SD1", "2026-06-22T13:10:00Z", created="2026-06-15T13:48:00Z",
           cancelled="2026-06-22T10:59:00Z", type_id=REVIEW)
sd2 = appt("SD2", "2026-06-25T14:20:00Z", created="2026-06-22T15:00:00Z",
           cancelled="2026-06-24T10:25:00Z", type_id=REVIEW)
results.append(check("rebooked the SAME day as the cancellation -> same lapse",
                     p1.is_same_lapse(sd1, sd2, [sd1, sd2]), True))


def lapse_row(a, pid="MF"):
    return {"patient": "P", "appointment_type": "t", "appointment_date": a["starts_at"],
            "_patient_id": pid, "_appt": a,
            "_appointment_type_id": p1.id_from_link(a["appointment_type"])}

got = p1._dedup_same_lapse([lapse_row(mf_ia), lapse_row(mf_rv)], {"MF": mf})
results.append(check("same run keeps ONE row — the cancelled follow-up",
                     [r["_appt"]["id"] for r in got], ["MF2"]))
got = p1._dedup_same_lapse([lapse_row(mf_ia)], {"MF": mf}, {"MF": {"MF2"}})
results.append(check("IA row is not re-added when the follow-up row is already in the sheet",
                     got, []))
got = p1._dedup_same_lapse([lapse_row(gm2, "GM")], {"GM": [gm_ia, gm1, gm2]}, {"GM": {"GM1"}})
results.append(check("a genuine second drop after a reactivation is still written",
                     len(got), 1))


# ---- 13. Duplicate sweep keeps the team's work (2026-09-14) ----
print("\n13. Duplicate sweep merges notes into the surviving row:")
def sheet_row(**kw):
    r = [""] * len(p1.SHEET_COLUMNS)
    for k, v in kw.items():
        r[p1.SHEET_COLUMNS.index(k)] = v
    return r
kept_row = sheet_row(reactivation_status="leave", reactivation_notes="left VM MH 2/9/26",
                     actioned="see above", pulled_at="46261.29")
gone_row = sheet_row(reactivation_status="contact_attempted", martys_comments="reactivate",
                     actioned="26/8/26 - Whatsapp sent. SR", pulled_at="46258.29")
merged = p1._merge_human_cols(kept_row, [gone_row])
results.append(check("status stays on the kept row's decision", merged[2], "leave"))
results.append(check("comments carried over from the removed row", merged[4], "reactivate"))
results.append(check("'see above' pointer replaced by the note it pointed at",
                     merged[5], "26/8/26 - Whatsapp sent. SR"))
results.append(check("pending kept row takes the other row's status",
                     p1._merge_human_cols(sheet_row(reactivation_status="pending"),
                                          [sheet_row(reactivation_status="contact_attempted")])[2],
                     "contact_attempted"))


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
