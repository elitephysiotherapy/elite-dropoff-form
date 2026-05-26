"""All patient-facing message templates + internal alerts.

Copy is the final wording from patient_communication_system.md. Variables use
{curly_brace} placeholders filled per send. SMS bodies are kept to ~1 segment.

Public API:
  render_sms(template_id, ctx)    -> str
  render_email(template_id, ctx)  -> dict(subject, text, html, from_name, from_email, reply_to)
  render_internal(template_id, ctx) -> dict(subject, text, html)
"""

import html as _html
import re

import config

# ===========================================================================
# SMS templates  (one 160-char segment target; sender is one-way "ElitePhysio")
# ===========================================================================

SMS = {
    "booking_confirmation": (
        "Hi {first_name}, you're booked at Elite Physio {clinic_name}: "
        "{appointment_date} {appointment_time} with {practitioner_name}. "
        "Check your email for what to do before your visit. "
        "Questions? {clinic_phone}"
    ),
    "form_reminder": (
        "Hi {first_name}, before your appointment on {appointment_date} please "
        "take 5 mins to complete your pre-assessment form so we can help you "
        "faster: {form_link}"
    ),
    "appointment_reminder": (
        "Hi {first_name}, a reminder of your appointment at Elite Physio "
        "{clinic_name} tomorrow, {appointment_date} at {appointment_time} with "
        "{practitioner_name}. Need to change it? Call {clinic_phone}"
    ),
    "ia_survey": (
        "Hi {first_name}, thanks for coming in to see {practitioner_name} today. "
        "How did we do? It takes 30 seconds and genuinely helps us improve: "
        "{survey_link}"
    ),
    "discharge_survey": (
        "Hi {first_name}, how have you been getting on since your recent visit "
        "with {practitioner_name}? We'd really value your feedback - 30 seconds, "
        "and it helps us look after every patient better: {survey_link}"
    ),
    "promoter_followup": (
        "Hi {first_name}, thank you so much for your kind feedback! If you have "
        "a moment, a quick Google review helps others find the help they need: "
        "{google_review_url}"
    ),
    "detractor_followup": (
        "Hi {first_name}, thank you for your honest feedback - we're sorry your "
        "visit fell short. Sinead from our team will be in touch personally. "
        "You can also reach us on {clinic_phone}."
    ),
    "cancellation_rebook": (
        "Hi {first_name}, we've cancelled your appointment as requested. When "
        "you're ready to get back on track, rebook here: {booking_link} or call "
        "{clinic_phone}. We're keen to see you finish strong."
    ),
    "no_show": (
        "Hi {first_name}, we missed you at Elite Physio today. No problem - "
        "things happen. Rebook here: {booking_link} or call {clinic_phone} and "
        "we'll find a time that works."
    ),
    "keep_in_touch_180": (
        "Hi {first_name}, it's been a while! If any old aches have returned, "
        "we'd love to help you get on top of them. Book here: {booking_link} "
        "or call {clinic_phone}."
    ),
}


# ===========================================================================
# Patient-facing email templates
# sender: "clinic" (info@), "sinead", or "martin"
# ===========================================================================

EMAIL = {
    "booking_confirmation": {
        "sender": "clinic",
        "subject": "You're booked in, {first_name} - here's what happens next",
        "body": """Hi {first_name},

Your appointment is confirmed:

  Date: {appointment_date} at {appointment_time}
  Where: Elite Physiotherapy {clinic_name}, {clinic_address}
  With: {practitioner_name}, {appointment_type}

One thing to do before you come in
To make the most of your first visit, please complete a short pre-assessment form. It takes about 5 minutes and tells your physiotherapist exactly how your problem is affecting you - so you spend your appointment getting help, not filling in paperwork.

Complete your pre-assessment form: {form_link}

What to expect
We'll send you a short guide before your visit, plus a reminder of your time. If anything changes, just call us on {clinic_phone} and we'll happily move things around.

We're looking forward to meeting you.

Elite Physiotherapy {clinic_name}
{clinic_phone}""",
    },
    "welcome": {
        "sender": "clinic",
        "subject": "Welcome to Elite Physiotherapy, {first_name}",
        "body": """Hi {first_name}

Thank you for choosing Elite Physiotherapy as your health care provider and giving us the privilege of working with you and to help you live free from pain and get back to doing things that matter most in your life. We appreciate you have other options and we promise you we will do everything possible to ensure you get the result you want.

Here is what you should expect from our team:

• We never treat just the site of the problem - We are not interested in short term relief (paracetamol can do that for you). Yes, of course, we want to ease your pain as quickly as possible, our goal, however, is to find the true cause and leave you feeling completely confident going back to the activities that matter most in your life.

• We ensure that once the pain has eased that we safely progress you through the complete treatment plan. It's not unusual to notice a significant reduction in your pain levels quickly with us but it's important to know that there may still be a little more work to do in your movement plan to ensure this problem does not reoccur so please listen to your physio at all times. Patients returning to activities just because their pain is gone is the BIGGEST MISTAKE WE SEE ON A DAILY BASIS. We see this even in our work in elite sport and it's our team's job to ensure you don't make the same mistake. We promise we have your best interests at heart and this will SAVE YOU time, money and effort in the long run and get you back safely and as quickly as possible.

• Complete support throughout the whole process with your movement plan emailed to you after each session. If for some reason you don't receive your exercises by the time you get home, please do us a favour and check your spam folder and if they're still not there send us a quick email to info@elitephysiocookstown.co.uk

As life is so manic these days, it's important to take a moment and just breathe. So we've enclosed some key tips for you to get started on your recovery before you visit the clinic. For tips on non-pharmaceutical immediate pain and stress relief click here: https://canva.link/bwk24xk93lqkooj and for exercises to help you start to get your body moving pain free again please click here: https://canva.link/91gpchk5r5fohu6

If you need any further help before your first appointment just give our reception team a call on 02886440995

Warm Regards,
The Elite Physiotherapy Team""",
    },
    "form_reminder": {
        "sender": "clinic",
        "subject": "A quick form before we see you, {first_name}",
        "body": """Hi {first_name},

We're looking forward to seeing you on {appointment_date} at {appointment_time}. There's one short thing left to do.

Your pre-assessment form takes about 5 minutes and means your physiotherapist already understands your problem before you walk in - so the whole appointment is spent on you, not paperwork.

Complete it here: {form_link}

If you've already done this, thank you - please ignore this email. Any trouble with the form? Just call us on {clinic_phone}.

See you soon,
Elite Physiotherapy {clinic_name}""",
    },
    "pre_appointment": {
        "sender": "clinic",
        "subject": "Your appointment on {appointment_date} - what to expect",
        "body": """Hi {first_name},

Your appointment is coming up:

  Date: {appointment_date} at {appointment_time}
  Where: Elite Physiotherapy {clinic_name}, {clinic_address}
  With: {practitioner_name}

A few things to make it easy:

- Arrive 5 minutes early so we can start right on time.
- Wear comfortable clothing you can move in - we may need to see the area we're treating.
- Bring any relevant scan results or referral letters.
- Parking is available on site.

Need to change your appointment? Just call {clinic_phone} - the sooner you let us know, the sooner we can offer the slot to someone who needs it.

See you soon,
Elite Physiotherapy {clinic_name}""",
    },
    "ia_survey": {
        "sender": "clinic",
        "subject": "How did we do today, {first_name}?",
        "body": """Hi {first_name},

Thank you for coming in to see {practitioner_name} today at Elite Physiotherapy {clinic_name}.

We're always working to give our patients the best possible experience, and your honest opinion is the most useful thing we have. Based on your visit today:

How likely are you to recommend us to a friend or family member?

Tell us in 30 seconds: {survey_link}

Thank you - it really does shape how we look after every patient.

Elite Physiotherapy {clinic_name}""",
    },
    "ia_survey_nurture": {
        "sender": "clinic",
        "subject": "30 seconds, {first_name}? We'd love your feedback",
        "body": """Hi {first_name},

We don't want to pester you - just a gentle nudge in case yesterday got busy.

If you have 30 seconds, we'd really value your feedback on your visit to see {practitioner_name}:

{survey_link}

If now isn't a good time, no problem at all. Thank you either way.

Elite Physiotherapy {clinic_name}""",
    },
    "discharge": {
        "sender": "clinic",
        "subject": "Well done, {first_name}",
        "body": """Hi {first_name},

Well done - and congratulations on completing your treatment with {practitioner_name} at Elite Physiotherapy.

You put in the work, and it paid off. A few things to help you stay well from here:

- Keep up your movement plan. The exercises that got you here are the ones that keep you here - your plan stays in your online library: {exercise_library_link}
- Build back gradually. If something flares, don't panic - it's a signal, not a setback. Ease off, breathe, and return to your exercises pain-free.
- We're still here. If this problem returns, or a new one appears, you don't go to the back of the queue - just call {clinic_phone} and we'll look after you.

And while we have you - your honest feedback helps us look after every patient better. It takes about 30 seconds: {survey_link}

It's been a pleasure helping you. Look after yourself.

The team at Elite Physiotherapy {clinic_name}""",
    },
    "discharge_survey": {
        "sender": "clinic",
        "subject": "How was your experience with us, {first_name}?",
        "body": """Hi {first_name},

We hope you've been keeping well since your recent appointment with {practitioner_name} at Elite Physiotherapy {clinic_name}.

We'd really value your feedback on how we did - across your whole experience with us, not just one visit. How likely are you to recommend us to a friend or family member?

Tell us in 30 seconds: {survey_link}

If you'd like to keep your progress going, your exercise plan is always in your online library: {exercise_library_link}

And if anything has flared up, or there's more you'd like help with, just give us a call on {clinic_phone} - you won't go to the back of the queue.

Thank you for trusting us with your care.

Elite Physiotherapy {clinic_name}""",
    },
    "promoter_followup": {
        "sender": "clinic",
        "subject": "Thank you, {first_name} - one small favour?",
        "body": """Hi {first_name},

Thank you for your wonderful feedback after your visit - it genuinely made our team's day.

Could we ask one small favour? It takes about a minute.

A lot of people are nervous or sceptical about whether physio can help them. Seeing a real review from someone like you is often the nudge they need to finally get help.

Leave us a Google review: {google_review_url}

And if you know someone in pain who we could help, feel free to pass our details on - or reply to this email with theirs and we'll take good care of them.

Thank you for being part of Elite Physiotherapy.

Elite Physiotherapy {clinic_name}""",
    },
    "passive_followup": {
        "sender": "clinic",
        "subject": "Thank you for your feedback, {first_name}",
        "body": """Hi {first_name},

Thank you for taking the time to give us your feedback after your recent visit - and for being honest with your score.

We aim for every patient to leave us a 9 or 10. If anything at all wasn't quite right, we'd genuinely like to hear it - just reply to this email. We read every reply, and it's exactly how we get better.

Thank you again,
Elite Physiotherapy {clinic_name}""",
    },
    "detractor_followup": {
        "sender": "sinead",
        "subject": "I'm sorry, {first_name} - and thank you for telling us",
        "body": """Hi {first_name},

My name is Sinead, and my job at Elite Physiotherapy is to make sure every patient gets the standard of care we promise.

Thank you for being honest in your feedback. I'm sorry your recent visit didn't meet that standard - and I'd genuinely like to put it right.

I'll be in touch personally within the next working day to listen and understand what happened. If you'd rather reach me first, just reply to this email or call {clinic_phone} and ask for me.

We take this seriously, and we're grateful you gave us the chance to make it right.

Kind regards,
Sinead Rocks
Operations Manager, Elite Physiotherapy
{clinic_phone}""",
    },
    "thirty_day_promoter": {
        "sender": "sinead",
        "subject": "Just checking in, {first_name}",
        "body": """Hi {first_name},

This is Sinead from Elite Physiotherapy. {practitioner_name} asked me to check in and see how you're keeping a month on from finishing your treatment.

Hopefully you're feeling great and staying on top of things. Your exercises are still in your online library if you'd like them: {exercise_library_link}

If anything has flared up - or a new niggle has appeared - don't wait for it to settle on its own. Just reply to this email or call {clinic_phone} and we'll get you straight back in.

And if you know someone struggling with pain, we'd be glad to help them too - feel free to pass on our details.

Take care,
Sinead Rocks
Elite Physiotherapy {clinic_name}""",
    },
    "thirty_day_passive": {
        "sender": "sinead",
        "subject": "Just checking in, {first_name}",
        "body": """Hi {first_name},

This is Sinead from Elite Physiotherapy - my role is to make sure every one of our patients gets the help they need.

{practitioner_name} asked me to check in and see how you're getting on a month after finishing your treatment.

Your exercises are still available in your online library: {exercise_library_link}

If your symptoms have returned, or something new has come up, please don't push through it - reply to this email or call {clinic_phone} and we'll look after you.

And if there's anything we could have done better, I'd genuinely like to hear it. Just hit reply.

Take care,
Sinead Rocks
Elite Physiotherapy {clinic_name}""",
    },
    "cancellation_rebook": {
        "sender": "clinic",
        "subject": "Let's get you rebooked, {first_name}",
        "body": """Hi {first_name},

We noticed you cancelled your recent appointment and haven't rebooked yet - and we don't want you to lose the progress you've made.

Recovery works best without long gaps. The sooner you're back in, the sooner you'll feel the benefit.

Rebook in under a minute: {booking_link}
Or call us on {clinic_phone}

If something came up, or you're not sure physio is still the right step, just reply to this email - we'd rather hear from you than have you struggle on alone.

Elite Physiotherapy {clinic_name}""",
    },
    "no_show": {
        "sender": "clinic",
        "subject": "We missed you today, {first_name}",
        "body": """Hi {first_name},

It looks like you weren't able to make your appointment today - that's OK, life happens.

Normally a fee applies for a missed appointment, but we'd much rather see you back than charge you. Get in touch to rebook and we'll happily waive it this time.

Rebook here: {booking_link}
Or call us on {clinic_phone}

The sooner we see you, the sooner we can get you feeling better.

Elite Physiotherapy {clinic_name}""",
    },
    "iadnr_nudge": {
        "sender": "clinic",
        "subject": "Did we answer everything for you, {first_name}?",
        "body": """Hi {first_name},

It was good to meet you at your first appointment with {practitioner_name}. We noticed you haven't booked your next visit yet - so we wanted to check in.

Sometimes that's because everything felt clear and you're happy to crack on. Sometimes it's because something held you back - a question that wasn't fully answered, or you weren't sure the plan was right for you.

Either way, we'd like to know:

If you're ready to continue, book here: {booking_link}
If you have a question first, call us on {clinic_phone} or just reply to this email - no pressure at all.

Your assessment is only the first step. The results come from the plan that follows it, and we'd love to see you through it.

Elite Physiotherapy {clinic_name}""",
    },
    "thirty_day_cna_dna": {
        "sender": "clinic",
        "subject": "Still thinking of you, {first_name}",
        "body": """Hi {first_name},

It's been about a month since we last saw you, and we wanted to check in.

If your problem has settled completely - that's brilliant, and we're genuinely pleased for you.

But if it's still there, or it's crept back, please don't put up with it. Pain that lingers usually means there's still something to resolve, and the longer it's left, the harder it can be to shift.

Book a visit: {booking_link}
Or call us on {clinic_phone}

We'd love to help you finish what you started.

Elite Physiotherapy {clinic_name}""",
    },
    "keep_in_touch_90": {
        "sender": "clinic",
        "subject": "How are you keeping, {first_name}?",
        "body": """Hi {first_name},

It's been about three months since we last saw you at Elite Physiotherapy, and we were thinking of you.

No agenda here - we just like to check in. How are you keeping? Is everything still feeling good?

If it is, wonderful. If something has crept back, you know where we are - and you won't be starting from scratch, because we already know your history.

Book whenever you're ready: {booking_link}
Or call us on {clinic_phone}

Look after yourself,
Elite Physiotherapy {clinic_name}""",
    },
    "reactivation_12mo": {
        "sender": "clinic",
        "subject": "It's been a year, {first_name} - how are you keeping?",
        "body": """Hi {first_name},

It's been a year since we last saw you at Elite Physiotherapy - which we hope means you've been feeling great.

But a year is also long enough for old problems to quietly return, or new ones to set in. If anything is bothering you - the thing we treated before, or something new - it's worth getting ahead of it.

As a returning patient, you're never starting from zero with us. We know your history, and we can pick up quickly.

Book your visit: {booking_link}
Or call us on {clinic_phone}

Whatever you decide, we wish you well - and we're here if you need us.

Elite Physiotherapy {clinic_name}""",
    },
    "birthday": {
        "sender": "clinic",
        "subject": "Happy birthday, {first_name}!",
        "body": """Hi {first_name},

Everyone at Elite Physiotherapy wanted to wish you a very happy birthday.

We hope your year ahead is a healthy, active and pain-free one - and if we can help you keep it that way, you know where we are.

Have a wonderful day,
The team at Elite Physiotherapy {clinic_name}""",
    },
    "manual_1d": {
        "sender": "martin",
        "subject": "Did we get something wrong, {first_name}?",
        "body": """Hi {first_name},

My name is Martin and I'm the Head Physiotherapist here at Elite Physiotherapy. My job is to make sure every patient who comes to us gets the help they really need.

Can I ask you a genuine favour?

You came to see us recently and didn't book back in. In our experience, that usually means something about the experience wasn't right for you - and I'd really like to understand what.

Could you watch this 60-second video and answer the three questions below? It'll take no more than two minutes, and it would help me enormously.

https://www.loom.com/share/2612c76c00b64a2591233e242b2be15d

1. Did you have clarity on your problem, the plan, and what to do next?
2. Did you have faith the plan would get you the result you wanted?
3. Was there anything we could have done better - and what was the real reason you didn't book back in?

Just reply to this email. I read every response personally, and I promise to take it on board.

Thank you,
Martin Loughran
Head Physiotherapist, Elite Physiotherapy""",
    },
}


# ===========================================================================
# Internal alert emails (sent TO staff, not patients)
# ===========================================================================

INTERNAL_EMAIL = {
    "detractor_alert": {
        "subject": "DETRACTOR - {patient_name} scored {score}/10 ({clinic_name})",
        "body": """A detractor response just came in. Please action the callback.

  Patient:       {patient_name}
  Score:         {score}/10
  Physio seen:   {physio_name}
  Clinic:        {clinic_name}
  Survey:        {trigger_label}
  Appointment:   {appointment_date}

  Callback requested: {callback_requested}
  Callback number:    {callback_number}

  What they told us:
  "{open_text}"

  Patient contact: {patient_phone} | {patient_email}

Logged in: NPS - Detractor Tracker. Please update the resolution status there once actioned.""",
    },
    "passive_alert": {
        "subject": "PASSIVE - {patient_name} scored {score}/10 ({clinic_name})",
        "body": """A passive response came in - for your weekly review (no urgent action needed).

  Patient:      {patient_name}
  Score:        {score}/10
  Physio seen:  {physio_name}
  Clinic:       {clinic_name}
  Survey:       {trigger_label}

  What would make it a 9 or 10:
  "{open_text}"

Logged in: NPS - Raw Data.""",
    },
}


# ===========================================================================
# Rendering
# ===========================================================================

class _Safe(dict):
    """format_map dict that leaves unknown {placeholders} visible (test-friendly)."""
    def __missing__(self, key):
        return "{" + key + "}"


_URL_RE = re.compile(r"(https?://[^\s<]+)")


def _to_html(text):
    """Convert a plain-text body to a simple, clean HTML email."""
    paras = text.split("\n\n")
    rendered = []
    for para in paras:
        out = []
        for i, seg in enumerate(_URL_RE.split(para)):
            if i % 2 == 1:   # a URL
                href = _html.escape(seg, quote=True)
                out.append(f'<a href="{href}" style="color:#2A77BC;'
                           'word-break:break-all;overflow-wrap:anywhere;">'
                           f'{_html.escape(seg)}</a>')
            else:
                out.append(_html.escape(seg).replace("\n", "<br>"))
        rendered.append('<p style="margin:0 0 16px;overflow-wrap:break-word;'
                        f'word-break:break-word;">{"".join(out)}</p>')
    body = "\n".join(rendered)
    return (
        '<!doctype html><html><body style="margin:0;background:#f4f7f9;">'
        '<div style="max-width:560px;margin:0 auto;padding:28px 24px;'
        'font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;'
        'color:#33424E;background:#ffffff;overflow-wrap:break-word;word-break:break-word;">'
        f'{body}'
        '</div></body></html>'
    )


def _sender(kind):
    """Return (from_name, from_email, reply_to) for a sender kind."""
    if kind == "sinead":
        name, addr = config.EMAIL_SINEAD
        return name, addr, addr
    if kind == "martin":
        name, addr = config.EMAIL_MARTIN
        return name, addr, addr
    return config.EMAIL_FROM_NAME, config.EMAIL_FROM_ADDRESS, config.EMAIL_FROM_ADDRESS


def render_sms(template_id, ctx):
    """Return the rendered SMS body string."""
    if template_id not in SMS:
        raise KeyError(f"unknown SMS template: {template_id}")
    return SMS[template_id].format_map(_Safe(ctx))


def render_email(template_id, ctx):
    """Return dict(subject, text, html, from_name, from_email, reply_to)."""
    if template_id not in EMAIL:
        raise KeyError(f"unknown email template: {template_id}")
    t = EMAIL[template_id]
    subject = t["subject"].format_map(_Safe(ctx))
    text = t["body"].format_map(_Safe(ctx))
    from_name, from_email, reply_to = _sender(t.get("sender", "clinic"))
    return {
        "subject": subject,
        "text": text,
        "html": _to_html(text),
        "from_name": from_name,
        "from_email": from_email,
        "reply_to": reply_to,
    }


def render_internal(template_id, ctx):
    """Return dict(subject, text, html) for an internal staff alert."""
    if template_id not in INTERNAL_EMAIL:
        raise KeyError(f"unknown internal template: {template_id}")
    t = INTERNAL_EMAIL[template_id]
    text = t["body"].format_map(_Safe(ctx))
    return {
        "subject": t["subject"].format_map(_Safe(ctx)),
        "text": text,
        "html": _to_html(text),
    }


def all_template_ids():
    """(sms_ids, email_ids, internal_ids) — used by the test harness."""
    return sorted(SMS), sorted(EMAIL), sorted(INTERNAL_EMAIL)
