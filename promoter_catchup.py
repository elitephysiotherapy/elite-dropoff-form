"""One-off: send the Google review ask to promoters who never got it.

From the May 2026 cloud migration until 2026-09-14 the webhook server had no
Twilio/Resend keys, so every promoter_followup SMS + email failed. This sends
the same approved promoter_followup templates, with each patient's own clinic
review link, to every promoter who responded in the last WEEKS weeks.

  ./venv/bin/python promoter_catchup.py            # dry run: counts only
  ./venv/bin/python promoter_catchup.py --send     # real send (needs LIVE env)

One ask per patient, however many 9-10s they gave. Logged to the Sent Log
under FLOW/ANCHOR, so a re-run never messages anyone twice. Output is
patient-ID only.
"""

import sys
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv(override=True)

import config
from marketing import cliniko, send, sent_log, templates
from marketing.sheets import tab

WEEKS = 8
FLOW = "promoter_followup_catchup"
ANCHOR = "catchup-2026-09"


def promoters_since(cutoff):
    """Latest promoter response per patient on or after `cutoff`."""
    latest = {}
    for r in tab("NPS - Raw Data").get_all_values()[1:]:
        if len(r) < 8 or r[7] != "Promoter" or not r[1]:
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%d %b %Y").date()
        except ValueError:
            try:
                d = datetime.strptime(r[0].strip(), "%Y-%m-%d").date()
            except ValueError:
                print(f"  [skip] unparseable date {r[0]!r} for patient {r[1]}")
                continue
        if d >= cutoff and (r[1] not in latest or d >= latest[r[1]][0]):
            latest[r[1]] = (d, r[4])
    return latest


def main(really_send):
    if really_send and not (config.MARKETING_LIVE and not config.MARKETING_SAFE_MODE):
        sys.exit("--send needs MARKETING_LIVE=true and MARKETING_SAFE_MODE=false")
    cutoff = (datetime.now(config_tz()) - timedelta(weeks=WEEKS)).date()
    latest = promoters_since(cutoff)
    print(f"Promoters since {cutoff}: {len(latest)} patients | "
          f"mode: {'SEND' if really_send else 'DRY RUN'}")

    stats = {"sms": 0, "email": 0, "failed": 0, "already": 0,
             "no_contact": 0, "archived": 0, "no_clinic": 0}
    by_clinic = {}
    for pid, (_, clinic_key) in sorted(latest.items()):
        clinic = config.CLINICS.get(clinic_key)
        if not clinic or not clinic.get("google_review_url"):
            stats["no_clinic"] += 1
            print(f"  [skip] patient {pid}: no review link for clinic {clinic_key!r}")
            continue
        if sent_log.already_sent(pid, FLOW, ANCHOR, within_days=3650, ignore_failed=True):
            stats["already"] += 1
            continue
        patient = cliniko.get_patient(pid) or {}
        if not patient:
            stats["no_contact"] += 1
            continue
        if patient.get("archived"):
            stats["archived"] += 1
            continue
        if not (patient.get("mobile") or patient.get("email")):
            stats["no_contact"] += 1
            continue
        by_clinic[clinic_key] = by_clinic.get(clinic_key, 0) + 1

        ctx = {"first_name": patient.get("first_name") or "there",
               "clinic_name": clinic_key,
               "clinic_phone": clinic.get("phone", ""),
               "google_review_url": clinic["google_review_url"]}
        results = []
        if patient.get("mobile"):
            if really_send:
                ok, info = send.send_sms(to=patient["mobile"],
                                         body=templates.render_sms("promoter_followup", ctx))
                results.append(("sms", ok, info))
            else:
                stats["sms"] += 1
        if patient.get("email"):
            if really_send:
                e = templates.render_email("promoter_followup", ctx)
                ok, info = send.send_email(
                    to=patient["email"], subject=e["subject"], html=e["html"],
                    text=e["text"], from_name=e["from_name"],
                    from_email=e["from_email"], reply_to=e["reply_to"])
                results.append(("email", ok, info))
            else:
                stats["email"] += 1

        for channel, ok, info in results:
            stats[channel if ok else "failed"] += 1
            sent_log.log_send(pid, patient.get("full_name", ""), FLOW, channel,
                              ANCHOR, "promoter_followup",
                              "sent" if ok else f"failed: {info}")
            print(f"  [{'sent' if ok else 'FAILED ' + str(info)[:80]}] "
                  f"{channel} -> patient {pid} ({clinic_key})")
        if really_send:
            time.sleep(0.6)   # stay under Resend's 2 requests/second

    print(f"By clinic: {by_clinic}")
    print(f"Done. {stats}")


def config_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Europe/London")


if __name__ == "__main__":
    main("--send" in sys.argv)
