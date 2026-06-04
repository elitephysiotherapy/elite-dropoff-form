# Twilio UK Alphanumeric Sender ID Submission — Draft Content

**Sender ID requested:** `ElitePhysio` (11 characters, within Twilio's limit)

**Country:** United Kingdom

**Use case category:** Customer Care (with secondary: Marketing — satisfaction follow-ups)

---

## Use case description (paste into "Use case description" field)

Elite Physiotherapy Limited is a UK private healthcare clinic operating from sites in Cookstown and Maghera, Northern Ireland. We send transactional SMS to existing patients only, covering four message types: (1) patient feedback surveys following an appointment, (2) reactivation reminders to patients who have cancelled or missed a scheduled appointment, (3) educational and follow-up content during an active course of treatment, and (4) administrative confirmations and intake-form reminders. Every recipient is an existing patient with an established treatment relationship, who has consented to SMS contact at the point of booking. We do not send to acquired lists, third-party data, or non-patients. Sending volume is approximately 400–500 SMS per month across two clinic sites.

---

## Sample messages (paste 5 into the "Sample Messages" fields)

**Sample 1 — Post-appointment feedback survey**
```
Hi {first_name}, thanks for visiting Elite Physiotherapy today. We'd love your feedback — it takes 30 seconds: {survey_link}. Reply STOP to opt out.
```

**Sample 2 — Post-discharge feedback survey**
```
Hi {first_name}, we hope you're feeling better after your treatment with Elite Physiotherapy. We'd love to hear how we did: {survey_link}. Reply STOP to opt out.
```

**Sample 3 — Missed appointment reactivation**
```
Hi {first_name}, we missed you at your appointment today. Let us know when suits to reschedule: 028 8644 0995 or reply to this SMS. Reply STOP to opt out.
```

**Sample 4 — Cancelled appointment reactivation**
```
Hi {first_name}, sorry to hear you had to cancel. Ready to rebook? Call 028 8644 0995 or book online at elitephysiocookstown.co.uk. Reply STOP to opt out.
```

**Sample 5 — Detractor follow-up**
```
Hi {first_name}, thank you for your honest feedback. We'd genuinely like to understand what we could have done better — please call us on 028 8644 0995 when you have a moment. Reply STOP to opt out.
```

---

## Opt-in evidence (paste into "How do recipients opt in?" field)

All patients consent to SMS communication when booking an appointment with Elite Physiotherapy Limited. The consent language is presented during our intake form (hosted in our practice management system, Cliniko) and is reaffirmed in the booking confirmation email. Patients may opt out at any time by replying STOP to any SMS, by contacting reception at 028 8644 0995, or by email to reception@elitephysiocookstown.co.uk. All opt-outs are honoured immediately and recorded against the patient's record in Cliniko; suppressed patients are excluded from all future SMS sends.

---

## Opt-out mechanism (paste into "Opt-out instructions" field)

Recipients can opt out by replying STOP to any SMS message. The opt-out is processed automatically via Twilio's STOP keyword filtering and recorded against the patient's record in Cliniko within one business day. Patients can also opt out by emailing reception@elitephysiocookstown.co.uk or by phone to 028 8644 0995.

---

## Volume estimate (paste into "Expected monthly volume" field)

400–500 SMS per month, scaling with patient volume.

---

## Before you submit

Replace these placeholders throughout:
- `028 8644 0995` — your main reception number (the one on the website)
- The Tally survey link `{survey_link}` will be auto-generated once we build the Tally form; for the Twilio submission, use a placeholder like `https://tally.so/r/elite-physio` — Twilio just needs to see the *pattern*, not the real URL.

If Twilio asks for a screenshot of the booking consent language, you can either:
- Screenshot the relevant section of Cliniko's booking page
- Or screenshot your website's privacy/T&Cs section that covers SMS consent

If your current consent wording doesn't explicitly mention SMS, let me know — we should update it before submission since Twilio scrutinises this in healthcare use cases.
