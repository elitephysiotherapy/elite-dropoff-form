# Elite Physiotherapy — NPS Survey Form (Tally) Build Spec

**Drafted:** 2026-05-16 · **Status:** Draft for Martin's review
**Companion docs:** `patient_communication_system.md` (the survey is linked by templates 3.1/3.2, 4.2/4.3) · `Elite_Marketing_Replacement_Plan.md` (§4) · `pre_assessment_form.md`

This is the build spec for the single Tally form that powers the whole NPS system. It's written so building it is mostly transcription — every page, block, option and logic rule is specified. Part 8 is the click-by-click.

---

## 1. Overview — how this form works

**One form, used everywhere.** The same Tally form serves the survey at every trigger point — after an Initial Assessment, after discharge, and after a cancellation/no-show. Which trigger it was is carried in a hidden field (`trigger_type`) so responses can be reported separately, but patients all see one form.

**It branches on the score.** The patient answers one 0–10 question. Tally's conditional logic then routes them:
- **9–10 → Promoter** screen → Google review
- **7–8 → Passive** screen → "what would make it a 9 or 10?"
- **0–6 → Detractor** screen → "what went wrong?" + callback request

**It carries patient context invisibly.** The Python system builds the form URL with hidden fields (patient ID, name, physio, clinic, etc.). The patient never sees or types these — they ride along in the link and come back in the webhook, so every response is matched to the right patient and clinic with no manual entry.

**On submit, it fires a webhook** to the Render server, which writes the response to the NPS sheet and — for detractors and passives — alerts Sinead.

```
 Python builds URL with hidden fields
        │
        ▼
 Patient taps {survey_link} in SMS/email
        │
        ▼
 PAGE 1 — Welcome + 0–10 score
        │
        ├─ 9–10 ─▶ PAGE 2 Promoter ─▶ Google review
        ├─ 7–8  ─▶ PAGE 3 Passive  ─▶ Thank you
        └─ 0–6  ─▶ PAGE 4 Detractor ─▶ Thank you
        │
        ▼
 Webhook ─▶ Render /tally/webhook ─▶ NPS sheet + Slack/email alert
```

---

## 2. Hidden fields

In Tally these are **"Hidden fields"** (added from the form builder). Each is populated from the URL the Python system builds. Field names must match exactly — they become URL parameters and webhook keys.

| Hidden field name | Carries | Used for |
|---|---|---|
| `patient_id` | Cliniko patient ID | Matching the response to the patient on the webhook |
| `patient_name` | First name | Personalising the form ("Thank you, Sarah") |
| `patient_email` | Email | Backup contact / matching |
| `patient_phone` | Mobile | Pre-fills the detractor callback number |
| `physio_name` | Physio first name | NPS-by-physio reporting |
| `clinic_name` | Cookstown / Maghera (later Omagh, Armagh) | NPS-by-clinic reporting |
| `appointment_date` | Date of the triggering appointment | Dedup / matching |
| `trigger_type` | `ia` / `discharge` / `cna` / `dna` | NPS-by-trigger reporting |
| `google_review_url` | The **clinic-specific** Google review URL | The promoter review link — this is what fixes the Maghera bug |

`google_review_url` values: Cookstown `https://g.page/r/CfpgA6cxZez1EAE/review` · Maghera `https://g.page/r/Cccza5z-M6UtEAE/review`. Python looks this up by clinic and bakes it into the URL — so a Maghera promoter can only ever be sent to the Maghera review page.

---

## 3. Form structure — page by page

Build it as **4 pages** plus a thank-you. Copy is final; `@field` means insert that hidden field via Tally's `@` mention.

### PAGE 1 — Welcome & score
**Block 1 — Text:**
```
Thank you for choosing Elite Physiotherapy @clinic_name 👋

We'd really value your honest feedback — it takes about 30 seconds
and helps us look after every patient better.
```
**Block 2 — Linear scale question** (this is the NPS score):
- Question: `How likely are you to recommend Elite Physiotherapy to a friend or family member?`
- Scale: **0 to 10**
- Left label: `Not at all likely` · Right label: `Extremely likely`
- Required: **Yes**
- Internal name: `nps_score` (used by the logic in Part 4)

### PAGE 2 — Promoter (score 9–10)
**Block 1 — Text:**
```
That's wonderful to hear — thank you, @patient_name! 🙌

Would you take 30 seconds to share that? An honest Google review
genuinely helps people who are nervous or unsure about getting
help find their way to us.
```
**Block 2 — Button / link:** label `Leave a Google review` → URL = `@google_review_url` (see Part 5 if Tally won't accept a variable here).
**Block 3 — Text:** `Thank you for being part of Elite Physiotherapy.`

### PAGE 3 — Passive (score 7–8)
**Block 1 — Text:**
```
Thank you, @patient_name.

We aim for every patient to score us a 9 or 10 — so we'd really
like to know one thing:
```
**Block 2 — Long answer question:**
- Question: `What would it have taken to make it a 9 or 10 for you?`
- Required: **No** (optional — don't force it)
- Internal name: `passive_feedback`

### PAGE 4 — Detractor (score 0–6)
**Block 1 — Text:**
```
Thank you for being honest, @patient_name — and we're sorry your
experience didn't meet the standard we aim for.

We take this seriously, and we'd like to put it right.
```
**Block 2 — Long answer question:**
- Question: `Could you tell us what went wrong?`
- Required: **No**
- Internal name: `detractor_feedback`

**Block 3 — Multiple choice question:**
- Question: `Would you like a member of our team to call you about this?`
- Options: `Yes, please call me` · `No, thank you`
- Required: **Yes**
- Internal name: `callback_wanted`

**Block 4 — Short answer question:**
- Question: `What's the best number to reach you on?`
- Default value / pre-fill: `@patient_phone`
- Required: **No**
- Internal name: `callback_number`
- **Conditional:** show this block only when `callback_wanted` = `Yes, please call me` (Part 4).

### PAGE 5 — Thank you (Passive & Detractor land here)
**Block 1 — Text:**
```
Thank you, @patient_name.

Your feedback goes straight to our team and helps us look after
every patient better. We're grateful you took the time.

— Elite Physiotherapy
```

---

## 4. Conditional logic (the branching)

Tally logic is free and built in the form editor. Set these rules:

**On Page 1, after the `nps_score` question — page jumps:**
- If `nps_score` is **less than or equal to 6** → jump to **Page 4 (Detractor)**
- If `nps_score` is **7 or 8** → jump to **Page 3 (Passive)**
- If `nps_score` is **greater than or equal to 9** → jump to **Page 2 (Promoter)**

**After Page 2 (Promoter)** → jump to end / submit (or redirect, Part 5).
**After Page 3 (Passive)** → jump to **Page 5 (Thank you)**.
**After Page 4 (Detractor)** → jump to **Page 5 (Thank you)**.

**On Page 4 — show/hide:**
- Show `callback_number` only if `callback_wanted` is `Yes, please call me`.

Order the pages 1→2→3→4→5 in the editor; the jumps above override the default top-to-bottom flow so each patient sees only their own branch.

---

## 5. Promoter → Google review routing

The promoter needs the **clinic-specific** review URL (`@google_review_url`). Two ways — use whichever your Tally account supports:

- **Method A — button (preferred):** the "Leave a Google review" button on Page 2 with its URL set to the `@google_review_url` hidden field. Test this when you build it — if Tally's button block won't accept a variable as the URL, use Method B.
- **Method B — conditional redirect:** in **Settings → Redirect on completion**, set the redirect URL to `@google_review_url`, conditional on `nps_score ≥ 9`. Promoters are sent straight to the review page on submit; passives/detractors see the Page 5 thank-you. (Note: Tally hides the thank-you page for anyone who is redirected — fine here, since for a promoter the redirect *is* the thank-you.)

**Belt and braces:** whichever you pick, the promoter follow-up SMS (template 3.5) and email (3.6) *also* carry the correct per-clinic `{google_review_url}`. So per-clinic accuracy is guaranteed even if a promoter doesn't click in the moment — the Maghera bug cannot recur.

---

## 6. Webhook setup

1. Publish the form.
2. Go to the form's **Integrations** tab → **Webhooks** → **Connect**.
3. Endpoint URL: `https://elite-dropoff-form.onrender.com/tally/webhook` (the existing Render server — a new `/tally/webhook` route is added to `server.py`).
4. Enable the **signing secret**. Tally then signs each request with a `Tally-Signature` header (SHA256). The Python webhook verifies it before trusting the payload — the same pattern `server.py` already uses for Slack.
5. The webhook payload delivers every answer **and** every hidden field. The Python side will:
   - parse `nps_score` → Promoter / Passive / Detractor;
   - append a row to `NPS — Raw Data` in the NPS & Marketing sheet;
   - if Detractor or Passive → send the internal alert email to Sinead (templates 3.10 / 3.11) and the Slack ping;
   - never expose patient data to anything outside Elite's existing tools.

---

## 7. The survey URL (for the Python build)

The Python system builds the link per patient. Shape:
```
https://tally.so/r/<FORM_ID>?patient_id=12345&patient_name=Sarah&patient_email=...
&patient_phone=...&physio_name=Daire&clinic_name=Cookstown&appointment_date=2026-05-14
&trigger_type=ia&google_review_url=https%3A%2F%2Fg.page%2Fr%2FCfpgA6cxZez1EAE%2Freview
```
- `<FORM_ID>` is the code in your published Tally URL — set it in `config.py`.
- Every value must be **URL-encoded** (note the `google_review_url` value above is encoded). This is the job of `marketing/tally_url.py`.
- This built URL is what fills `{survey_link}` in comms templates 3.1, 3.2, 4.2, 4.3.

---

## 8. Step-by-step: building it in Tally

1. Create a free Tally account (use a clinic email, e.g. `info@elitephysiocookstown.co.uk`).
2. **New form** → name it `Elite Physiotherapy — Feedback`.
3. **Add the hidden fields** (Part 2): in the form builder, add a Hidden field for each of the nine names. Spell them exactly.
4. **Build Page 1** — add the welcome Text block and the Linear scale question (0–10) per Part 3. Mark the scale required.
5. **Add Pages 2, 3, 4, 5** — one per Part 3 section. Add each block in order; set the question types (Long answer, Multiple choice, Short answer) and required/optional as specified.
6. **Set the conditional logic** (Part 4) — the three page-jumps from the score, the after-page jumps, and the show/hide on `callback_number`.
7. **Set up the promoter review route** (Part 5) — Method A or B.
8. **Style it** — add the Elite logo (`email_assets/logo.png`) as the form header; brand colour teal `#2A9EA7` for buttons.
9. **Publish**, then **connect the webhook** (Part 6) with the signing secret.
10. **Test:** open the published URL with `?nps_score=...` style hidden-field params for each clinic; submit a 10, an 8 and a 3; confirm each branch shows correctly and the webhook fires. (Until the Render route exists, you can point the webhook at a test endpoint like webhook.site to see the payload.)

---

## 9. Decisions / things to confirm

1. **Promoter routing** — when building, confirm whether Method A (button with variable URL) works in Tally; if not, use Method B. Either is fine.
2. **Form name shown to patients** — I've used "Elite Physiotherapy — Feedback". Change if you'd prefer.
3. **Tally free tier** — free covers unlimited forms and submissions; the paid tier mainly adds branding removal and extras. The plan estimates you'll be under any response cap. Confirm the free tier when you sign up.
4. **One form vs. per-clinic forms** — this spec uses **one** form for all clinics and triggers (clinic and trigger ride in hidden fields). That's the right call — one form to maintain, all reporting driven by the hidden fields. No per-clinic duplicates.

---

## Summary

- One Tally form, branching on a 0–10 score into Promoter / Passive / Detractor.
- Nine hidden fields carry patient + clinic context invisibly; `google_review_url` is per-clinic, which kills the Maghera review bug for good.
- Webhook to the existing Render server feeds the NPS sheet and the detractor/passive alerts.
- Part 8 is the click-by-click build; ~30–45 minutes in Tally.

**Next build steps after this:** the Google Sheet structure (`NPS — Raw Data` etc.), then the Python `marketing/` module, then the `/tally/webhook` route on Render.
