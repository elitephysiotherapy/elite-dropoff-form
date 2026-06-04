# Elite Physiotherapy — Pre-Assessment Form

**Drafted:** 2026-05-16 · **Status:** Draft for Martin's review
**Companion docs:** `patient_communication_system.md` (the form is delivered by template 1.2 / chased by 1.4 & 1.5) · `Elite_Marketing_Replacement_Plan.md`

Your overnight ask: research what the best clinics in the world put in a pre-assessment form, analyse Elite's current one, and give a step-by-step build — or do it for me. This document does all four. Part 3 is the finished form, written question-by-question so building it in Cliniko is pure data entry.

---

## How to read this

- **Part 1** — what world-class clinics put in a pre-assessment form, with sources.
- **Part 2** — line-by-line analysis of your current Cliniq Apps form.
- **Part 3** — the recommended Elite Pre-Assessment Form, every question ready to build.
- **Part 4** — step-by-step build instructions for Cliniko Forms.
- **Part 5** — how it links to the comms system and your clinical workflow.

**Updated 2026-05-16:** Martin provided screenshots of the live Cliniq Apps "Pre Ax Form" (saved in `~/Desktop/pre ax questionnaire screenshots/`). Part 2 is now a full line-by-line analysis of the real form — no longer an inference.

---

# PART 1 — What the best clinics put in a pre-assessment form

A pre-assessment (intake) form captures the patient's history, symptoms, functional limitations and goals **before** the hands-on appointment. Best practice across leading physiotherapy and healthcare practices:

### 1. It's sent digitally, 24–48 h before the appointment
Pre-appointment digital delivery cuts on-site paperwork handling by ~85% and — the real prize — gives the clinician time to *read it before the patient walks in*. The appointment starts on the problem, not the clipboard.

### 2. It captures these domains
Leading intake forms consistently cover:

- **Demographics & contacts** — name, DOB, address, phone, email, occupation, GP, emergency contact, and *how they heard about you*.
- **Presenting complaint** — what the problem is, where, which side, when and how it started, and whether it's getting better, worse or staying the same.
- **Pain assessment** — location, intensity on the 0–10 Numeric Pain Rating Scale (current / best / worst in 24 h), and quality (sharp, dull, aching, burning, throbbing, numbness, pins-and-needles).
- **Aggravating & easing factors** — what makes it worse, what relieves it.
- **Functional impact** — effect on daily activities, work, sleep, sport and hobbies; level of independence.
- **Red-flag screening** — unexplained weight loss, night pain/sweats, bowel/bladder changes, saddle numbness, progressive weakness, cancer history, recent trauma, fever/infection, plus cardiac/neuro screen.
- **Past medical history** — conditions, surgery, medications, allergies.
- **Prior investigations & treatment** — scans/imaging done, other treatment tried.
- **Beliefs, expectations & goals** — what the patient thinks is wrong, what worries them, what they want to get back to. This is increasingly treated as *essential*, not optional — it shapes engagement and outcomes.
- **Consent** — to treatment, to data handling (GDPR), and to communications.

### 3. It is consent-compliant
In the UK, CQC regulations require evidence that a patient understood treatment before care begins. A signed intake form is that documentary proof. It's also the right place to capture GDPR data consent and — usefully for Elite — **marketing/communication consent**, which the new comms system needs for its reactivation and birthday flows.

### 4. It is concise and finishable
Best-in-class forms target ~5–8 minutes. Every question earns its place; free-text is used sparingly; checkboxes are preferred where they give cleaner data and faster completion.

### 5. The best ones feed the clinician's workflow directly
The strongest forms aren't generic — they're built so the patient's answers pre-populate the exact assessment template the clinician uses. That is the single biggest lever, and it's where Elite can go beyond the benchmark (see Part 2).

**Sources:**
- [Physiotherapy Intake Form Template — Jotform](https://www.jotform.com/form-templates/physiotherapy-intake-form)
- [Physical Therapy Intake Form — Pabau](https://pabau.com/templates/physical-therapy-intake-form/)
- [Physical Therapy Patient Intake Form Guide — SPRY](https://www.sprypt.com/blog/pt-intake-form-guide)
- [General Physiotherapy Assessment — Physiopedia](https://www.physio-pedia.com/General_Physiotherapy_Assessment)
- [Getting started with secure patient forms — Cliniko Help](https://help.cliniko.com/en/articles/3945156-getting-started-with-secure-patient-forms)
- [Create patient form templates — Cliniko Help](https://help.cliniko.com/en/articles/3953842-create-patient-form-templates)

---

# PART 2 — Line-by-line analysis of your current form

## 2.1 What your current form contains

The current form is the Cliniq Apps **"Pre Ax Form"**. It has four parts:

**Patient details** (standard fields, synced to your practice-management software):
- *Included & required:* First name, Last name, Date of birth, Email, Street address, City, Post code, Mobile phone
- *Included, optional:* Title, State, Country, Home phone, Occupation
- *Switched OFF:* Emergency contact, Referring doctor, Referral source

**Body chart** — patients sketch on a front/back body image: RED = the area they're concerned about, BLUE = other areas of concern, GREEN = previous injuries.

**Clinical questions** (pulled from your Cliniko treatment-note template "Pre Ax Questionnaire"; answers copy into patient notes on submit):
1. The most important thing you want from your first visit
2. The current issue you'd like help to solve
3. What caused it — one-off or gradual, and anything else around that time
4. Other past injuries or medical issues
5. How the pain affects daily life — difficult positions / movements / activities
6. Other stressors contributing (sleep, stress, work, home, sport)
7. Other treatment tried, and whether it worked
8. Pain relief / medication being taken
9. Activities you need to get back to
10. Dream goal — what you want to get back to doing
11. What you believe is causing it / what others have said
12. Anything else you'd like to tell us
13. Red-flag tick list — 10 grouped items (generally unwell; dizziness/fainting/speech/swallowing/double vision; toileting changes; saddle pins & needles/numbness; heart/BP/circulation/pacemaker; osteoporosis/anticoagulants/steroids; cancer/diabetes/epilepsy/pregnant; recent major ops/unexpected weight loss; rheumatoid arthritis; COPD/asthma/respiratory)
14. How did you hear about us — 12 radio options

**Settings:** signature pad on; privacy-policy consent question on (links to a Google Drive privacy document); a PDF copy of each submission saved; results saved to treatment notes; delivered by email from "Elite Physiotherapy <info@…>", subject "Action Required - Important Pre Assessment Information".

## 2.2 What it does well — keep these

Honestly, this is a **strong form** — better than expected before seeing it:
- **The 12 clinical questions are genuinely good.** They already capture cause, beliefs, goals, stressors, daily impact, prior treatment and medication — the subjective heart of your Ax sheet. The wording is warm and plain. The new form reuses most of it.
- **The body chart is excellent** and does real clinical work — the red/blue/green system separates the primary complaint, secondary areas and old injuries at a glance.
- **A red-flag screen already exists** and is patient-completed.
- **It already feeds the record** — connected patient-detail fields update the PMS, and answers copy into treatment notes.
- **Consent, signature and a PDF audit copy** are already in place.

## 2.3 Content gaps vs best practice

- **No structured pain measurement.** No 0–10 scale (current / best / worst in 24 h), no pain *quality* (sharp, dull, burning, pins & needles), no *pattern* (constant vs intermittent, time of day). This is the biggest single content gap — it's the one objective baseline a patient can give you, and you'd want it to track change across an episode.
- **Onset, side and trajectory aren't discrete.** "When did it start", "which side", and "getting better / worse / same" are only answerable inside free-text Q3. Discrete fields make them scannable and reportable.
- **Emergency contact is switched off.** For a hands-on clinical service it should be on.
- **No explicit treatment consent.** There's a privacy-policy consent and a signature, but not an explicit "I consent to assessment and treatment" line. CQC-style best practice wants that stated plainly.
- **No marketing/communication consent.** The form captures privacy consent only. The new communications system (reactivation, birthday flows) needs a clear, dated marketing opt-in — and the pre-assessment form is exactly where to capture it.
- **Work and sleep impact aren't asked directly** — they sit inside Q5's free text.

## 2.4 What moving to Cliniko Forms will COST you — read before migrating

The migration to Cliniko Forms is **not purely an upgrade.** Three current features are at risk:

1. **The body chart.** Cliniko's body chart is a clinician treatment-note tool; Cliniko's *patient* forms are not known to offer a patient-sketchable body chart. **Confirm this in your account.** If it can't be done, it's a genuine feature loss — and your red/blue/green sketch is good clinical kit. Mitigation: the body-area checklist (form question 3.2) plus free text — workable, but less rich. This is the single biggest reason to check Cliniko's capability before committing.
2. **Auto-pull from a treatment-note template.** Today the questions are sourced from your "Pre Ax Questionnaire" treatment-note template and copy back into notes automatically. In Cliniko Forms you rebuild the questions in the form itself; "connected questions" cover patient-record fields and the submission saves to the file, but the tight template link isn't identical.
3. **No conditional logic.** Already known and designed around — the new form is linear.

None are deal-breakers, but the body chart genuinely matters. If Cliniko patient forms can't do it, the options are: (a) accept the checklist, (b) keep the body chart as a clinician step — the physio completes it in the treatment note from the patient's description, or (c) reconsider whether a different forms tool earns its place. Flagging it honestly — your call.

## 2.5 Verdict and design principle

Your current form is solid — this is **a refinement and a careful migration, not a rescue.** The new form should:
- **keep** the 12 clinical questions (lightly tidied), the red-flag screen, "how did you hear about us", signature and consent;
- **add** structured pain measurement, discrete onset / side / trajectory fields, explicit treatment consent, and marketing consent;
- **turn on** emergency contact;
- **be built to mirror the `Elite Ax 2024.pdf` sheet**, so the subjective side of every first assessment arrives pre-filled — the clinician spends the appointment on hands-on assessment and the objective findings only they can produce;
- **be checked against the body-chart limitation** before you commit to Cliniko Forms.

Part 3 is that form.

---

# PART 3 — The Elite Physiotherapy Pre-Assessment Form

### Cliniko Forms constraints (important — design respects these)
- **No conditional/branching logic.** Confirmed for Cliniko Forms. The form is therefore **linear** — one form, every patient, top to bottom. No region-specific question sets that appear/hide. (This is why the body-area question is a checklist + free text, not a branch.)
- **Field types** used below map to Cliniko's standard answer types: **Short text**, **Paragraph/long text**, **Checkboxes** (multi-select), **Multiple choice/radio** (single select), **Dropdown**, **Date**, **Signature**, and **Section** headings with descriptions. Where Cliniko's exact label differs in your account, pick the nearest match.
- **Connected questions** auto-update the patient record. Marked **[CONNECTED]** below — use Cliniko's "connected question" for these so the patient file populates itself.
- **No interactive body chart.** Your current Cliniq Apps form has a patient-sketchable body chart; Cliniko's patient forms are not known to offer one (see 2.4 — confirm in your account). Body area is therefore a checklist + free text (question 3.2), and the clinician marks the body chart on the Ax sheet from the patient's answer.
- **Privacy.** Mark the template **"Can only be viewed by practitioners"** — it contains clinical detail. Connected demographic questions still update the record, which reception can see; the clinical answers stay practitioner-only.

Legend: **[CONNECTED]** = connect to patient record · **[Type]** = Cliniko answer type.

---

### Section 0 — Welcome (information text, no answer)
```
Welcome to Elite Physiotherapy.

This short form takes about 5–8 minutes. Your answers go straight to
your physiotherapist before your appointment, so we can spend your
visit helping you — not filling in paperwork.

There are no right or wrong answers. If you're unsure of anything,
just give your best estimate, and we'll go through it together.
```

### Section 1 — About you
| # | Question | Type | Notes |
|---|---|---|---|
|1.1|First name|Short text|[CONNECTED]|
|1.2|Last name|Short text|[CONNECTED]|
|1.3|Date of birth|Date|[CONNECTED]|
|1.4|Mobile number|Short text|[CONNECTED]|
|1.5|Email address|Short text|[CONNECTED]|
|1.6|Home address|Paragraph|[CONNECTED]|
|1.7|Your occupation|Short text|[CONNECTED] — also feeds "Job" on the Ax sheet|
|1.8|GP name and practice|Short text|[CONNECTED] if your account has a GP field|
|1.9|Emergency contact — name|Short text|[CONNECTED]|
|1.10|Emergency contact — phone|Short text|[CONNECTED]|
|1.11|How did you hear about us?|Dropdown|Options: Google search · Google review · Friend or family · Social media · GP or consultant referral · Insurance company · Saw the clinic · Returning patient · Other|

### Section 2 — Consent
```
Section description: Please read and confirm the following before
your appointment.
```
| # | Question | Type | Notes |
|---|---|---|---|
|2.1|I consent to assessment and treatment by Elite Physiotherapy, and I understand the assessment will be explained to me and I can ask questions at any time.|Multiple choice|Options: I agree|
|2.2|I understand my information is held securely and used only to provide my care, in line with Elite Physiotherapy's privacy policy (GDPR).|Multiple choice|Options: I agree|
|2.3|How would you like us to contact you about appointments and your care? (tick all that apply)|Checkboxes|Options: Email · Text message (SMS) · Phone call|
|2.4|We'd also like to occasionally send helpful health tips, reminders and clinic news. You can opt out any time. Tick to opt in:|Checkboxes|Options: Yes, email me these · Yes, text me these — **feeds the comms system's marketing-consent requirement**|
|2.5|Signature|Signature|Confirms the above|
|2.6|Today's date|Date||

### Section 3 — Your main problem
```
Section description: Tell us about the main problem you're coming
to see us with.
```
| # | Question | Type | Notes |
|---|---|---|---|
|3.1|In your own words, what is the main problem you'd like help with?|Paragraph|→ Ax: Presenting Complaint|
|3.2|Where is the problem? (tick all areas that apply)|Checkboxes|Options: Head/jaw · Neck · Shoulder · Upper back · Elbow · Wrist/hand · Chest/ribs · Lower back · Hip/groin · Buttock · Thigh · Knee · Calf/shin · Ankle/foot · Other|
|3.3|Which side?|Multiple choice|Options: Left · Right · Both · Centre / not sided · Not applicable|
|3.4|When did it start?|Short text|e.g. "3 weeks ago", "since January" → Ax: HPC|
|3.5|How did it start? What were you doing?|Paragraph|→ Ax: Mechanism of Injury|
|3.6|Since it started, is it…|Multiple choice|Options: Getting better · Getting worse · Staying about the same · Varying day to day|
|3.7|Have you had this same problem before?|Multiple choice|Options: No, this is the first time · Yes — once before · Yes — it comes back regularly|
|3.8|If you've had it before, tell us about previous episodes and what helped.|Paragraph|→ Ax: Previous Injuries|

### Section 4 — Your pain and symptoms
| # | Question | Type | Notes |
|---|---|---|---|
|4.1|Pain right now (0 = none, 10 = worst imaginable)|Dropdown|0–10|
|4.2|At its WORST in the last 24 hours|Dropdown|0–10|
|4.3|At its BEST in the last 24 hours|Dropdown|0–10|
|4.4|How would you describe it? (tick all that apply)|Checkboxes|Options: Sharp · Dull/aching · Burning · Throbbing · Stiffness · Pins and needles · Numbness · Weakness · Clicking/catching|
|4.5|Is it…|Multiple choice|Options: There all the time · Comes and goes|
|4.6|When is it worst? (tick all that apply)|Checkboxes|Options: First thing in the morning · During the day · Evening · Night / wakes me from sleep · With activity · At rest|

### Section 5 — What makes it better or worse
| # | Question | Type | Notes |
|---|---|---|---|
|5.1|What makes it WORSE?|Paragraph|→ Ax: Aggravating factors|
|5.2|What makes it BETTER or eases it?|Paragraph|→ Ax: Easing factors|

### Section 6 — How it's affecting you
| # | Question | Type | Notes |
|---|---|---|---|
|6.1|What can't you do now, because of this problem, that you'd normally do?|Paragraph|→ Ax: Daily Pattern / function|
|6.2|Effect on your work|Multiple choice|Options: Not affecting work · Working but modified/struggling · Off work because of it · Not currently working|
|6.3|Effect on your sleep|Multiple choice|Options: Sleeping normally · Disturbs my sleep sometimes · Disturbs my sleep most nights|
|6.4|Effect on your sport, exercise or hobbies — and what specifically you can't do|Paragraph|→ Ax: Job/Sport/Training|

### Section 7 — Important health questions
```
Section description: These questions help us make sure physiotherapy
is safe and right for you. Please tick anything you have experienced.
If you're unsure, leave it unticked and we'll ask you in person.
```
| # | Question | Type | Notes |
|---|---|---|---|
|7.1|In the LAST FEW WEEKS, have you had any of the following? (tick any that apply)|Checkboxes|Options: Unexplained weight loss · Night pain or night sweats · Fever or feeling generally unwell · Recent significant injury or trauma · Problems controlling your bladder or bowel · Numbness around the saddle/groin area · Progressive weakness in arms or legs · Dizziness, blackouts or drop attacks · Double vision, difficulty swallowing or slurred speech · None of these|
|7.2|Do any of these apply to you? (tick any that apply)|Checkboxes|Options: Heart condition · High or low blood pressure · Circulation problems · Diabetes · Epilepsy · Rheumatoid arthritis · Osteoporosis · Currently pregnant · Fitted with a pacemaker · History of cancer · None of these|
|7.3|Is there anything you ticked above you'd like to explain?|Paragraph||

### Section 8 — Medical history and medications
| # | Question | Type | Notes |
|---|---|---|---|
|8.1|Please list any ongoing medical conditions.|Paragraph|→ Ax: Medical Hx|
|8.2|Please list any medications you currently take (including injections).|Paragraph|→ Ax: Drug Hx|
|8.3|Do you have any allergies?|Short text||
|8.4|Please list any operations or significant injuries you've had.|Paragraph|→ Ax: Previous Injuries / surgical history|

### Section 9 — Scans and other treatment
| # | Question | Type | Notes |
|---|---|---|---|
|9.1|Have you had any scans or imaging for this problem (X-ray, MRI, ultrasound, CT)?|Multiple choice|Options: No · Yes|
|9.2|If yes, what did they show? (you can also bring the report to your appointment)|Paragraph|→ Ax: Investigations|
|9.3|Have you had any other treatment for this problem (GP, physio, osteopath, injections, medication)?|Paragraph|→ Ax: Other Treatment|

### Section 10 — You and your lifestyle
| # | Question | Type | Notes |
|---|---|---|---|
|10.1|What does a typical day look like for you, including work demands?|Paragraph|→ Ax: Daily Pattern|
|10.2|What sport, exercise or training do you do — and how often?|Paragraph|→ Ax: Sport/Training|
|10.3|How would you describe your general activity level?|Multiple choice|Options: Mostly sedentary · Lightly active · Moderately active · Very active / training regularly|
|10.4|How do you sleep — usual position and how well?|Short text|→ Ax: Sleep|
|10.5|Is there anything stressful going on (work, family, life) that might affect your recovery? Only share what you're comfortable with.|Paragraph|→ Ax: Stressors|

### Section 11 — Your goals
| # | Question | Type | Notes |
|---|---|---|---|
|11.1|What do you think is going on with your body?|Paragraph|→ Ax: Beliefs|
|11.2|What worries you most about this problem?|Paragraph|→ Ax: Beliefs / fears|
|11.3|What's the ONE thing you most want to get back to doing?|Paragraph|→ Ax: Patient Goal|
|11.4|What would a successful outcome look like for you?|Paragraph|→ Ax: Patient Goal|
|11.5|Is there a date or event you're working towards?|Short text|→ Ax: time/sessions to success|

### Section 12 — Anything else
| # | Question | Type | Notes |
|---|---|---|---|
|12.1|Is there anything else you'd like your physiotherapist to know before your appointment?|Paragraph||

**That's 12 sections, ~55 questions — most of them taps, not typing. Realistic completion time: 6–8 minutes.** If you want it shorter, the trimmable sections are 10 and 11 (could merge), and 8.4 could fold into 3.8. I'd keep all of Sections 3, 4 and 7 — they carry the most clinical value.

---

# PART 4 — Step-by-step: building it in Cliniko Forms

### A. Create the template
1. In Cliniko, go to **Settings → Templates → Patient form templates** (under the "Templates" heading).
2. Click **New patient form template**.
3. **Name:** `Pre-Assessment Form` (this name shows to patients — keep it clean).
4. Tick **"Can only be viewed by practitioners (contains private information)"** — this keeps the clinical answers practitioner-only. Connected demographic questions still update the patient record for reception.

### B. Build each section
For every section in Part 3:
5. Click **Add section**. Enter the **section title** (e.g. "Your main problem") and, where Part 3 gives one, the **section description**.
6. Within the section, click **Add question** for each row in the table.
7. For each question: type the question text exactly, then choose the **answer type** (Short text / Paragraph / Checkboxes / Multiple choice / Dropdown / Date / Signature).
8. For Checkboxes, Multiple choice and Dropdown questions, add each **option** listed in Part 3, one per line.
9. For the **[CONNECTED]** questions in Section 1 (and GP if available), use **"Add connected question"** instead of a plain question, and pick the matching patient-record field (First name, Last name, Date of birth, Phone, Email, Address, Occupation, Emergency contact, Referral source). This is what makes the form auto-fill the patient file.
10. Section 0 is **information only** — add it as a section with the welcome text in the description and no questions (or a single information block if your account offers one).
11. Save the template.

### C. Deliver the form to patients
Two ways — and they map to the comms system:
12. **Cliniko's own appointment emails:** add the patient-form **placeholder** to your Cliniko appointment confirmation/reminder email, and Cliniko inserts a personalised form link per patient.
13. **The new comms system (recommended, since it's replacing Cliniq Apps):** the booking-confirmation email (template **1.2**) and the form reminders (**1.4 / 1.5**) carry `{form_link}`. Decide whether `{form_link}` is (a) Cliniko's per-patient form link or (b) a single generic form URL. Per-patient is better — responses attach straight to the right record. Confirm which Cliniko exposes when you build it, and set `{form_link}` in `config.py` accordingly.

### D. Test before going live
14. Send the form to a test patient (or yourself). Complete it on a phone — that's how most patients will.
15. Check the connected questions wrote through to the patient record.
16. Confirm a practitioner can see the full submission and reception sees only the record fields.
17. Time yourself. If it runs much over 8 minutes, trim per the note at the end of Part 3.

### What I could not do for you
Cliniko form **templates are built in the Cliniko web UI** — they can't be created through the API or from this machine, so I can't literally click it into your account. Part 3 is written so that building it is pure transcription: every question, type and option is specified. Budget ~30–40 minutes to enter it. If you'd rather, reception can build it straight from Part 3.

---

# PART 5 — How this links to everything else

- **Comms system:** the booking confirmation email (`1.2`) delivers `{form_link}`; the reminders (`1.4` SMS, `1.5` email) chase it. Part 4, item 3 of `patient_communication_system.md` listed "build the Cliniko pre-assessment form" as the one outstanding action — **this document is that action's spec.**
- **Marketing consent:** questions 2.3 and 2.4 capture contact and marketing consent. This satisfies the comms system's Stage 6/7 (reactivation, birthday) consent requirement and gives you a clean, dated, signed record of it.
- **Clinical workflow:** every clinical question is tagged with the `Elite Ax 2024.pdf` field it feeds. Your physios open the patient file before the appointment and the entire subjective side of the assessment sheet is already answered.
- **CQC compliance:** Section 2 gives you dated, signed evidence of informed consent before care begins.

### Open question for you
- **`Elite Ax 2024.pdf` is currently a paper/PDF sheet.** Worth deciding separately whether the *clinician* assessment also becomes a Cliniko treatment-note template, so the patient's pre-assessment answers and the clinician's objective findings live in one digital record. Out of scope here — flagging it because the two forms are two halves of the same assessment.

---

## Summary

- **Part 1** — best-practice pre-assessment content, sourced.
- **Part 2** — analysis: your clinical model (the Ax 2024 sheet) is strong; the opportunity is to make the patient form *pre-fill it*, add structured pain capture, patient-led red-flag screening, consent, and auto-population of the patient record.
- **Part 3** — the full form: 12 sections, ~55 questions, ready to build.
- **Part 4** — step-by-step Cliniko build (~30–40 min of data entry).

Next step when you're back: review the questions, cut anything you don't want, then build it (or hand Part 3 to reception). Once it's live with a stable link, Stage 1 of the comms system can go live too.
