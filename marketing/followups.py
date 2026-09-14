"""NPS follow-ups — the poller sends them, the webhook only records.

Until 2026-09-14 the Tally webhook (elite-dropoff-form on Render) sent these
itself, but that service never had the Twilio/Resend keys, so every one failed
from the May cloud migration on. The marketing poller already holds the keys
and the live switches, so it now reads recent responses from 'NPS - Raw Data'
and sends, for each one not yet in the Sent Log:

  Promoter  (9-10) -> review-ask SMS + email with that clinic's review link
  Passive   (7-8)  -> thank-you email to the patient + alert email to Sinead
  Detractor (0-6)  -> apology SMS + email to the patient + alert email to Sinead

Contact details come from Cliniko, not the survey link. Each channel is logged
separately under the response, so a failure on one never re-sends the others,
and a logged failure is not retried (the send-failure alert covers it).
"""

from datetime import datetime, timedelta

import config
from marketing import cliniko, common, detractor, send, sent_log, templates
from marketing.sheets import tab

LOOKBACK_DAYS = 3
FLOW = {"Promoter": "nps_followup_promoter",
        "Passive": "nps_followup_passive",
        "Detractor": "nps_followup_detractor"}
ALERT_FLOW = "nps_alert"
# The one-off review ask (promoter_catchup.py) already reached these patients.
CATCHUP = ("promoter_followup_catchup", "catchup-2026-09")

# 'NPS - Raw Data' columns (0-based), written by detractor._write_raw_row.
DATE, PID, NAME, PHYSIO, CLINIC, TRIGGER, SCORE, CAT, TEXT, CB, CB_NUM = range(11)
RESPONDED, RESPONSE_ID = 12, 13


def parse_date(s):
    for fmt in ("%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def recent_responses(since):
    """Raw Data rows responded on/after `since`, as dicts."""
    out = []
    for r in tab("NPS - Raw Data").get_all_values()[1:]:
        r = r + [""] * (RESPONSE_ID + 1 - len(r))
        d = parse_date(r[RESPONDED]) or parse_date(r[DATE])
        if not r[PID] or r[CAT] not in FLOW or d is None or d < since:
            continue
        out.append({
            "patient_id": r[PID], "full_name": r[NAME], "physio_name": r[PHYSIO],
            "clinic_name": r[CLINIC], "trigger_type": r[TRIGGER],
            "nps_score": r[SCORE], "category": r[CAT], "open_text": r[TEXT],
            "callback_wanted": r[CB] == "Yes", "callback_number": r[CB_NUM],
            "responded": d,
            # Legacy rows have no Tally id; date + score + trigger is unique enough.
            "anchor": r[RESPONSE_ID] or f"{d}-{r[TRIGGER]}-{r[SCORE]}",
        })
    return out


def _patient_messages(resp, patient):
    """[(channel, dest, render)] for the patient, by category."""
    clinic = config.CLINICS.get(resp["clinic_name"]) or {}
    ctx = {"first_name": patient.get("first_name") or "there",
           "clinic_name": resp["clinic_name"],
           "clinic_phone": clinic.get("phone", ""),
           "google_review_url": clinic.get("google_review_url", "")}
    cat = resp["category"]
    msgs = []
    if cat in ("Promoter", "Detractor"):
        tid = "promoter_followup" if cat == "Promoter" else "detractor_followup"
        msgs.append(("sms", patient.get("mobile"),
                     lambda: templates.render_sms(tid, ctx)))
        msgs.append(("email", patient.get("email"),
                     lambda: templates.render_email(tid, ctx)))
    else:
        msgs.append(("email", patient.get("email"),
                     lambda: templates.render_email("passive_followup", ctx)))
    return msgs


def _alert(resp, patient):
    tid = "detractor_alert" if resp["category"] == "Detractor" else "passive_alert"
    r = templates.render_internal(tid, {
        "patient_name": resp["full_name"] or patient.get("full_name", ""),
        "score": resp["nps_score"], "physio_name": resp["physio_name"],
        "clinic_name": resp["clinic_name"],
        "trigger_label": detractor.TRIGGER_LABEL.get(resp["trigger_type"],
                                                     resp["trigger_type"]),
        "appointment_date": "",
        "callback_requested": "Yes" if resp["callback_wanted"] else "No",
        "callback_number": resp["callback_number"] or "(not given)",
        "open_text": resp["open_text"] or "(no comment left)",
        "patient_phone": patient.get("mobile", ""),
        "patient_email": patient.get("email", ""),
    })
    return send.send_email(to=config.NPS_ALERT_EMAIL, subject=r["subject"],
                           html=r["html"], text=r["text"])


def _deliver(channel, dest, render):
    if channel == "sms":
        return send.send_sms(to=dest, body=render())
    e = render()
    return send.send_email(to=dest, subject=e["subject"], html=e["html"],
                           text=e["text"], from_name=e["from_name"],
                           from_email=e["from_email"], reply_to=e["reply_to"])


def _log(resp, patient, flow, channel, status):
    try:
        sent_log.log_send(resp["patient_id"], patient.get("full_name", ""), flow,
                          channel, resp["anchor"], flow, status)
    except Exception as e:
        print(f"  WARN: sent_log write failed: {e}")


def run(since=None, alerts=True, categories=tuple(FLOW)):
    """Send every outstanding follow-up for responses since `since`.
    Returns the stats dict. `alerts=False` skips Sinead's per-response emails
    (the backlog script sends her one digest instead)."""
    since = since or (common.now_utc().date() - timedelta(days=LOOKBACK_DAYS))
    enabled = config.MARKETING_LIVE or config.MARKETING_SAFE_MODE
    stats = {"sent": 0, "failed": 0, "shadow": 0, "done": 0, "no_contact": 0}
    patients, done_this_run = {}, set()
    for resp in recent_responses(since):
        if resp["category"] not in categories:
            continue
        pid, flow, todo = resp["patient_id"], FLOW[resp["category"]], []
        if resp["category"] == "Promoter" and sent_log.already_sent(
                pid, *CATCHUP, within_days=3650, ignore_failed=True):
            stats["done"] += 1
            continue
        if pid not in patients:
            patients[pid] = cliniko.get_patient(pid) or {}
        patient = patients[pid]
        if not patient or patient.get("archived"):
            stats["no_contact"] += 1
            continue

        for channel, dest, render in _patient_messages(resp, patient):
            todo.append((flow, channel, dest, render))
        if alerts and resp["category"] != "Promoter":
            todo.append((ALERT_FLOW, "email-internal", config.NPS_ALERT_EMAIL, None))

        for flow_name, channel, dest, render in todo:
            key = (pid, flow_name, channel, resp["anchor"])
            if key in done_this_run or _channel_done(*key):
                stats["done"] += 1
                continue
            if not dest:
                stats["no_contact"] += 1
                continue
            if not enabled:
                stats["shadow"] += 1
                print(f"  [shadow] would send {flow_name} ({channel}) -> patient {pid}")
                continue
            ok, info = (_alert(resp, patient) if render is None
                        else _deliver(channel, dest, render))
            done_this_run.add(key)   # the Sent Log cache is read once per process
            stats["sent" if ok else "failed"] += 1
            _log(resp, patient, flow_name, channel, "sent" if ok else f"failed: {info}")
            print(f"  [{'sent' if ok else 'failed'}] {flow_name} ({channel}) "
                  f"-> patient {pid}")
    print(f"NPS follow-ups: {stats}")
    return stats


def _channel_done(pid, flow, channel, anchor):
    """Sent Log has a row for this patient + flow + channel + response."""
    rows = sent_log._rows()
    if rows is sent_log._READ_FAILED:
        return True   # can't verify -> never double-send; next cycle retries
    return any(len(r) > 5 and r[1] == str(pid) and r[3] == flow
               and r[4] == channel and r[5] == str(anchor) for r in rows)
