"""Marketing poller — the every-10-minutes entry point.

Run by launchd via marketing_poll.sh; one cycle per invocation. Each cycle:
  1. pulls recent Cliniko appointments + cancellations
  2. asks each flow module which touches are due now
  3. for each touch: dedup -> fetch patient -> consent check -> render -> send -> log

Console output is patient-ID only (never names / emails / message bodies) so
no patient data is exposed outside the clinic's own tools.

Modes (config.py): SHADOW (nothing sent) -> SAFE_MODE (sent to test contacts)
-> LIVE. Run a one-off cycle:  ./venv/bin/python -m marketing.poller
"""

from dotenv import load_dotenv

load_dotenv(override=True)

import config
from marketing import (cliniko, common, nps, reactivation, lifecycle,
                       results, sent_log, templates, send, tally_url)

LOOKBACK_DAYS = 5
LIFECYCLE_HOUR = getattr(config, "MARKETING_LIFECYCLE_HOUR", 9)
QUIET_START = getattr(config, "MARKETING_QUIET_START", 21)
QUIET_END = getattr(config, "MARKETING_QUIET_END", 8)


def _mode():
    if config.MARKETING_LIVE:
        return "LIVE"
    if config.MARKETING_SAFE_MODE:
        return "SAFE_MODE (sends rerouted to test contacts)"
    return "SHADOW (nothing sent)"


def _in_quiet_hours(local_dt):
    h = local_dt.hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    return h >= QUIET_START or h < QUIET_END


def _survey_link(touch, patient, ctx):
    try:
        return tally_url.build_survey_url(
            patient_id=patient.get("id"),
            patient_name=patient.get("first_name", ""),
            patient_email=patient.get("email", ""),
            patient_phone=patient.get("mobile", ""),
            physio_name=ctx.get("practitioner_name", ""),
            clinic_name=ctx.get("_clinic_key", ""),
            appointment_date=ctx.get("appointment_date", ""),
            trigger_type=touch.trigger_type)
    except Exception as e:
        return f"[survey link unavailable: {e}]"


def _deliver(touch, ctx, dest):
    if touch.channel == "sms":
        body = templates.render_sms(touch.template_id, ctx)
        return send.send_sms(to=dest, body=body)
    e = templates.render_email(touch.template_id, ctx)
    return send.send_email(
        to=dest, subject=e["subject"], html=e["html"], text=e["text"],
        from_name=e["from_name"], from_email=e["from_email"],
        reply_to=e["reply_to"])


def run_once():
    now = common.now_utc()
    local = now.astimezone(common.LONDON)
    print(f"=== Marketing poller {local:%Y-%m-%d %H:%M %Z} | mode: {_mode()} ===")

    if _in_quiet_hours(local):
        print(f"Quiet hours ({QUIET_START}:00-{QUIET_END}:00) — skipping send cycle.")
        return

    started = cliniko.recently_started_appointments(LOOKBACK_DAYS)
    cancelled = cliniko.recently_cancelled_appointments(LOOKBACK_DAYS)
    print(f"Cliniko: {len(started)} recent appointments, "
          f"{len(cancelled)} recent cancellations")

    responders = results.recent_responder_ids()

    touches = []
    touches += nps.collect(started, responders)
    touches += nps.collect_welcome()
    touches += reactivation.collect(started, cancelled)
    if local.hour == LIFECYCLE_HOUR and local.minute < 10:
        print("Daily lifecycle window — collecting 30/90/180-day + birthday touches")
        touches += lifecycle.collect()
    print(f"{len(touches)} candidate touches")

    enabled = config.MARKETING_LIVE or config.MARKETING_SAFE_MODE
    stats = {"sent": 0, "failed": 0, "shadow": 0,
             "dedup": 0, "consent": 0, "no_contact": 0}
    patients = {}

    for t in touches:
        if sent_log.already_sent(t.patient_id, t.flow_name, t.anchor):
            stats["dedup"] += 1
            continue

        patient = patients.get(t.patient_id)
        if patient is None:
            patient = cliniko.get_patient(t.patient_id) or {}
            patients[t.patient_id] = patient
        if not patient or patient.get("archived"):
            stats["no_contact"] += 1
            continue

        dest = patient.get("mobile") if t.channel == "sms" else patient.get("email")
        if not dest:
            print(f"  [skip] patient {t.patient_id} {t.flow_name}: no {t.channel}")
            stats["no_contact"] += 1
            continue

        if t.is_marketing:
            consented = (patient.get("accepted_sms_marketing") if t.channel == "sms"
                         else patient.get("accepted_email_marketing"))
            if not consented:
                stats["consent"] += 1
                continue

        ctx = dict(t.appt_ctx)
        ctx["first_name"] = patient.get("first_name") or "there"
        if t.trigger_type:
            ctx["survey_link"] = _survey_link(t, patient, ctx)

        ok, info = _deliver(t, ctx, dest)

        if not enabled:
            stats["shadow"] += 1
            print(f"  [shadow] would send {t.flow_name} ({t.channel}) "
                  f"-> patient {t.patient_id}")
            continue

        status = "sent" if ok else f"failed: {info}"
        stats["sent" if ok else "failed"] += 1
        try:
            sent_log.log_send(t.patient_id, patient.get("full_name", ""),
                              t.flow_name, t.channel, t.anchor, t.template_id, status)
        except Exception as e:
            print(f"  WARN: sent_log write failed: {e}")
        print(f"  [{status}] {t.flow_name} ({t.channel}) -> patient {t.patient_id}")

    print(f"Done. {stats}")


if __name__ == "__main__":
    run_once()
