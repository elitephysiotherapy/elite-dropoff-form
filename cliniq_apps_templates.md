# Cliniq Apps — Current Message Templates (transcribed from screenshots)

Collected 2026-05-15 from Martin's Cliniq Apps screenshots. These are the CURRENT live templates. Claude will draft improved replacements for the new system from this set.

Notes:
- `{FIRSTNAME}` is Cliniq Apps' dynamic variable — our system uses `{first_name}`.
- Current SMS sender shows as an Australian number (+61…) — Cliniq Apps default. New system sends from `ElitePhysio`.
- Current emails sent from `info@elitephysiocookstown.co.uk` with Elite Physiotherapy logo header.

---

## DNA Text (SMS)
```
Hi {FIRSTNAME},
You missed your appt today at Elite Physiotherapy.
Please call us on 02886440995 to reschedule if you haven't already.
Thank You.
```

## DNA Email
**Subject:** It looks like you missed your appointment today!
**Body:**
```
Dear {FIRSTNAME},
It looks like you missed an appointment today!
Usually, a non-attendance fee applies for no-shows (but we hate doing things like that!).
We understand sometimes things happen, so we're able to waive the fee for you today if you get in touch and reschedule your appointment.
Just give us a call AS SOON AS POSSIBLE to reschedule your appointment.
Our contact number is 02886440995
Looking forward to hear from you.
Elite Physiotherapy
```

## CNA Rebooker (SMS)
```
Hi {FIRSTNAME},
We are sending this SMS to confirm your cancellation.
To re-book please contact us on 02886440995
Thanks, Elite Physiotherapy
```

## CNA Email
**Subject:** Do you still need to re-book?
**Body:**
```
Hi {FIRSTNAME},
It's been a few days since your cancellation and you haven't rebooked yet. The clinic is busy as the minute and we don't want you to miss out so please call us on 02886440995 or hit 'reply' to re-book your appointment.
Elite Physiotherapy
```

## CNA/DNA satisfaction tracker (SMS)
```
Hi, we are constantly trying to provide the best patient experience possible. Based on your experience in the clinic, we would greatly appreciate your honest opinion on how likely you would be to recommend us to your friends or family. Thank you, Marty & Julie
```
**Flags:** (1) Currently ~2 SMS long — over 160 chars. (2) No survey link visible in the body — the new version MUST include the Tally survey link. (3) Has an optional header image.

## CNA/DNA satisfaction tracker email
**Subject:** How did we do?
**Body:** (NPS survey email — Cliniq Apps embeds a 0–10 button scale natively)
```
Based on your recent appointment with us, how likely are you to recommend our clinic to a friend or colleague?
[0–10 scale: 0 = Not likely, 10 = Very likely]
```
**Branding:** survey button background #2A9EA7 (teal), text #FFFFFF. Logo header. Sender name inconsistent across screenshots ("Elite Physiotherapy" vs "Jacinta Monaghan") — new system will standardise to "Elite Physiotherapy". From: info@elitephysiocookstown.co.uk.
**Note:** In the new system the 0–10 scale lives in the Tally form, not the email — the email just needs a button linking to Tally.

## detractor follow up email
**Subject:** What would it have taken to have scored 9 or 10?
**Body:**
```
Hi {FIRSTNAME}

Thank you for letting us know through our survey that your recent visit to us didn't meet your expectations. Our goal is to deliver world class service so we take your feedback very seriously if our service is not quite right.

We would really appreciate your comments so we can change what we do and improve our service even more. We would appreciate if you could help us get better.

PLEASE REPLY TO THIS EMAIL letting us know WHAT WE WOULD HAVE NEEDED TO DO TO HAVE ACHIEVED A SCORE OF 9 or 10 on your recent visit. Alternatively we can arrange for one of the team to give you a call and have a chat in regards to what we could do better.

Kind Regards
[signature — screenshot truncated]
```
130 words.

## detractor sms
```
Hi {FIRSTNAME} We are always striving to improve our service. We are sorry your recent visit didn't match up to your expectations. We would greatly appreciate it if you could reply and let us know what we would could have done in your last visit to have achieved a 9 or 10 out of 10 on the survey. Thank you, Marty & Julie
```
**Flags:** (1) ~3 SMS long — 3× the cost per send. New version must be far tighter. (2) Typo in original: "what we would could have done". (3) In new system the closed-loop callback request happens in the Tally form, so this SMS just needs to point them somewhere or acknowledge — rework needed.

## passive nps follow up email
**Subject:** What would it have taken to have scored 9 or 10?
**Body:**
```
Hi {FIRSTNAME}

Thank you very much for your recent survey response. We pride ourselves on constantly trying to deliver the best client experience possible. We would greatly appreciate it if you could reply to this email and give us some honest feedback and let us know what we could have done for you to have scored us 9 or 10 out of 10 on the survey.

Thank you again

Marty & Julie
```
71 words.

## Manual 1&D Email FU
**Subject:** Did we do something wrong {FIRSTNAME}?
**From:** Martin Loughran <martin@elitephysiocookstown.co.uk> (note: personal, not info@)
**Body:**
```
Hi {FIRSTNAME},

My name is Martin and I'm the head physio at Elite Physiotherapy. My job is to make sure every patient that comes to us gets the help they really need.

Can you do me a massive favour, please {FIRSTNAME}?

You recently cancelled an appointment with us and didn't rebook.

We know this usually happens because your expectations were not met in some way.

Could you watch this 60-second video and answer the 3 following questions below.

I promise it'll take no more than 2 minutes of your time but will help me out massively.

https://www.loom.com/share/2612c76c00b64a2591233e242b2be15d

1. Did you have clarity on your problem, the treatment plan, and what you needed to do next?
2. Did you have faith that the treatment plan was going to help you achieve the goals you wanted to achieve from coming to see us?
3. Was there anything else we could have done to make your experience better and what was the real reason you cancelled?

I really appreciate your brutally honest feedback so we can be better.
```
174 words. "Manual" = sent manually, not automated. Contains a Loom video link. Original typo: "want you needed to do next" → "what".

## Initial Appointment Satisfaction (email)
**Subject:** Hey {FIRSTNAME} Quick Question
**Body:** NPS survey email — Cliniq Apps embeds the 0–10 scale natively.
```
Based on your recent appointment with us, how likely are you to recommend our clinic to a friend or colleague?
[0–10 scale: 0 = Not likely, 10 = Very likely]
```
Sender name: Jacinta Monaghan. From: info@elitephysiocookstown.co.uk. Branding #2A9EA7.
**Note:** This is the post-IA NPS survey email. In the new system the 0–10 scale lives in Tally — email just needs a button to the Tally form.

## Pre Ax form reminder (SMS)
```
Hi {FIRSTNAME}, Thank you for booking your first appointment with us. To help us understand how your issue is affecting you and what the underlying cause of the issue might be, please complete our Pre Assessment Information Form which has been sent to you via email. Thanks
```
~2 SMS. Original typos: "what your the underlying", "which has sent to you".
**Note:** Part of the Onboarding flow → being MIGRATED TO CLINIKO FORMS. Informational only — not rebuilt in the new system.

## IA satisfaction SMS
```
How did we do? Please let us know how likely you are to recommend us to friends or family based on your visit? Thank You.
```
1 SMS. **Flag:** no survey link in the body — new version MUST include the Tally link, or patients can't score.

## promotor nps follow up (email)
**Subject:** Thank you very much {FIRSTNAME}
**From:** Jacinta Monaghan <info@elitephysiocookstown.co.uk>
**Body:**
```
Hi {FIRSTNAME}

Thank you very much for your survey response after our physio appointment. We are very grateful you took the time to complete the survey. Could we please ask you for one more favour, it will just take one minute?

Could you please leave us a Google review to let other people know about your experience with us?

Some people can be skeptical about booking an appointment and seeing a positive review on Google can give them the encouragement they need to book an appointment with us so we can help them too.

Here is a link to our Google review page:

https://g.page/r/CfpgA6cxZez1EAE/review

Thank you very much
```
109 words. **Flag:** link is the COOKSTOWN Google review URL — confirms the all-locations bug (Maghera promoters wrongly sent here). New system: in the Tally form's promoter screen, `google_review_url` is set per clinic. Cookstown = https://g.page/r/CfpgA6cxZez1EAE/review, Maghera = https://g.page/r/Cccza5z-M6UtEAE/review.

## Welcome Letter (email)
**Subject:** Welcome Letter
**From:** Jacinta Monaghan <info@elitephysiocookstown.co.uk>
**Body:**
```
Hi {FIRSTNAME}

Thank you for choosing Elite Physiotherapy as your health care provider and giving us the privilege of working with you and to help you live free from pain and get back to doing things that matter most in your life. We appreciate you have other option and we promise you we will do everything possible to ensure you get the result you want

Here is what you should expect from our team:

• We never treat just the site of the problem- We are not interested in short term relief (paracetamol can do that for you). Yes, of course, we want to ease your pain as quickly as possible, our goal however, is to find the true cause and leave you feeling completely confident going back to the activities that matter most in your life.

• We ensure that once the pain has eased that we safely progress you through the complete treatment plan. It's not unusual to notice a significant reduction in your pain levels quickly with us but it's important to know that there may still be a little more work to do in your movement plan to ensure this problem does not reoccur so please listen to your physio at all times. Patients returning to activities just because their pain is gone is the BIGGEST MISTAKE WE SEE ON A DAILY BASIS. We see this even in our work in elite sport and it's our team's job to ensure you don't make the same mistake. We promise we have your best interests at heart and this will SAVE YOU time, money and effort in the long run and get you back safely and as quickly as possible.

• Complete support throughout the whole process with your movement plan emailed to you after each session. If for some reason you don't receive your exercises by the time you get home, please do us a favour and check your spam folder and if they're still not there send us a quick email to info@elitephysiocookstown.co.uk

As life is so manic these days, it's important to take a moment and just breathe.. So we've enclosed some key tips for you to get started on your recovery before you visit the clinic. For tips on non-pharmaceutical immediate pain and stress relief click [here] and for exercises to help you start to get your body moving pain free again please click [here]

If you need any further help before your first appointment just give our reception team a call on 02886440995

Warm Regards
```
427 words. Two "click here" hyperlinks (targets not captured). **Note:** Part of Onboarding flow → MIGRATE TO CLINIKO FORMS. Informational only.

## How to get the most from elite physio (email)
**Subject:** How To Get The Most Value Out of Your Experience With Elite Physio & Deal With Any Setbacks
**From:** [NEXT_PRACTITIONER_NAME] <[NEXT_PRACTITIONER_EMAIL]> (Cliniq Apps dynamic variables — resolves to the patient's assigned practitioner)
**Body:** Mostly an embedded image — editor word count is only 3, so nearly all content is a graphic (the "stress and journey" progression graph).
```
Hi {FIRSTNAME}

The graph below outlines the stress and journey we will be exposing your body to in returning to the meaningful activities in your life…

It is CRITICAL we do this in the step by step order.

[IMAGE: two-panel movement-progression graph.
 Panel 1 — "Stress On The Body" (y-axis), a stepped progression A→B→C→D→E→F→G with green arrows labelled "Meaningful Progress Every session", a "Real Life Stress On The Body" callout, ending in a smiley face + orange tick.
 Panel 2 — "Movement Progressions" numbered 1–6, "Stress On The Body" axis, a steep green arrow labelled "BIG MISTAKE" jumping from B straight to E, "Most Physio Exercises" callout at B and "Real Life Stress On The Body" callout at E, ending in a sad face + orange X.]

The biggest cause of setbacks is progressing too quickly and jumping from B to E for example. It is too big a jump for your brain usually and your body usually reverts to old movement habits that it knows best.

In the first session or two, you will progress from A to B to C etc but some activities of your daily life will cause you to use certain body parts under stress levels such as E and that's fine within moderation.

While some of our patients progress in a very linear fashion, some patients will have symptoms return short term again and that is absolutely fine and part and parcel of your journey. Every patient is different and everyone progresses at different speeds.

If you do experience a 'flare up' or pain returns, don't be down hearted, remember it is simply a 'warning signal' from your brain that you may have increased stress on a particular body part too quickly.

Especially in the first few sessions with us, you will be progressing through lower stress movements and slower movements than are happening in your day to day life at present. So if you experience any symptoms or an increase in symptoms, DO NOT PANIC.

It is simply a warning signal that your body has not earned the right to handle that level of stress on a certain body part yet at that speed YET. But, as you progress through your step by step treatment plan, we will be getting to this speed and stress and you will be able to tolerate this as we progress through your movement plan and you will be doing this with thoughtless, fearless, movement shortly.

The best thing to do in this situation is:
First, relax and reassure yourself you are safe
Second, breathe and use your 60 second Miracle De Stress Technique if required
Thirdly, refocus and follow your therapist's advice
Fourth, spend some time doing your exercises if time allows.
Fifth, ensure all your exercises are pain free and you breathe through your movements.

Most setbacks will simply settle down within 24 hours. Often it is the worrying and emotional reactions that cause the most problems thereafter and not the event itself :)

If you are worried or anxious about anything, don't hesitate to contact us at:

info@elitephysiocookstown.co.uk
```
**Flags:** (1) Content is image-based — does not transfer cleanly to other email systems; the graph would need to be re-exported as an image asset. (2) Uses `[NEXT_PRACTITIONER_NAME]`/`[NEXT_PRACTITIONER_EMAIL]` dynamic variables — our system uses different variable names. **Note:** Educational onboarding email — part of the Onboarding flow → informational only, not rebuilt in the new NPS system.

## 30 day follow up (email)
**Subject:** Just Checking in...
**From:** Sinead Rocks <sinead@elitephysiocookstown.co.uk> (note: personal staff sender, not info@)
**Body:** Editor word count is 242 — only the opening ~90 words were captured; remainder not screenshotted.
```
Hi {FIRSTNAME},

This is Sinead from Elite Physio…

{LAST_PROVIDER_FIRSTNAME} asked me to get in touch to see how you are keeping and how your recovery is going?

To keep you on track you should still have access to all your rehab videos through our online library, if you have any problems accessing that then just let me know.

Hopefully all is going well but if you need any help just reply to this email and I will get {LAST_PROVIDER_FIRSTNAME} to give you a call to see if we can help with anything.

Thank you again for your kind review after your last session with us, we really appreciate the feedback. If you haven't already done so could we ask you to please take 30 seconds and leave the review on our google page by clicking this [link].

Some people find coming to physio for the first time daunting or are sceptical we can help so these reviews really help them get over those anxieties by showing them we have helped other people with similar issues.

If there is anyone that you know that is in pain or suffering needlessly at the minute and you think we can help, please let them know about us or you can reply to this email with their details and we will get them booked in.

If there's anything else you need or if you have any other feedback, please just let me know

Thank You

Sinead
```
242 words. Contains a hyperlinked "link" → Google review page (target not captured — likely the Cookstown review URL).
**Flags:** (1) Uses `{LAST_PROVIDER_FIRSTNAME}` dynamic variable — different from our naming. (2) Asks for a Google review and references "your kind review after your last session" — this is the PROMOTER variant of the 30-day follow-up (sent to satisfied/promoter patients). Pairs with "30 day follow up passive" below. (3) Also doubles as a referral ask. **Note:** 30-day post-discharge retention email — directly relevant to the drop-off automation and the broader retention goal. Worth deciding whether the new system owns a version of this.

## 30 day follow up passive (email)
**Subject:** Just Checking in...
**From:** Sinead Rocks <sinead@elitephysiocookstown.co.uk>
**Body:**
```
Hi {FIRSTNAME},

This is Sinead from Elite Physiotherapy, my role in the clinic is to ensure all our patients get the help they need.

{LAST_PROVIDER_FIRSTNAME} asked me to get in touch to see how you are keeping and how your recovery is going?

You should still have access to all your rehab videos through our online library, if you have any problems accessing that then just let me know.

Hopefully all is going well but if you need any help just reply to this email and I will give you a call to see if we can help with anything.

If there's anything else you need or if you have any other feedback, please just let me know
```
121 words (sign-off "Thank You / Sinead" likely below, not captured).
**Flags:** (1) Uses `{LAST_PROVIDER_FIRSTNAME}` dynamic variable. (2) This is the PASSIVE/non-promoter variant of the 30-day follow-up — no Google review ask, no referral ask, softer. Pairs with "30 day follow up" above. **Note:** Confirms the 30-day check-in is already segmented by NPS score (promoter vs passive) — useful precedent for how the new system should branch its 30-day messaging.

---

# Injection Therapy flow (added 2026-08-24)

Martin's Cliniq Apps injection workflow has **4 parts**. This is part 1 of 4 —
the rest to follow. Note this is richer than the May plan assumed: the plan
listed only "Injection Therapy Day 14 + Day 28" plus a pre-form, and missed
that the flow opens with an information email on booking.

## 1. Injection Therapy Information (email, on booking)

**Trigger:** patient books an Injection Therapy appointment (fires on booking,
not on the appointment date).
**Subject:** *(not captured in the screenshot — need it)*
**Sign-off variable:** `{NEXT_PROVIDER_FIRSTNAME}{NEXT_PROVIDER_LASTNAME}`
— note there is **no space between them** in the original, so it renders as
"MartinLoughran". Fix on migration.

**Body:**
```
Injection Therapy Information

Hi {FIRSTNAME} Thank you for booking your injection therapy appointment with
us. This email should cover all the important information you will need ahead
of your appointment. If you need anything else, all our contact details are at
the bottom of this email.

Common Questions

What is a steroid injection?
Corticosteroids are medicines which can relieve pain, swelling and stiffness by
reducing inflammation. Corticosteroids are extremely safe.

A steroid injection will help reduce your pain and allow you to start
rehabilitation sooner. This should reduce the amount of physiotherapy needed and
will help you to return to normal activities more quickly.

Why don't I just take anti-inflammatory tablets?
You can, but the side effects of these are much more common and can cause
stomach upsets and bleeding. An injection will bypass the stomach.

What is an Ostenil Plus Injection?
Ostenil Plus is a solution containing hyaluronic acid developed specifically for
the treatment of osteoarthritis. It can be injected into the knee, or any of the
other synovial joints in the body to decrease pain and stiffness and improve the
other symptoms of osteoarthritis.

An injection will not be possible if...
  - Have an infection on your skin or anywhere in your body or have had
    antibiotics within the last 2 weeks.
  - Are allergic to local anaesthetic or steroid
  - Feel unwell.
  - Have had Covid-19 within the last 2 weeks
  - Are under 18.
  - Have certain circulatory/cardiac /liver or kidney conditions
  - Area to be treated has prosthetic joint
  - Had a previous infection in the area to be treated
  - Received a live or live attenuated vaccine within last 2 weeks

Are there any possible side effects?
These are very rare and your physiotherapist will discuss them with you:
  - Flushing of the face for a few hours.
  - Small area of fat loss or change of skin colour around the injection site.
  - Slight vaginal bleeding.
  - Diabetic patients may notice a temporary increase in blood sugar levels.
  - Temporary bruising at the site of the injection.
  - Infection: if the area becomes hot swollen and more painful for more than
    24 hours you should contact your physiotherapist or doctor immediately.
  - Allergic reaction to drugs.
  - In very rare cases a condition called Chorioretinopathy can develop which
    can cause a detatched retina. If you find new blurred or distorted vision,
    difficulty with bright lights, this should be reported to your GP or
    optician.

    You will be asked to wait for 30 minutes after the injection to ensure
    there is no allergic reaction to the drugs injected.

How is the injection done?
The skin is cleaned with antiseptic. A needle is gently put into the affected
part and the solution is injected through the needle.

Is the injection painful?
Not particularly, as your physiotherapist has had intensive training in the
technique. Sometimes it can be sore for a few hours, but you will be told what
to do about this.

How fast does the injection work?
If local anaesthetic is used the pain should be less within a few minutes,
though it may return after about an hour. The steroid usually starts to work
after 24 to 48 hours.

You will have to rest for 48 hours after injection and you should not drive
home. If you are unable to rest after the injection please inform staff at time
of booking appointment and it may be deferred.

How many injections can I have?
This depends on what has been injected, how severe the pain is and how long you
have had the pain for. Usually one injection is enough; you may need more but no
more than three per episode. This will be decided by you and your
physiotherapist.

Sustained use of joint injections with steroid can cause weakening of soft
tissues and cartilage. This may be more detrimental in the long term.

There is no limit on how many Ostenil Plus injections you can have as there is
no evidence that it causes weakening of soft tissues and cartilage in the same
way steroid may do.

Herbal Remedies
Please stop taking all herbal remedies 2 weeks prior to injection
(Ginseng/Turmeric/St Johns Wort)

[image: syringe drawing solution from a vial]

Thank you and we look forward to meeting you in the clinic.

{NEXT_PROVIDER_FIRSTNAME}{NEXT_PROVIDER_LASTNAME}
```

### Typos in the original (fix on migration, don't carry over)
- "detatched retina" -> "detached retina"
- "hot swollen and more painful" -> "hot, swollen and more painful"
- "circulatory/cardiac /liver" -> stray space before "/liver"
- Sign-off renders with no space between first and last name.
- The contraindication and side-effect lists start mid-sentence ("Have an
  infection...", "Are allergic to...") with no lead-in stem such as
  "An injection will not be possible if you:".

### REQUIRED CHANGE — steroid vs hyaluronic acid (Martin, 2026-08-24)
The clinic now performs **as many hyaluronic acid injections as
corticosteroid** ones, but this email is written almost entirely around
steroid. Ostenil Plus gets a single paragraph.

**Not two workflows — one email that covers both**, and tells the patient their
physio will have recommended whichever is most appropriate for them.

**NO BRAND NAMES** (Martin, 2026-08-24). The clinic may use brands other than
Ostenil, so the new copy says **"hyaluronic acid"** generically throughout.
The current email names "Ostenil Plus" twice — in the "What is an Ostenil Plus
Injection?" heading and in the "How many injections can I have?" section. Both
become generic. Same principle for the steroid side: "corticosteroid" /
"steroid", never a product name. This keeps the email correct whichever brand
is stocked, and avoids a rewrite every time supply changes.

Where the current copy is steroid-only and needs an HA counterpart:

| Section | Problem |
|---|---|
| "What is a steroid injection?" / "What is an Ostenil Plus Injection?" | Leads with steroid as if it is the default, and names a brand. Needs a paired, brand-free "what is each, and which am I getting" framing. |
| "An injection will not be possible if..." | Written for steroid ("allergic to local anaesthetic or steroid"). HA contraindications differ. |
| "Are there any possible side effects?" | Entirely steroid side effects — flushing, fat loss, blood sugar, chorioretinopathy. None of these apply to HA. |
| "How fast does the injection work?" | "The steroid usually starts to work after 24 to 48 hours." HA has a different onset profile. |
| "You will have to rest for 48 hours" | Stated as universal. Needs confirming whether it applies to HA too. |
| "How many injections can I have?" | Already covers both — the one section that does. |

### Hyaluronic acid — clinical facts (Martin, 2026-08-24)

Supplied by Martin in answer to the six questions above. **This is the source of
truth for every HA claim in the new copy — nothing about HA may be written that
is not traceable to this list.**

| | Corticosteroid (existing copy) | Hyaluronic acid (Martin) |
|---|---|---|
| **Onset** | Local anaesthetic eases pain in minutes, may return after ~1 hour. Steroid works after 24-48 hours. | Slow-acting. Some feel immediate benefit; for others **3-4 weeks** to feel significantly better. |
| **Duration** | not stated | Long-lasting — **6 to 9 months**. |
| **Rest** | **Four days** active rest (Martin 2026-08-24). NB the Cliniq Apps original said 48 hours — it was wrong / out of date. | **48 hours** active rest and deloading — and the reason matters: this is to get **best value from the injection**, NOT because of harm. There are **no detrimental physiological effects** from doing too much too soon. |
| **Driving** | **Only if local anaesthetic is used** — then no driving home (Martin 2026-08-24). The Cliniq Apps original stated it unconditionally. | **Can drive home** for the vast majority of HA injections — but check with the physiotherapist before attending. |
| **Herbal remedies** | Stop 2 weeks prior (ginseng / turmeric / St John's Wort) | **Same — applies to both** (Martin 2026-08-24) |
| **Course** | Usually one; no more than **three per episode**. Sustained steroid use can weaken soft tissues and cartilage. | Typically a **one-off**. Some patients with long-standing conditions choose to repeat every **6-12 months**. |
| **Contraindications** | The 9-item list above. | **Very few.** Do not attend if unwell, or if they have had a **cold or flu that week**, or if there is any medical reason they cannot receive an injection on that date. |
| **Side effects** | Long list — flushing, fat loss, blood sugar, chorioretinopathy etc. | **Fainting, bruising, and a 5-in-100,000 chance of skin infection.** Performed under **no-touch sterile conditions** to minimise infection risk. |
| **Post-injection wait** | **30 minutes** (allergic reaction check) | **15-20 minutes** |

The driving difference is the safety-critical one: steroid says do not drive
home, HA says most patients can. The new copy must not blur these together.

### Part 1 — fully answered, nothing outstanding

The subject line was never captured from Cliniq Apps, so the new one is written
rather than migrated: **"Your injection appointment — what you need to know"**
(approved by Martin 2026-08-24).

Everything else on part 1 is answered (Martin 2026-08-24): herbal remedies
apply to both; steroid rest is four days vs 48 hours for HA; steroid driving
restriction applies only when local anaesthetic is used; no needle/syringe
image in the new email.

Two of those **correct the Cliniq Apps original**, they are not just additions:
the old email said 48 hours' rest after steroid (should be four days) and
banned driving outright (only applies when local anaesthetic is used).
