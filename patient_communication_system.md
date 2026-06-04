# Elite Physiotherapy — Patient Communication System

**Drafted:** 2026-05-15 (overnight) · **Status:** Draft for Martin's review
**Companion docs:** `cliniq_apps_templates.md` (the *current* Cliniq Apps copy, as-is) · `Elite_Marketing_Replacement_Plan.md` (the build plan)

This is the **to-be** design: a full map of every point where Elite Physiotherapy talks to a patient, plus a finished, ready-to-load template for each one. It is built to sit between the automation in `~/cliniko-dropoffs/` and the intake forms in Cliniko, replacing Cliniq Apps.

---

## How to read this document

- **Part 1** — the journey map, the rules (channels, timing, variables, senders).
- **Part 2** — what the best clinics in the world do, with sources, and where this design beats them.
- **Part 3** — the templates themselves, stage by stage. Every template is final copy you can review and load.
- **Part 4** — the open questions only you can answer. ~10 items. None block reviewing the copy.

Every template uses British English, the new variable convention (`{first_name}`, not Cliniq Apps' `{FIRSTNAME}`), and is written to be **better than the current Cliniq Apps version** — each carries a "What's better" line so you can see the change at a glance.

---

## Part 4 first — decisions I made for you while you slept

You said "make judgment calls." I did. These are the ones worth knowing before you read the copy. Full list with reasoning is in Part 4 at the end.

1. **SMS never says "reply".** The sender ID `ElitePhysio` is one-way in the UK — patients physically cannot text back. Every current Cliniq Apps SMS that says "hit reply" is broken on the new stack. All SMS here route to **call `{clinic_phone}`** or **tap a link** instead.
2. **Cancellation/no-show outreach leads with rebooking, not a survey.** The current system fires a satisfaction survey at cancelled patients. Best practice (and common sense) is: get them rebooked first; only ask "was everything OK?" if they *still* don't rebook. I sequenced it that way.
3. **Detractor follow-up email comes from a person** (Sinead Rocks), not `noreply@`. Accountability matters most exactly when someone is unhappy.
4. **Discharge/cancellation NPS branches reuse the IA branch copy** with one `{context}` variable, rather than three more sets of near-identical templates. Less to maintain, no drift.
5. **Birthday and keep-in-touch default to email, not SMS.** Email is free; SMS costs ~£0.045 each. Sending ~2,000 birthday/reactivation SMS a year is ~£90 for no measurable gain over email on a non-urgent message.

---

# PART 1 — The communication architecture

## 1.1 The journey map

```
  NEW PATIENT BOOKS
        │
        ▼
  ┌─────────────────── STAGE 1: BOOKING & ONBOARDING ───────────────────┐
  │  Booking confirmed   → SMS (+15 min)  + Email (+15 min)              │
  │  Welcome             → Email (+1 h)                                  │
  │  Pre-assessment form → delivered in booking email; Cliniko Form      │
  │  Form not done?      → Reminder SMS + Email (48 h before appt)       │
  │  (optional) "How to get the most from physio" → Email (2 days before)│
  └──────────────────────────────────┬──────────────────────────────────┘
                                      ▼
  ┌─────────────────── STAGE 2: PRE-APPOINTMENT ────────────────────────┐
  │  What to expect      → Email (48 h before)                          │
  │  Appointment reminder→ SMS  (24 h before)                           │
  └──────────────────────────────────┬──────────────────────────────────┘
                                      ▼
                              APPOINTMENT ATTENDED
                  ┌───────────────────┴───────────────────┐
                  ▼                                       ▼
        IA / Review attended                    Appointment missed/cancelled
                  │                                       │
                  ▼                                       ▼
  ┌──── STAGE 3: POST-APPOINTMENT NPS ────┐   ┌─ STAGE 5: CANCELLATION / DNA / DNR ─┐
  │  Survey  → SMS (+15 m) + Email (+2 h) │   │  Cancelled → rebook SMS (+1 h)      │
  │           + nurture Email (+1 d)      │   │             + rebook Email (+1 d)   │
  │                                       │   │  No-show   → SMS (+15 m)            │
  │  Patient opens Tally form, scores 0-10│   │             + Email (+1 h, +1 d)    │
  │      ├ 9-10 Promoter → Google review  │   │  Still no rebook → feedback survey  │
  │      ├ 7-8  Passive  → "what's a 9?"  │   │  Did-not-rebook  → IADNR nudge email │
  │      └ 0-6  Detractor→ callback + alert│   │  Deep win-back   → Manual 1&D email │
  │  Branch follow-ups sent after submit  │   │  30 days on      → CNA/DNA follow-up │
  └───────────────────────────────────────┘   └─────────────────────────────────────┘
                  │
                  ▼  (course of treatment completes)
  ┌─────────────────── STAGE 4: DISCHARGE ──────────────────────────────┐
  │  Well done / discharge → Email (+15 m)                              │
  │  Discharge NPS survey  → SMS (+15 m) + Email (+2 h)  [same branches] │
  │  30 days post-discharge→ Email — promoter variant / passive variant │
  └──────────────────────────────────┬──────────────────────────────────┘
                                      ▼
  ┌─────────────────── STAGE 6: REACTIVATION / KEEP IN TOUCH ───────────┐
  │  90 days since last appt, no future booking  → Email                │
  │  180 days                                    → SMS                  │
  │  12 months                                   → Email (final win-back)│
  └──────────────────────────────────┬──────────────────────────────────┘
                                      ▼
  ┌─────────────────── STAGE 7: ALWAYS-ON LIFECYCLE ────────────────────┐
  │  Birthday        → Email, on the day                                │
  │  Referral ask    → folded into the promoter pathway                 │
  └─────────────────────────────────────────────────────────────────────┘
```

## 1.2 Channel & timing principles

| Principle | Rule | Why |
|---|---|---|
| **SMS is one-way** | Never write "reply" in an SMS. Use "call `{clinic_phone}`" or a link. | `ElitePhysio` alphanumeric sender ID cannot receive replies in the UK. |
| **SMS = short & urgent** | One 160-character segment wherever possible. Confirmations, reminders, time-sensitive nudges. | 98% open rate, read within 5 min. Cost is per segment — keep it to one. |
| **Email = warm & detailed** | Welcome, what-to-expect, discharge, reactivation, anything explanatory. | Free, room to personalise, can carry the relationship. |
| **Confirm within 15 minutes** | Booking confirmation goes out fast. | Patients booked-and-confirmed within 15 min attend ~18% more often. |
| **Survey while it's fresh** | NPS survey within hours; never later than ~48 h. | Recall and response rate both fall off a cliff after 2 days. |
| **One ask per message** | Don't bury a survey link inside a rebooking message. | Two asks halve the response to both. |
| **Close the loop** | Every detractor: acknowledge < 24 h, callback, internal alert. | Full closed-loop converts 40-60% of detractors to passive/promoter next cycle. |

## 1.3 Variable reference

All templates use these. The Python `marketing/` module populates them; Cliniko/Tally hidden fields carry the rest.

| Variable | Example | Source |
|---|---|---|
| `{first_name}` | Sarah | Cliniko patient record |
| `{clinic_name}` | Cookstown | Cliniko → location. Current: Cookstown, Maghera. Future: Omagh, Armagh |
| `{clinic_phone}` | 028 8644 0995 | `config.py` per clinic |
| `{clinic_address}` | 133 Moneymore Road, Cookstown, BT80 9UU | `config.py` per clinic |
| `{practitioner_name}` | Daire | Cliniko appointment → practitioner first name |
| `{appointment_date}` | Mon 18 May | Cliniko appointment |
| `{appointment_time}` | 2:30pm | Cliniko appointment |
| `{appointment_type}` | Initial Assessment | Cliniko appointment |
| `{survey_link}` | tally.so/r/… (+ hidden fields) | `marketing/tally_url.py` |
| `{form_link}` | Cliniko pre-assessment form URL | Cliniko Forms (form still to be built) |
| `{booking_link}` | https://linktr.ee/ElitePhysiotherapy | `config.py` |
| `{manage_booking_link}` | (no separate link — templates route changes to `{clinic_phone}`) | — |
| `{google_review_url}` | Cookstown / Maghera review URL | `config.py` per clinic |
| `{exercise_library_link}` | https://patient.thegotoclinichub.com/index.php | `config.py` |
| `{context}` | "your first appointment" / "your treatment with us" | set by flow (IA vs discharge) |

**Locked facts** (from project memory — verified against the plan): Cookstown phone `028 8644 0995`; address `133 Moneymore Road, Cookstown, BT80 9UU`; Google review URLs — Cookstown `https://g.page/r/CfpgA6cxZez1EAE/review`, Maghera `https://g.page/r/Cccza5z-M6UtEAE/review`.

## 1.4 Sender reference

All routine clinic email sends from **`info@elitephysiocookstown.co.uk`** — the address the front desk monitors — so any patient reply (a rebooking question, a query) lands where it can be actioned. Only the deliberately personal emails use a named address.

| Template group | SMS from | Email from | Reply-to |
|---|---|---|---|
| Booking, reminders, surveys, lifecycle, reactivation | `ElitePhysio` | Elite Physiotherapy `<info@elitephysiocookstown.co.uk>` | `info@elitephysiocookstown.co.uk` |
| Detractor follow-up | `ElitePhysio` | **Sinead Rocks** `<sinead@elitephysiocookstown.co.uk>` | `sinead@…` |
| Detractor / passive internal alert | — | system → `sinead@elitephysiocookstown.co.uk` | — |
| 30-day post-discharge follow-up | — | **Sinead Rocks** `<sinead@…>` | `sinead@…` |
| Manual 1&D deep win-back | — | **Martin Loughran** `<martin@elitephysiocookstown.co.uk>` | `martin@…` |

Rule of thumb: routine email = the clinic front desk (`info@`). Anything emotional or accountable (someone's unhappy, someone's drifted away) = a named human.

---

# PART 2 — What the best clinics in the world do, and how we beat them

A benchmark of current best practice in physiotherapy and healthcare patient communication, with sources, and the specific design decision we took to go one better.

### Appointment reminders & no-shows
**Benchmark:** SMS reminders cut no-shows 30-50% (98% open rate vs ~50% for phone calls). A *three-touch* engagement model (confirm → remind → follow-up) reduces no-shows 28-32% within 60 days. Confirming within 15 minutes of booking lifts attendance ~18%.
**We beat it by:** confirming in <15 min on **two** channels at once (SMS + email), then a 48 h "what to expect" email and a 24 h SMS — a genuine multi-touch sequence, not a single reminder. The "what to expect" email removes attendance barriers (parking, what to bring, what to wear) that a bare reminder never addresses.

### Patient journey & onboarding
**Benchmark:** Sending intake forms *before* arrival reduces wait time and improves the onboarding experience; condition-relevant educational content during onboarding builds the relationship and outcomes.
**We beat it by:** delivering the Cliniko pre-assessment form *inside* the booking confirmation email (step 1, while motivation is highest), chasing it only if undone, and adding an optional "How to get the most from physio" educational email 2 days before the first visit — so the patient arrives informed, not cold.

### Closed-loop NPS
**Benchmark:** Survey within 48-72 h. Close the loop in three steps — alert the owner < 24 h, structured follow-up with a timeline, re-survey within 60 days. Clinics doing all three convert 40-60% of responding detractors to passive/promoter.
**We beat it by:** surveying at **+15 min / +2 h** (far inside the window), routing by score automatically, alerting Sinead within ~60 seconds (not 24 h), and giving the detractor a *named human + a callback* — not an automated apology. We deliberately do **not** re-survey detractors (decision locked in the plan: avoid re-opening a bad feeling — the callback is the recovery moment). The internal passive alert also feeds Sinead's weekly review, so 7-8s are coached, not ignored.

### Reactivation / win-back
**Benchmark:** A light sequence beats a single message — e.g. email at 3 months, SMS at 4, email at 6. Sequenced campaigns reactivate 10-15% of lapsed patients vs 5-8% for one-shot. A 5% retention lift raises profit 25-95%; reactivating a patient is 5-25× cheaper than acquiring one. Always include a direct booking link.
**We beat it by:** a true 90 / 180 / 365-day sequence, each touch a *different* channel and angle (helpful check-in → short nudge → genuine win-back), every one carrying a one-tap `{booking_link}`. And it is *triggered by data we already have* — the drop-off automation already knows who has no future booking — so it is precise, not a blast.

### Tone after a missed appointment
**Benchmark:** Check in after a missed appointment "with curiosity, not frustration."
**We beat it by:** the no-show copy never mentions the fee as a threat — it leads with "things happen", offers the waiver, and makes rebooking effortless. The did-not-rebook nudge is curious ("did we answer everything for you?"), not chasing.

**Sources:**
- [No-shows shouldn't derail your PT practice — PT Everywhere](https://www.pteverywhere.com/media/no-shows-shouldnt-derail-your-pt-practice)
- [Minimizing Physical Therapy No-Shows: 7 Proven Methods — Plus Physio](https://www.plusphysio.com/blog/minimizing-physical-therapy-no-shows-7-proven-methods)
- [AI Patient Engagement 2026: How Clinics Reduce No-Shows by 30% — Neuwark](https://neuwark.com/blog/ai-patient-engagement-reduce-no-shows-conversational-ai-2026)
- [How to Increase Patient Retention in a Physiotherapy Clinic — PhysioCare PMS](https://physiocarepms.com/blogs/increase-patient-retention-physiotherapy-clinic/)
- [Touch-Points in Patient Journey — MedLaunch](https://medlaunch.health/blogs/patient-experience/touch-points-in-patient-journey/)
- [Patient Onboarding: From First Visit to Forever — Harris CareTracker](https://harriscaretracker.com/patient-onboarding-from-first-visit-to-forever/)
- [How to win back lapsed patients — splose](https://splose.com/resources/the-handover/how-to-win-back-lapsed-patients-without-overloading-your-to-do-list)
- [Patient Reactivation Campaigns for Healthcare — Roving Health](https://www.rovinghealth.com/articles/patient-reactivation-campaigns-healthcare-lapsed-patients)
- [Healthcare NPS Survey Best Practices — Lobbie Institute](https://www.lobbie.com/institute/healthcare-net-promoter-score-nps-survey-best-practices)
- [NPS Detractor Follow-Up Best Practices — Qualaroo](https://qualaroo.com/blog/how-to-follow-up-with-an-nps-survey/)
- [NPS in Healthcare — Zonka Feedback](https://www.zonkafeedback.com/blog/nps-in-healthcare-and-patient-satisfaction)

---

# PART 3 — The templates

Format for each: trigger, timing, channel, sender, (subject), body, and — for SMS — an estimated rendered length. SMS aims for one 160-character segment.

---

## STAGE 1 — Booking & Onboarding

### 1.1 Booking confirmation — SMS
- **Trigger:** any appointment booked (new or returning patient)
- **Timing:** within 15 minutes of booking
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, you're booked at Elite Physio {clinic_name}: {appointment_date} {appointment_time} with {practitioner_name}. Check your email for what to do before your visit. Questions? Call {clinic_phone}
```
- **Length:** ~150 chars rendered (1 segment).
- **What's better than current:** Cliniq Apps has no booking confirmation at all — the patient's first contact was the reminder. This closes the highest-impact gap in the journey.

### 1.2 Booking confirmation — Email
- **Trigger:** any appointment booked · **Timing:** within 15 minutes
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** You're booked in, {first_name} — here's what happens next

**Body:**
```
Hi {first_name},

Your appointment is confirmed:

  📅  {appointment_date} at {appointment_time}
  📍  Elite Physiotherapy {clinic_name}, {clinic_address}
  👤  With {practitioner_name}, {appointment_type}

One thing to do before you come in
To make the most of your first visit, please complete a short
pre-assessment form. It takes about 5 minutes and tells your
physiotherapist exactly how your problem is affecting you — so
you spend your appointment getting help, not filling in paperwork.

  ▶  Complete your pre-assessment form: {form_link}

What to expect
We'll send you a short guide before your visit, plus a reminder
of your time. If anything changes, just call us on {clinic_phone}
and we'll happily move things around.

We're looking forward to meeting you.

Elite Physiotherapy {clinic_name}
{clinic_phone}
```
- **What's better than current:** delivers the intake form at the moment of highest intent (right after booking), instead of relying on a separate reminder. One clear next step.

### 1.3 Welcome email
- **Trigger:** new patient's first appointment booked · **Timing:** +1 hour after booking
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Welcome to Elite Physiotherapy, {first_name}

**Body:**
```
Hi {first_name},

Welcome to Elite Physiotherapy, and thank you for choosing us.
You have other options, and we don't take it lightly that you
picked us to help you get back to living free from pain and
doing the things that matter most to you.

Here's what you can expect from our team:

1.  We treat the cause, not just the site of the problem.
    Easing your pain quickly matters — but our real job is to
    find why it happened, so it doesn't keep coming back.

2.  We'll progress you safely, all the way through.
    Pain often eases well before the problem is fully resolved.
    The biggest mistake we see is people stopping too soon. We'll
    guide you through the full plan so it lasts.

3.  We support you between sessions.
    You'll get your movement plan after each visit so you always
    know what to do next.

Before your first visit
If you haven't already, please complete your short pre-assessment
form — it helps us hit the ground running:

  ▶  {form_link}

Any questions at all before you come in, just reply to this email
or call us on {clinic_phone}. We're glad you're here.

Warm regards,
The team at Elite Physiotherapy {clinic_name}
```
- **What's better than current:** the Cliniq Apps Welcome Letter is 427 words in one dense block. This keeps all three promises but is scannable in 20 seconds, fixes the original's grammar slips ("you have other option"), and ends with one clear action.

### 1.4 Pre-assessment form reminder — SMS
- **Trigger:** pre-assessment form not completed · **Timing:** 48 h before the appointment
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, before your appointment on {appointment_date} please take 5 mins to complete your pre-assessment form so we can help you faster: {form_link}
```
- **Length:** ~130 chars + link (1 segment).
- **What's better than current:** the Cliniq Apps "Pre Ax form reminder" runs ~2 segments and has two typos ("what your the underlying", "which has sent to you"). This is one segment, clean, with the link in the message (the original had no link).

### 1.5 Pre-assessment form reminder — Email
- **Trigger:** form still not completed 24 h after the SMS · **Timing:** ~24 h before the appointment
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** A quick form before we see you, {first_name}

**Body:**
```
Hi {first_name},

We're looking forward to seeing you on {appointment_date} at
{appointment_time}. There's one short thing left to do.

Your pre-assessment form takes about 5 minutes and means your
physiotherapist already understands your problem before you walk
in — so the whole appointment is spent on you, not paperwork.

  ▶  Complete it here: {form_link}

If you've already done this, thank you — please ignore this email.
Any trouble with the form? Just call us on {clinic_phone}.

See you soon,
Elite Physiotherapy {clinic_name}
```
- **What's better than current:** explains *why* the form matters (less wait, more help) rather than just instructing — this lifts completion rates.

### 1.6 "How to get the most from physio" — educational email (optional)
- **Trigger:** new patient, IA upcoming · **Timing:** 2 days before the first appointment
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Getting the most from your physio — and what to do if you have a setback

**Body:**
```
Hi {first_name},

Before we see you, here's the one idea that helps our patients
recover fastest.

Recovery is a step-by-step climb, not a leap
We rebuild what your body can handle gradually — A to B to C —
not straight from A to E. The most common cause of a setback is
jumping ahead too quickly: the body reverts to old habits it
knows, and pain can flare.

If you have a flare-up, don't panic
A flare-up is not a sign of damage. It's a signal that a body
part was loaded a little too much, too soon. It almost always
settles within 24 hours. What helps:

  1. Relax — remind yourself you are safe.
  2. Breathe, and ease off the activity that triggered it.
  3. Refocus and follow your physio's advice.
  4. Keep gently doing your exercises, pain-free, if you can.

Often it's the worry around a setback that causes more trouble
than the setback itself. We'll be with you the whole way.

Any questions before your visit on {appointment_date}, just call
us on {clinic_phone}.

See you soon,
Elite Physiotherapy {clinic_name}
```
- **What's better than current:** the Cliniq Apps version is built almost entirely as an embedded image (editor word count: 3) — it breaks on email clients that block images, can't be read by screen readers, and won't transfer to the new stack. This is text-first: accessible, deliverable, and editable. Keep the progression graphic as an *optional* image below the text if you want it.

---

## STAGE 2 — Pre-Appointment

### 2.1 What to expect — Email
- **Trigger:** any appointment · **Timing:** 48 h before
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Your appointment on {appointment_date} — what to expect

**Body:**
```
Hi {first_name},

Your appointment is coming up:

  📅  {appointment_date} at {appointment_time}
  📍  Elite Physiotherapy {clinic_name}, {clinic_address}
  👤  With {practitioner_name}

A few things to make it easy:

  •  Arrive 5 minutes early so we can start right on time.
  •  Wear comfortable clothing you can move in — we may need to
     see the area we're treating.
  •  Bring any relevant scan results or referral letters.
  •  Parking is available on site.

Need to change your appointment? Just call {clinic_phone} — the
sooner you let us know, the sooner we can offer the slot to
someone who needs it.

See you soon,
Elite Physiotherapy {clinic_name}
```
- **What's better than current:** Cliniq Apps has no "what to expect" email — patients arrive unprepared. Removing practical barriers (clothing, parking, what to bring) is a proven no-show reducer.

### 2.2 Appointment reminder — SMS
- **Trigger:** any appointment · **Timing:** 24 h before
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, a reminder of your appointment at Elite Physio {clinic_name} tomorrow, {appointment_date} at {appointment_time} with {practitioner_name}. Need to change it? Call {clinic_phone}
```
- **Length:** ~155 chars rendered (1 segment).
- **What's better than current:** names the practitioner (warmer, "I have an appointment with Daire" sticks better than "an appointment"), and routes changes to a phone call instead of an unanswerable "reply".

---

## STAGE 3 — Post-Appointment (Initial Assessment) — NPS

The survey is one Tally form. The invite (SMS + email) sends the patient to it; the form scores them and shows the right screen; branch follow-ups are sent after submission.

### 3.1 NPS survey invite — SMS
- **Trigger:** IA attended · **Timing:** +15 minutes
- **Channel:** SMS · **Sender:** `ElitePhysio` · **trigger_type:** `ia`

**Body:**
```
Hi {first_name}, thanks for coming in to see {practitioner_name} today. How did we do? It takes 30 seconds and genuinely helps us improve: {survey_link}
```
- **Length:** ~125 chars + link (1 segment).
- **What's better than current:** the Cliniq Apps "IA satisfaction SMS" has **no link in it at all** — patients literally cannot score. This one carries the survey link, which is the entire point.

### 3.2 NPS survey invite — Email
- **Trigger:** IA attended · **Timing:** +2 hours
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>` · **trigger_type:** `ia`
- **Subject:** How did we do today, {first_name}?

**Body:**
```
Hi {first_name},

Thank you for coming in to see {practitioner_name} today at
Elite Physiotherapy {clinic_name}.

We're always working to give our patients the best possible
experience, and your honest opinion is the most useful thing we
have. Based on your visit today:

How likely are you to recommend us to a friend or family member?

  ▶  Tell us in 30 seconds: {survey_link}

Thank you — it really does shape how we look after every patient.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** the current email embeds the 0-10 scale natively in Cliniq Apps. On the new stack the score lives in Tally, so the email's job is simply one clear button. Sender name is now consistent ("Elite Physiotherapy") — the old one alternated between "Jacinta Monaghan" and "Elite Physiotherapy" across screenshots.

### 3.3 NPS survey nurture — Email
- **Trigger:** IA attended, no survey response yet · **Timing:** +1 day
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>` · **trigger_type:** `ia`
- **Subject:** 30 seconds, {first_name}? We'd love your feedback

**Body:**
```
Hi {first_name},

We don't want to pester you — just a gentle nudge in case
yesterday got busy.

If you have 30 seconds, we'd really value your feedback on your
visit to see {practitioner_name}:

  ▶  {survey_link}

If now isn't a good time, no problem at all. Thank you either way.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** Cliniq Apps sends one survey ask and stops. A single, polite nurture typically recovers a meaningful share of non-responders without being pushy.

### 3.4–3.8 Tally form screen copy

The form itself does the scoring and the branching. Screen copy to load into Tally:

**Welcome screen**
```
Hi {patient_name} 👋

Thank you for visiting Elite Physiotherapy {clinic_name} and
seeing {physio_name}.

One quick question:

How likely are you to recommend Elite Physiotherapy to a friend
or family member?

   0  —  Not at all likely        10  —  Extremely likely
```

**Promoter screen (score 9-10)**
```
That's wonderful to hear — thank you, {patient_name}! 🙌

Would you take 30 seconds to share that with others? A short
Google review genuinely helps people who are nervous or unsure
about getting help find their way to us.

        [  Leave a Google review  →  ]   ({google_review_url})

Thank you for being part of Elite Physiotherapy.
```

**Passive screen (score 7-8)**
```
Thank you, {patient_name}.

We aim for every patient to score us a 9 or 10 — so we'd really
like to know:

What would it have taken to make today a 9 or 10 for you?

   [ open text — optional ]
```

**Detractor screen (score 0-6)**
```
Thank you for being honest, {patient_name} — and we're sorry
your visit didn't meet the standard we aim for.

We take this seriously. Could you tell us what went wrong?

   [ open text ]

Would you like a member of our team to call you about this?

   ( ) Yes, please call me      ( ) No, thank you

   Best number to reach you:  [ phone — shown if "Yes" ]
```

**Thank-you screen (all paths)**
```
Thank you, {patient_name}.

Your feedback goes straight to our team and helps us look after
every patient better. We're grateful you took the time.

— Elite Physiotherapy
```
- **What's better than current:** the Maghera Google-review bug is fixed here — `{google_review_url}` is a per-clinic hidden field, so Maghera promoters reach the Maghera review page. Detractor capture and the callback request happen *in the form*, in the moment, instead of relying on a reply to an email.

### 3.5 Promoter follow-up — SMS
- **Trigger:** survey submitted, score 9-10 · **Timing:** +10 minutes after submission
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, thank you so much for your kind feedback! If you have a moment, a quick Google review helps others find the help they need: {google_review_url}
```
- **Length:** ~125 chars + link (1 segment).
- **What's better than current:** a second, frictionless route to the review for promoters who didn't tap through inside the form (in-the-moment click-through is always low).

### 3.6 Promoter follow-up — Email
- **Trigger:** survey submitted, score 9-10, no review detected · **Timing:** +1 day
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Thank you, {first_name} — one small favour?

**Body:**
```
Hi {first_name},

Thank you for your wonderful feedback after your visit — it
genuinely made our team's day.

Could we ask one small favour? It takes about a minute.

A lot of people are nervous or sceptical about whether physio
can help them. Seeing a real review from someone like you is
often the nudge they need to finally get help.

  ▶  Leave us a Google review: {google_review_url}

And if you know someone in pain who we could help, feel free to
pass our details on — or reply to this email with theirs and
we'll take good care of them.

Thank you for being part of Elite Physiotherapy.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** keeps the strong "sceptical people need a review to feel safe" framing from the current promoter email, folds in a soft referral ask, and — critically — `{google_review_url}` is per-clinic, so this no longer sends Maghera patients to the Cookstown page.

### 3.7 Passive follow-up — Email
- **Trigger:** survey submitted, score 7-8 · **Timing:** +1 day
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Thank you for your feedback, {first_name}

**Body:**
```
Hi {first_name},

Thank you for taking the time to give us your feedback after
your recent visit — and for being honest with your score.

We aim for every patient to leave us a 9 or 10. If anything at
all wasn't quite right, we'd genuinely like to hear it — just
reply to this email. We read every reply, and it's exactly how
we get better.

Thank you again,
Elite Physiotherapy {clinic_name}
```
- **What's better than current:** the patient already gave "what would make it a 9/10" *inside the Tally form*, so this no longer repeats that question (the current passive email asks it cold). It simply acknowledges and leaves the door open — and email *can* take a reply, unlike SMS.

### 3.8 Detractor follow-up — SMS
- **Trigger:** survey submitted, score 0-6 · **Timing:** +10 minutes after submission
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, thank you for your honest feedback — we're sorry your visit fell short. Sinead from our team will be in touch personally. You can also reach us on {clinic_phone}.
```
- **Length:** ~155 chars (1 segment).
- **What's better than current:** the current detractor SMS runs ~3 segments (3× the cost) and contains a typo ("what we would could have done"). It also asks the patient to "reply" — impossible on the new stack. This is one segment, names a real person, and sets the expectation of a call.

### 3.9 Detractor follow-up — Email
- **Trigger:** survey submitted, score 0-6 · **Timing:** +30 minutes after submission
- **Channel:** Email · **Sender:** **Sinead Rocks** `<sinead@elitephysiocookstown.co.uk>`
- **Subject:** I'm sorry, {first_name} — and thank you for telling us

**Body:**
```
Hi {first_name},

My name is Sinead, and my job at Elite Physiotherapy is to make
sure every patient gets the standard of care we promise.

Thank you for being honest in your feedback. I'm sorry your
recent visit didn't meet that standard — and I'd genuinely like
to put it right.

I'll be in touch personally within the next working day to
listen and understand what happened. If you'd rather reach me
first, just reply to this email or call {clinic_phone} and ask
for me.

We take this seriously, and we're grateful you gave us the
chance to make it right.

Kind regards,
Sinead Rocks
Operations Manager, Elite Physiotherapy
{clinic_phone}
```
- **What's better than current:** comes from a named, accountable person with a concrete timeline ("within the next working day") — the closed-loop "acknowledge + resolution timeline" step best practice requires. The current detractor email is unsigned and has no commitment to act.

### 3.10 Detractor internal alert — Email (to the team)
- **Trigger:** survey submitted, score 0-6 · **Timing:** immediate (~60 s)
- **Channel:** Email · **Sender:** system · **To:** `sinead@elitephysiocookstown.co.uk`
- **Subject:** 🔴 DETRACTOR — {patient_name} scored {score}/10 ({clinic_name})

**Body:**
```
A detractor response just came in. Please action the callback.

  Patient:        {patient_name}
  Score:          {score}/10
  Physio seen:    {physio_name}
  Clinic:         {clinic_name}
  Appointment:    {appointment_type} on {appointment_date}
  Trigger:        {context}

  Callback requested:  {callback_requested}
  Callback number:     {callback_number}

  What they told us:
  "{open_text}"

  Patient contact:  {patient_phone} · {patient_email}

Logged in: NPS — Detractor Tracker. Please update the resolution
status there once actioned.
```
- **What's better than current:** Cliniq Apps sends the patient an SMS + email and stops — no one inside the clinic is told. This puts the full context in front of Sinead within a minute, with everything she needs to make the call. (Plan also adds a parallel Slack ping — same payload.)

### 3.11 Passive internal alert — Email (to the team)
- **Trigger:** survey submitted, score 7-8 · **Timing:** immediate
- **Channel:** Email · **Sender:** system · **To:** `sinead@elitephysiocookstown.co.uk`
- **Subject:** 🟡 PASSIVE — {patient_name} scored {score}/10 ({clinic_name})

**Body:**
```
A passive response came in — for your weekly review (no urgent
action needed).

  Patient:      {patient_name}
  Score:        {score}/10
  Physio seen:  {physio_name}
  Clinic:       {clinic_name}
  Trigger:      {context}

  What would make it a 9 or 10:
  "{open_text}"

Logged in: NPS — Raw Data.
```
- **What's better than current:** net-new. Passives are the patients most easily won back to promoters — surfacing their "what would make it a 9" comments for Sinead's weekly review turns them into a coaching input instead of a number no one reads.

---

## STAGE 4 — Discharge

### 4.1 Discharge / well done — Email
- **Trigger:** course of treatment complete (last appt was a Review/Club follow-up, no future booking)
- **Timing:** +15 minutes after the final appointment
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Well done, {first_name} 🎉

**Body:**
```
Hi {first_name},

Well done — and congratulations on completing your treatment
with {practitioner_name} at Elite Physiotherapy.

You put in the work, and it paid off. A few things to help you
stay well from here:

  •  Keep up your movement plan. The exercises that got you
     here are the ones that keep you here — your plan stays in
     your online library: {exercise_library_link}
  •  Build back gradually. If something flares, don't panic —
     it's a signal, not a setback. Ease off, breathe, and return
     to your exercises pain-free.
  •  We're still here. If this problem returns, or a new one
     appears, you don't go to the back of the queue — just call
     {clinic_phone} and we'll look after you.

It's been a pleasure helping you. Look after yourself.

The team at Elite Physiotherapy {clinic_name}
```
- **What's better than current:** Cliniq Apps has no discharge email — patients simply stopped hearing from the clinic. A proper send-off ends the episode well and explicitly keeps the door open, which is itself a retention lever.

### 4.2 Discharge NPS survey — SMS
- **Trigger:** discharge · **Timing:** +15 minutes
- **Channel:** SMS · **Sender:** `ElitePhysio` · **trigger_type:** `discharge`

**Body:**
```
Hi {first_name}, congratulations on finishing your treatment with {practitioner_name}! How did we do overall? 30 seconds, and it really helps us: {survey_link}
```
- **Length:** ~135 chars + link (1 segment).
- **What's better than current:** the plan flags that the current discharge survey "reuses IA copy" — this is purpose-written for discharge ("finishing your treatment", "overall"), not a recycled first-visit message.

### 4.3 Discharge NPS survey — Email
- **Trigger:** discharge, no SMS response · **Timing:** +2 hours
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>` · **trigger_type:** `discharge`
- **Subject:** How was your experience with us, {first_name}?

**Body:**
```
Hi {first_name},

Now that you've completed your treatment with {practitioner_name}
at Elite Physiotherapy {clinic_name}, we'd love to know how we
did — across the whole journey, not just one visit.

How likely are you to recommend us to a friend or family member?

  ▶  Tell us in 30 seconds: {survey_link}

Thank you for trusting us with your recovery.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** distinct discharge framing; same Tally form, `trigger_type=discharge` so responses are reportable separately from IA scores.

> **Discharge branch follow-ups:** detractor / passive / promoter follow-ups reuse templates **3.5–3.11** with `{context}` = "your treatment with us". No separate copy — one set, maintained once. (See decision #4 in Part 4.)

### 4.4 30-day post-discharge follow-up — Promoter variant — Email
- **Trigger:** discharged 30-45 days ago, scored as promoter, no future booking
- **Timing:** +15 minutes once the 30-day window opens
- **Channel:** Email · **Sender:** **Sinead Rocks** `<sinead@…>`
- **Subject:** Just checking in, {first_name}

**Body:**
```
Hi {first_name},

This is Sinead from Elite Physiotherapy. {practitioner_name}
asked me to check in and see how you're keeping a month on from
finishing your treatment.

Hopefully you're feeling great and staying on top of things.
Your exercises are still in your online library if you'd like
them: {exercise_library_link}

If anything has flared up — or a new niggle has appeared — don't
wait for it to settle on its own. Just reply to this email or
call {clinic_phone} and we'll get you straight back in.

And if you know someone struggling with pain, we'd be glad to
help them too — feel free to pass on our details.

Take care,
Sinead Rocks
Elite Physiotherapy {clinic_name}
```
- **What's better than current:** keeps the warm Sinead voice and the rehab-library reference from the existing 30-day promoter email, but trims it from 242 words to something readable on a phone, and removes the slightly heavy review-and-referral stack (the review was already asked at discharge).

### 4.5 30-day post-discharge follow-up — Passive variant — Email
- **Trigger:** discharged 30-45 days ago, scored as passive (or no score), no future booking
- **Timing:** +15 minutes once the window opens
- **Channel:** Email · **Sender:** **Sinead Rocks** `<sinead@…>`
- **Subject:** Just checking in, {first_name}

**Body:**
```
Hi {first_name},

This is Sinead from Elite Physiotherapy — my role is to make
sure every one of our patients gets the help they need.

{practitioner_name} asked me to check in and see how you're
getting on a month after finishing your treatment.

Your exercises are still available in your online library:
{exercise_library_link}

If your symptoms have returned, or something new has come up,
please don't push through it — reply to this email or call
{clinic_phone} and we'll look after you.

And if there's anything we could have done better, I'd genuinely
like to hear it. Just hit reply.

Take care,
Sinead Rocks
Elite Physiotherapy {clinic_name}
```
- **What's better than current:** mirrors the existing two-variant approach (the clinic already segments the 30-day check-in promoter vs passive — good practice to keep), softer than the promoter version, no review ask, and explicitly invites the improvement feedback a passive is most likely to give.

---

## STAGE 5 — Cancellations, No-Shows & Did-Not-Rebook

Design note: this stage **leads with rebooking**. The satisfaction survey is a *later* touch, only if the patient still hasn't rebooked — so we never dilute the rebooking ask, and we only ask "was everything OK?" when the silence suggests it might not have been.

### 5.1 Cancellation — rebook — SMS
- **Trigger:** appointment cancelled, no future booking · **Timing:** +1 hour
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, we've cancelled your appointment as requested. When you're ready to get back on track, rebook here: {booking_link} or call {clinic_phone}. We're keen to see you finish strong.
```
- **Length:** ~150 chars + link (1 segment).
- **What's better than current:** the current CNA SMS only confirms the cancellation and gives a phone number. This adds a one-tap rebooking link (the single biggest lever on rebooking rate) and a reason to return.

### 5.2 Cancellation — rebook — Email
- **Trigger:** cancelled, still no future booking · **Timing:** +1 day
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Let's get you rebooked, {first_name}

**Body:**
```
Hi {first_name},

We noticed you cancelled your recent appointment and haven't
rebooked yet — and we don't want you to lose the progress you've
made.

Recovery works best without long gaps. The sooner you're back
in, the sooner you'll feel the benefit.

  ▶  Rebook in under a minute: {booking_link}
  ☎  Or call us on {clinic_phone}

If something came up, or you're not sure physio is still the
right step, just reply to this email — we'd rather hear from you
than have you struggle on alone.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** keeps the existing CNA email's "the clinic is busy, don't miss out" urgency but reframes it around *the patient's* progress, adds the booking link, and — because email can take replies — opens a conversation for anyone who's hesitating.

### 5.3 No-show (DNA) — SMS
- **Trigger:** appointment marked no-show · **Timing:** +15 minutes
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, we missed you at Elite Physio today. No problem — things happen. Rebook here: {booking_link} or call {clinic_phone} and we'll find a time that works.
```
- **Length:** ~135 chars + link (1 segment).
- **What's better than current:** the current DNA SMS says "Please call us to reschedule if you haven't already" — functional but cold. This leads with "things happen" (curiosity, not frustration) and gives the one-tap rebook link.

### 5.4 No-show (DNA) — Email
- **Trigger:** no-show, no rebook within ~1 h · **Timing:** +1 hour, then again +1 day if still no rebook
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** We missed you today, {first_name}

**Body:**
```
Hi {first_name},

It looks like you weren't able to make your appointment today —
that's OK, life happens.

Normally a fee applies for a missed appointment, but we'd much
rather see you back than charge you. Get in touch to rebook and
we'll happily waive it this time.

  ▶  Rebook here: {booking_link}
  ☎  Or call us on {clinic_phone}

The sooner we see you, the sooner we can get you feeling better.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** keeps the existing DNA email's generous "we'll waive the fee" offer (a genuinely good piece of patient care) but cleans up the copy and adds the booking link. The waiver stays a *carrot*, never a *threat*.

### 5.5 Did-not-rebook (IADNR) nudge — Email
- **Trigger:** attended an Initial Assessment, never booked a follow-up (the drop-off automation's `iadnr` type)
- **Timing:** ~3 days after the IA with no future booking
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Did we answer everything for you, {first_name}?

**Body:**
```
Hi {first_name},

It was good to meet you at your first appointment with
{practitioner_name}. We noticed you haven't booked your next
visit yet — so we wanted to check in.

Sometimes that's because everything felt clear and you're happy
to crack on. Sometimes it's because something held you back — a
question that wasn't fully answered, or you weren't sure the
plan was right for you.

Either way, we'd like to know:

  ▶  If you're ready to continue, book here: {booking_link}
  ☎  If you have a question first, call us on {clinic_phone} or
     just reply to this email — no pressure at all.

Your assessment is only the first step. The results come from
the plan that follows it, and we'd love to see you through it.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** net-new, and aimed squarely at the most valuable drop-off the clinic has — the patient who came once and vanished. It's curious, not chasing, and gives an honest off-ramp ("call with a question") rather than only a booking button.

### 5.6 Manual 1&D deep win-back — Email (sent by Martin)
- **Trigger:** detractor *or* a cancelled/no-rebooked patient who didn't respond to the standard flow — sent manually by Martin, or auto-drafted for his approval
- **Timing:** when Martin chooses (typically 1-2 weeks after the drop-off)
- **Channel:** Email · **Sender:** **Martin Loughran** `<martin@elitephysiocookstown.co.uk>`
- **Subject:** Did we get something wrong, {first_name}?

**Body:**
```
Hi {first_name},

My name is Martin and I'm the Head Physiotherapist here at Elite
Physiotherapy. My job is to make sure every patient who comes to
us gets the help they really need.

Can I ask you a genuine favour?

You came to see us recently and didn't book back in. In our
experience, that usually means something about the experience
wasn't right for you — and I'd really like to understand what.

Could you watch this 60-second video and answer the three
questions below? It'll take no more than two minutes, and it
would help me enormously.

  ▶  https://www.loom.com/share/2612c76c00b64a2591233e242b2be15d

  1. Did you have clarity on your problem, the plan, and what to
     do next?
  2. Did you have faith the plan would get you the result you
     wanted?
  3. Was there anything we could have done better — and what was
     the real reason you didn't book back in?

Just reply to this email. I read every response personally, and
I promise to take it on board.

Thank you,
Martin Loughran
Head Physiotherapist, Elite Physiotherapy
```
- **What's better than current:** this is the strongest piece of copy in the whole Cliniq Apps set — kept almost intact. Only changes: fixed the original typo ("want you needed to do next" → "what to do next"), tightened slightly, and confirmed it stays a *manual* send from Martin's own address (its power is that it's clearly from a real person, not automation).

### 5.7 30-day cancellation / no-show follow-up — Email
- **Trigger:** cancelled or no-showed 30-45 days ago, still no future booking
- **Timing:** +15 minutes once the 30-day window opens
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Still thinking of you, {first_name}

**Body:**
```
Hi {first_name},

It's been about a month since we last saw you, and we wanted to
check in.

If your problem has settled completely — that's brilliant, and
we're genuinely pleased for you.

But if it's still there, or it's crept back, please don't put up
with it. Pain that lingers usually means there's still something
to resolve, and the longer it's left, the harder it can be to
shift.

  ▶  Book a visit: {booking_link}
  ☎  Or call us on {clinic_phone}

We'd love to help you finish what you started.

Elite Physiotherapy {clinic_name}
```
- **What's better than current:** the plan lists separate 30-day DNA and 30-day CNA/DNR flows; the copy need is identical, so this is one template covering both — less to maintain. Honest tone: it openly allows "maybe you're better now", which builds trust and makes the message land rather than feel like a sales chase.

---

## STAGE 6 — Reactivation / Keep in Touch

Triggered off data the drop-off automation already holds (last appointment date, no future booking). A genuine sequence — different channel and angle each time.

### 6.1 90-day keep in touch — Email
- **Trigger:** 90 days since last appointment, no future booking, not currently in another flow
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** How are you keeping, {first_name}?

**Body:**
```
Hi {first_name},

It's been about three months since we last saw you at Elite
Physiotherapy, and we were thinking of you.

No agenda here — we just like to check in. How are you keeping?
Is everything still feeling good?

If it is, wonderful. If something has crept back, you know where
we are — and you won't be starting from scratch, because we
already know your history.

  ▶  Book whenever you're ready: {booking_link}
  ☎  Or call us on {clinic_phone}

Look after yourself,
Elite Physiotherapy {clinic_name}
```
- **What's better than current (net-new):** the first reactivation touch is a *check-in*, not a sell — it earns the right to the later, more direct nudges.

### 6.2 180-day keep in touch — SMS
- **Trigger:** 180 days since last appointment, no future booking, no response to the 90-day email
- **Channel:** SMS · **Sender:** `ElitePhysio`

**Body:**
```
Hi {first_name}, it's been a while! If any old aches have returned, we'd love to help you get on top of them. Book here: {booking_link} or call {clinic_phone}.
```
- **Length:** ~130 chars + link (1 segment).
- **What's better than current (net-new):** switches channel to cut through where email didn't, and stays short and warm — a nudge, not a pitch.

### 6.3 12-month reactivation — Email
- **Trigger:** 12 months since last appointment, no future booking
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** It's been a year, {first_name} — how's that {body_area} of yours?

**Body:**
```
Hi {first_name},

It's been a year since we last saw you at Elite Physiotherapy —
which we hope means you've been feeling great.

But a year is also long enough for old problems to quietly
return, or new ones to set in. If anything is bothering you —
the thing we treated before, or something new — it's worth
getting ahead of it.

As a returning patient, you're never starting from zero with us.
We know your history, and we can pick up quickly.

  ▶  Book your visit: {booking_link}
  ☎  Or call us on {clinic_phone}

Whatever you decide, we wish you well — and we're here if you
need us.

Elite Physiotherapy {clinic_name}
```
- **What's better than current (net-new):** the final, most direct win-back. `{body_area}` in the subject (the drop-off automation already categorises body area) makes it specific — "how's that knee of yours?" lands far harder than a generic check-in. Use a plain fallback subject if body area is unknown.

---

## STAGE 7 — Always-on Lifecycle

### 7.1 Birthday — Email
- **Trigger:** patient's birthday (active patients; respect marketing-consent status — see Part 4)
- **Channel:** Email · **Sender:** Elite Physiotherapy `<info@…>`
- **Subject:** Happy birthday, {first_name}! 🎉

**Body:**
```
Hi {first_name},

Everyone at Elite Physiotherapy wanted to wish you a very happy
birthday.

We hope your year ahead is a healthy, active and pain-free one —
and if we can help you keep it that way, you know where we are.

Have a wonderful day,
The team at Elite Physiotherapy {clinic_name}
```
- **What's better than current (net-new):** deliberately has **no booking link and no sell** — it's a pure goodwill touch. A birthday message that tries to sell physio is worse than no message at all. Email, not SMS, keeps it free.

### 7.2 Referral ask
Not a standalone send. The referral ask is folded into the **promoter follow-up email (3.6)** — the moment a patient is happiest is the right (and only) time to ask. A standalone referral campaign can be added later if you want one; flagged but not built here.

---

# PART 4 — Decisions (resolved with Martin, 2026-05-16)

All ten open questions are now answered and applied to this document.

1. **Email sender addresses — RESOLVED.** All routine clinic email sends from `info@elitephysiocookstown.co.uk` — the address the front desk monitors — so patient replies land where they can be actioned. Named addresses retained for the personal sends: detractor follow-up and 30-day follow-up from `sinead@`, Manual 1&D from `martin@`. Sender reference (1.4) and every template header updated.
2. **`{exercise_library_link}` — RESOLVED.** Set to `https://patient.thegotoclinichub.com/index.php`.
3. **`{form_link}` — the pre-assessment form — OUTSTANDING ACTION.** The Cliniko Form itself is not built yet ("to do"). Templates 1.2 / 1.4 / 1.5 are ready and reference `{form_link}`; they go live once the form exists and has a shareable URL. **This is the one open action on Martin.**
4. **`{booking_link}` — RESOLVED.** Set to `https://linktr.ee/ElitePhysiotherapy`. Note: this is a Linktree hub, so a patient taps through it to reach Cliniko booking — one extra click vs a direct Cliniko booking URL. Fine to launch with; swapping in a direct Cliniko booking URL later would shave that click if rebooking conversion proves to matter.
5. **Marketing consent — RESOLVED.** Consent is captured. Stage 6/7 sends (reactivation, birthday) proceed; the system still honours Cliniko's `do_not_contact` flag before any send.
6. **Google review gating — RESOLVED.** Keep current behaviour — only promoters (9-10) are routed to Google. No change.
7. **Cancellation flow — RESOLVED.** Rebook-first sequencing confirmed (satisfaction survey only if the patient still hasn't rebooked).
8. **Shared NPS question & branch copy — RESOLVED.** The standard 0-10 "how likely to recommend" question, and the detractor/passive/promoter follow-up copy, are shared across the IA, discharge and cancellation surveys — distinguished only by `trigger_type` / `{context}`. One set, maintained once.
9. **Educational email (1.6) — RESOLVED.** Template stays text-first; Martin will add the progression graphic as an image himself (below the text).
10. **NPS reporting — RESOLVED (requirement clarified).** NPS is to be reported at **three cuts**: (a) whole company, (b) per clinic — Cookstown and Maghera now, Omagh and Armagh once they open, (c) per physiotherapist individually. Build implication: the Tally `clinic_name` hidden field must support all current and future clinics, and the `NPS — Dashboard` tab needs company / per-clinic / per-physio breakdowns. This supersedes the old "Net Promoter %" ambiguity — the metric wanted is the NPS score itself, sliced these three ways.

---

## Summary — what this gives you

- **37 templates** across 7 journey stages, every one final copy ready to load.
- Every gap in the current Cliniq Apps set closed: **no booking confirmation, no what-to-expect email, no discharge email, no reactivation sequence, survey SMS with no link, "reply"-to-SMS that can't work, the Maghera review-URL bug** — all fixed.
- A benchmark showing where this beats world-class practice, with sources.
- Copy that is consistent in voice, British English, one-segment SMS to control cost, and personalised with data the system already holds.

Next step: all 10 questions are resolved (Part 4) and applied. This is now the content spec for `marketing/templates/`. The only outstanding dependency is the **Cliniko pre-assessment form** (Part 4, item 3) — once Martin builds it and it has a shareable URL, Stage 1 can go live.
```
