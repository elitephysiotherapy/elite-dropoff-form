# Elite Physiotherapy — Marketing Automation Replacement Plan

**Date:** 2026-05-14
**Goal:** Cancel Cliniq Apps (~£1,000–1,500/yr) and replace with owned infrastructure that lives inside the existing `~/cliniko-dropoffs/` project.
**Source docs:** `Elite_NPS_ClaudeCode_Brief.docx` (this folder) + 15 Cliniq Apps automation screenshots audited 2026-05-14.

---

## 1. Final scope

### Replicate in new system (13 flows)

| # | Cliniq Apps name | Trigger | Notes |
|---|---|---|---|
| 1 | IA Satisfaction | IA attended | NPS survey: SMS +15m, Email +2h, Nurture +1d |
| 2 | NPS Promoter → Google | Score 9–10 | **Consolidated into Tally form's promoter screen** — fixes Maghera URL bug |
| 3 | NPS Passive feedback | Score 7–8 | **Consolidated into Tally form's passive screen** |
| 4 | DNA Rebooker | No-show + no future appt | SMS +15m, Email +1h, Email +1d |
| 5 | CNA Rebooker | Cancelled + no future appt | SMS +1h, Email +1d |
| 6 | Discharge Satisfaction | Last appt was Review/Club FU + no future appt | SMS +15m, Email +2h. **Rename templates** — currently reuses IA copy |
| 7 | CNA NPS tracker | Cancelled appt + 15m | **Consolidated with #5** — one outreach, not two |
| 8 | DNA NPS tracker | No-show + 15m | **Consolidated with #4** — one outreach, not two |
| 9 | Detractor follow up | Score 0–6 | **Upgraded:** SMS + email to patient + new email alert to Sinead Rocks (Ops Manager) + callback request in form |
| 9b | Passive follow up (alert) | Score 7–8 | **NEW:** email alert to Sinead Rocks for weekly review (in addition to existing patient-facing passive email) |
| 10 | 30-day promoter follow-up | Promoter, 30–45d since last appt | Email +15m |
| 11 | 30-day DNA follow-up | No-show, 30–45d ago | Email +15m |
| 12 | 30-day CNADNR follow-up | Cancelled, 30–45d ago | Email +15m |
| 13 | Injection Therapy Day 14 + Day 28 | After Injection Therapy appt | Patient form emails (forms themselves move to Cliniko) |

### Migrate to Cliniko Forms (out of scope for new system)

| # | Cliniq Apps name | Reason |
|---|---|---|
| A | Onboarding (Pre-Ax form + SMS reminder + Welcome letter) | Cliniko Forms is now free — native intake replaces this |
| B | Injection Therapy Pre-form | Same — pre-procedure intake form lives in Cliniko |
| C | Ultrasound Scan pre-form (planned) | Same |

### Defer to separate workstream

| # | Cliniq Apps name | Reason |
|---|---|---|
| D | ACL Journey (10 touches over ~6 months) | Substantive content migration; tackle as Phase 2 after core build is stable |

### Net-new additions

| # | Flow | Trigger | Notes |
|---|---|---|---|
| N1 | Birthday email | Patient's birthday | Annual, all active patients |
| N2 | 90-day keep in touch | 90d since last appt + no future appt | Soft reactivation |
| N3 | 180-day keep in touch | 180d since last appt + no future appt | Final reactivation touch |

---

## 2. Architecture

**Path B (confirmed):** Extend the existing `~/cliniko-dropoffs/` project.

```
                    ┌──────────────────────────────────────────────┐
                    │  macOS launchd                               │
                    │  - 07:00 daily: phase1_fetch.py (drop-offs)  │
                    │  - every 10 min: marketing_poll.py (NEW)     │
                    └──────────────────────┬───────────────────────┘
                                           │
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │  Cliniko API (existing auth)                 │
                    │  Pulls: yesterday's appts (daily)            │
                    │        recent appts since last poll (10m)    │
                    └──────────────────────┬───────────────────────┘
                                           │
            ┌──────────────────────────────┼───────────────────────┐
            │                              │                       │
            ▼                              ▼                       ▼
   ┌────────────────┐         ┌──────────────────────┐  ┌────────────────────┐
   │ Drop-off logic │         │ Marketing logic (NEW)│  │ Lifecycle (NEW)    │
   │ (existing)     │         │ - IA satisfaction    │  │ - 30d follow-ups   │
   └────────┬───────┘         │ - Discharge sat      │  │ - 90/180d touch    │
            │                 │ - CNA/DNA reactivat. │  │ - Birthday         │
            │                 │ - Injection Day 14/28│  └─────────┬──────────┘
            │                 └──────────┬───────────┘            │
            │                            │                        │
            │                            ▼                        │
            │                ┌─────────────────────────┐          │
            │                │ Resend (email) +        │◄─────────┘
            │                │ Twilio (SMS)            │
            │                │ Tally URL builder       │
            │                └────────────┬────────────┘
            │                             │
            │                             ▼
            │                   ┌──────────────────┐
            │                   │  Patient mailbox │
            │                   │  + phone         │
            │                   └────────┬─────────┘
            │                            │ (clicks survey link)
            │                            ▼
            │                   ┌──────────────────┐
            │                   │  Tally form      │
            │                   │  (hidden fields  │
            │                   │   drive routing) │
            │                   └────────┬─────────┘
            │                            │ webhook on submit
            │                            ▼
            │                   ┌────────────────────────────┐
            │                   │  Render Flask (existing)   │
            │                   │  /tally/webhook (NEW)      │
            │                   │  - Update NPS Sheet row    │
            │                   │  - If detractor → Slack    │
            │                   └────────────┬───────────────┘
            │                                │
            ▼                                ▼
   ┌────────────────────────────────────┐  ┌──────────────────────────────────┐
   │  Sheet 1 — Drop-Off (existing)     │  │  Sheet 2 — NPS & Marketing (NEW) │
   │  ID: 1RC7QkH...                    │  │  ID: TBD (Phase 3)               │
   │                                    │  │                                  │
   │    • W/C [date] (drop-offs)        │  │    • NPS — Raw Data              │
   │    • IA Rebook Rate                │  │    • NPS — Dashboard             │
   │    • Monthly Summary               │  │    • NPS — Physio Breakdown      │
   │    • Weekly Snapshot               │  │      (Marty's coaching, private) │
   │    • Performance Dashboard         │  │    • NPS — Detractor Tracker     │
   │                                    │  │    • Marketing — Sent Log        │
   │  Audience: whole clinical team     │  │      (dedup tracker, hi-volume)  │
   │                                    │  │                                  │
   │                                    │  │  Audience: Marty + Sinead Rocks  │
   └────────────────────────────────────┘  └──────────────────────────────────┘
   Same Google service account writes to both. Two SPREADSHEET_ID env vars in config.
```

### Project structure

```
~/cliniko-dropoffs/
  phase1_fetch.py            existing (drop-off daily run)
  phase2.py                  existing (Cliniko client) — reuse
  slack_notifier.py          existing — extend for detractor alerts
  server.py                  existing — add /tally/webhook
  config.py                  existing — add marketing config section
  run_daily.sh               existing
  marketing_poll.sh          NEW — runs every 10 min via launchd

  marketing/                 NEW package
    __init__.py
    poller.py                main entry — what to send, to whom, now
    nps.py                   IA, Discharge, CNA, DNA NPS surveys
    reactivation.py          DNA Rebooker, CNA Rebooker
    lifecycle.py             30/90/180-day, birthday
    detractor.py             callback alert + Slack ping
    tally_url.py             build Tally URLs with hidden fields
    resend_client.py         email sender
    twilio_client.py         SMS sender
    sent_log.py              dedup tracker (queries Marketing — Sent Log tab)
    templates/
      ia_satisfaction.html
      ia_satisfaction.sms
      discharge_satisfaction.html
      discharge_satisfaction.sms
      ... (one pair per flow)

  Elite_NPS_ClaudeCode_Brief.docx
  Elite_Marketing_Replacement_Plan.md   (this doc)
```

---

## 3. Stack additions

| Tool | Purpose | Monthly cost | Setup time |
|---|---|---:|---|
| Resend | Transactional email | £0 (free <3k/mo) | 30 min (domain verification) |
| Twilio | SMS | ~£10–12 (~300 SMS/mo) | **1–5 business days** (UK A2P registration) |
| Tally | NPS survey form | £0 (free <200 responses/mo) | 30 min |
| Cliniko Forms | Intake forms (onboarding, pre-op) | £0 (newly free) | 1–2 hours (migrate templates) |

**Total new spend:** ~£10–12/month (~£120–150/year). Savings vs Cliniq Apps: £850–1,350/year.

---

## 4. Tally form spec

**One form, three conditional branches.** Trigger type and patient context arrive as hidden fields in the URL.

### Hidden fields (URL parameters)

| Field | Purpose | Populated by |
|---|---|---|
| `patient_id` | Cliniko patient ID (for matching on webhook) | Python URL builder |
| `patient_name` | Display name in form welcome | Python |
| `patient_email` | Backup contact channel | Python |
| `patient_phone` | For callback flow | Python |
| `physio_name` | Display on welcome screen | Python |
| `clinic_name` | Cookstown or Maghera | Python |
| `appointment_date` | For dedup matching | Python |
| `trigger_type` | `ia` / `discharge` / `cna` / `dna` | Python |
| `google_review_url` | Clinic-specific — **fixes Maghera bug** | Python (lookup by clinic) |

### Form screens

1. **Welcome** — "How likely are you to recommend Elite Physiotherapy to a friend or family member?" 0–10 scale. Subtext shows physio + clinic.
2. **Promoter (9–10)** — "Thank you. Would you take a moment to leave a review?" → button to `google_review_url`. Form ends.
3. **Passive (7–8)** — "We'd love to understand what would make this a 9 or 10?" Open text (optional). Form ends.
4. **Detractor (0–6)** — "We're sorry. Could you tell us what went wrong?" Open text. Yes/No callback. Phone field (conditional).
5. **Thank you** — all paths end here.

---

## 5. Data model — Google Sheets

**Two sheets, not one.** The existing drop-off sheet (`1RC7QkH...`) stays untouched. A new sheet — *Elite Physio — NPS & Marketing* — holds all 5 new tabs. Reasons: keep Physio Breakdown easily private to Marty, avoid 20-tab clutter in the dropoff sheet, isolate the high-volume Sent Log from team-facing dashboards.

### New tab: `NPS — Raw Data`

| Column | Notes |
|---|---|
| Date Sent | When survey dispatched |
| Patient ID | Cliniko ID (dedup key) |
| Patient Name | |
| Physio Name | |
| Clinic | Cookstown / Maghera |
| Trigger Type | ia / discharge / cna / dna |
| Score | 0–10 (blank until response received) |
| Category | Promoter / Passive / Detractor (auto-formula) |
| Open Text | Patient's written feedback |
| Callback Requested | Yes / No (detractors only) |
| Callback Number | Phone if provided |
| Status | Sent / Response Received / Callback Completed |
| Date Responded | Timestamp |

### New tab: `NPS — Dashboard`
- Overall NPS (rolling 90d + all-time)
- NPS by trigger type
- NPS by clinic
- NPS by physio
- Monthly trend line
- Response rate
- Detractor count + callback completion rate

### New tab: `NPS — Physio Breakdown`
- Per-physio NPS + category breakdown + comments (Marty's coaching use, not shared with clinical team).

### New tab: `NPS — Detractor Tracker`
- Filtered view: detractors only. Includes resolution status (Pending / Called / Resolved) + resolution notes (manual entry).

### New tab: `Marketing — Sent Log`
**Critical for dedup.** Every email and SMS sent by the marketing system writes one row here. Before sending any touch, the poller checks: "has patient X received touch Y for episode Z in the last N days?"

| Column | Notes |
|---|---|
| Timestamp | |
| Patient ID | |
| Flow Name | ia_satisfaction / cna_rebooker / 90d_keep_in_touch / etc. |
| Channel | sms / email |
| Episode Anchor Date | The appt that triggered this flow (dedup key) |
| Template Used | |
| Status | sent / failed / suppressed_unsubscribe |

---

## 6. Build sequence

**Approach: build-then-notice, no hard deadline.** Decision 2026-05-14: Martin will NOT give Cliniq Apps notice until the new system is fully built, tested, and proven bulletproof in shadow mode. Cliniq Apps 30-day notice is given AFTER we sign off internally. Cutover then happens 30 days after that.

**Why:** removes all deadline pressure. New system can be validated against real Cliniko data for as long as needed before any patient sees the difference. The cost of running Cliniq Apps for an extra 4–6 weeks during build is trivial (~£160) vs. the cost of going live with bugs.

**Critical path:** RESOLVED 2026-05-15. Twilio confirmed "ElitePhysio" is NOT on the MEF protected list, so NO sender ID registration is required — it can be used dynamically by simply setting the SMS "From" parameter to `ElitePhysio`. The anticipated 5–10 day approval wait no longer exists. Only remaining Twilio task: upgrade account from Trial to paid (add card) before live sending — a 5-minute job done just before testing.

**Working timeline (not a deadline):**
- Week 1–2: Foundations (Twilio submitted, Resend live, Cliniko Forms migrated, copy drafted)
- Week 3–4: Build (Python module, Tally form, Sheet 2, webhook)
- Week 5–6: Shadow mode (real Cliniko data, no actual sends) — validate every flow
- Week 7: SAFE_MODE testing (sends rerouted to Martin only) — validate channels end-to-end
- Week 8+: Optional early enablement of net-new flows that don't conflict with Cliniq Apps (Birthday, 90/180-day)
- **When Martin signs off:** give Cliniq Apps 30-day notice
- **30 days later:** flip MARKETING_LIVE=True; Cliniq Apps lapses

### Phase 0 — Today (5 min)
- **Fix Maghera Google review URL bug in current Cliniq Apps.** Replace the `all_locations` setting with two clinic-specific automations. Don't wait for the rebuild — Maghera has been losing reviews for who knows how long.

### Phase 1 — Foundations (parallel, 1–5 days)

These can all start today; Twilio is the longest pole.

| Task | Owner | Time |
|---|---|---|
| Twilio account + UK number + A2P registration | Martin | 1–5 business days (mostly waiting) |
| Resend account + DNS records for elitephysiocookstown.co.uk | Martin (DNS) + Claude (config) | 30 min + DNS propagation |
| Cliniko Forms migration: Pre-Ax, Welcome letter, Injection pre-form, Ultrasound pre-form | Martin | 2–3 hours in Cliniko UI |
| Confirm Google Review URLs are correct for both clinics | Martin | 1 min |

### Phase 2 — Tally form (30 min)
- Build single Tally form with hidden fields + 0–10 scale + conditional logic (promoter / passive / detractor screens).
- Martin builds in browser; Claude provides exact click-by-click spec.

### Phase 3 — Sheet structure (5 min)
- Claude generates Apps Script. Martin pastes once, all 5 new tabs created with formulas.

### Phase 4 — Python marketing module (1–2 days, Claude)
- `marketing/poller.py` — entry point called every 10 min
- `marketing/nps.py` — IA, Discharge, CNA, DNA flows
- `marketing/reactivation.py` — DNA + CNA rebookers
- `marketing/lifecycle.py` — 30/90/180-day, birthday
- `marketing/detractor.py` — Slack alert
- `marketing/sent_log.py` — dedup
- `marketing/resend_client.py` + `twilio_client.py`
- `marketing/templates/` — all email + SMS copy (Claude drafts from current Cliniq Apps copy; Martin reviews)

### Phase 5 — Render webhook (2 hours, Claude)
- Add `/tally/webhook` to `server.py`
- Score parsing + Sheet update + detractor Slack alert

### Phase 6 — Launchd schedule (15 min)
- Add `marketing_poll.sh` running every 10 minutes
- Reuses existing log dir structure

### Phase 7 — Testing (1 day)
- `SAFE_MODE = True` flag: every send is rerouted to Marty's email + phone only, prefixed `[TEST]`
- Run for 24h, validate each trigger type, each touch, each timing
- Confirm dedup works (no double-sends)

### Phase 8 — Go-live (Day 1)
- Flip `MARKETING_LIVE = True` in `config.py`
- Monitor logs + Slack for 2 weeks
- **Do not cancel Cliniq Apps yet** — run both in parallel for the first week to compare

### Phase 9 — Cancel Cliniq Apps (Day 14)
- After 2 weeks stable, pause Cliniq Apps automations one at a time
- Cancel subscription at end of billing period

### Phase 10 — ACL Journey migration (separate workstream, defer)
- Tackle after core marketing replacement is stable
- ~10 templates × 6 months of cadence to migrate

---

## 7. Dedup and idempotency (the trickiest bit)

**Problem:** if `marketing_poll.py` runs every 10 minutes and an IA was attended at 14:00, the poller will see it at 14:10, 14:20, 14:30, etc. We must not send four SMS.

**Solution:** before sending any touch, query `Marketing — Sent Log` tab:
- Is there a row with this `patient_id` + this `flow_name` + this `episode_anchor_date` in the last 30 days?
- If yes → skip silently
- If no → send and immediately append row to log

Episode anchor = the date of the appointment that triggered the flow. Patient could have 5 IAs over 3 years; each one is its own episode and gets its own surveys.

---

## 8. Decisions (locked 2026-05-14)

1. ~~30-day flow for detractors?~~ **DECIDED: No.** Leave detractors alone after the immediate detractor flow. Avoid re-triggering bad feeling; the Sinead Rocks callback is the recovery moment.

2. ~~Tally vs Cliniko Forms for the NPS survey?~~ **DECIDED: Tally.** Cliniko Forms confirmed no conditional/branching logic per their docs (cliniko.com/features/health-records/patient-forms/ + help.cliniko.com/en/articles/3945156). Cliniko Forms remains in scope for linear intake forms (Pre-Ax, Welcome, Injection pre-form, Ultrasound pre-form) where no branching is needed.

3. ~~Detractor alert destination?~~ **DECIDED: Email to Sinead Rocks (Ops Manager) `sinead@elitephysiocookstown.co.uk` for BOTH passives (7–8) AND detractors (0–6).** Promoters (9–10) trigger no alert — their action is leaving a Google review. Marty is NOT cc'd; Ops Manager owns this workflow.

4. **Email "from" name and address.** Recommendation: `Elite Physiotherapy <noreply@elitephysiocookstown.co.uk>` with reply-to set to `reception@elitephysiocookstown.co.uk`. **Pending Martin confirmation.**

5. ~~Email + SMS copy~~ **DECIDED: Martin screenshots current Cliniq Apps templates; Claude reviews and improves.** New flows (Birthday, 90/180-day) drafted from scratch by Claude.

8. ~~Same Sheet or split?~~ **DECIDED: Split into two sheets.** Existing dropoff sheet untouched. New sheet *Elite Physio — NPS & Marketing* holds all 5 new tabs. Same service account writes both.

6. **Patient consent — has booking language covered SMS via Twilio specifically?** UK A2P registration will ask. **Pending Martin confirmation.**

7. ~~Slack vs email alert~~ See decision #3.

---

## 9. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Twilio A2P rejection | Medium | High (no SMS until resolved) | Submit clean campaign description ASAP; have email-only fallback ready |
| Tally response → patient match failure | Low | Medium (orphan responses) | Include `patient_id` as hidden field; webhook matches on ID |
| Email deliverability poor | Low–Med | Medium | Resend with proper SPF/DKIM/DMARC; monitor bounce rate weekly |
| Sheet race conditions | Very Low | Low | Single Python process, no concurrent writers |
| Cliniq Apps still firing during overlap week | Medium | Low (patient gets 2 surveys) | Schedule Cliniq Apps pause in advance; communicate to team |
| Marketing flow sends to unsubscribed patient | Low | High (regulatory) | Honour Cliniko's `do_not_contact` flag; check before every send |

---

## 10. Cost summary

**Cliniq Apps actual spend last year: £1,950** (confirmed by Martin 2026-05-14, higher than the brief's earlier £1,000–1,500 estimate).

**Twilio cost recalculated** from current pricing ($0.056/SMS UK outbound, free alphanumeric sender ID) × audited flow volume of ~420 SMS/month (~5,000/yr).

| Item | Old (Cliniq Apps) | New |
|---|---:|---:|
| Cliniq Apps subscription | £1,950/yr | £0 |
| Resend (email) | — | £0 (free <3k/mo; you'll be at ~1.5k) |
| Twilio SMS (~5,000 SMS/yr UK) | — | ~£220/yr (range £175–£285) |
| Alphanumeric Sender ID "ElitePhysio" | — | £0 (free per Twilio docs) |
| Tally NPS form | — | £0 (free <200 responses/mo) — *may tip to ~£250/yr if volume grows* |
| Cliniko Forms (intake) | (bundled in Cliniq Apps) | £0 (now free) |
| Render Flask server | (existing) | £0 incremental |
| **New system total** | **£1,950/yr** | **£220–£470/yr** |
| **Annual saving** | | **£1,480–£1,730/yr** |

Even in the heavy-volume / paid-Tally scenario, the system pays for itself in ~3 months and saves >£1,400/yr every year after.

---

## 11. What good looks like

**At Go-Live + 2 weeks:**
- Every patient who attends an IA receives SMS at +15min and email at +2h
- Every cancelled / no-show patient with no rebook is reached out to within hours
- Every promoter sees the right clinic's Google Review URL (Maghera bug fixed)
- Every detractor triggers a Slack ping to Marty + Sinead within 60 seconds of submitting
- Dashboard tab shows live NPS, response rate, per-physio breakdown
- Cliniq Apps subscription cancelled at end of billing period
- Zero double-sends, zero missed sends in the log

**At Go-Live + 90 days:**
- ACL Journey migrated in (Phase 10)
- Birthday + 90/180-day flows live and measured
- NPS trend visible across 3 months
- Detractor close-loop callback rate measurable
