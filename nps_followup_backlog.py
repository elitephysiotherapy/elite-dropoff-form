"""One-off: the passive + detractor follow-ups the broken webhook never sent.

Sends each passive/detractor from the last WEEKS weeks their normal follow-up
(marketing/followups.py), logged under the same Sent Log keys so the poller
never repeats them. Sinead gets ONE digest instead of a burst of alerts.
Promoters were handled by promoter_catchup.py.

  ./venv/bin/python nps_followup_backlog.py          # dry run
  ./venv/bin/python nps_followup_backlog.py --send   # needs LIVE env
"""

import sys
from datetime import timedelta

from dotenv import load_dotenv
load_dotenv(override=True)

import config
from marketing import common, followups, send

WEEKS = 8


def digest(responses):
    lines = [f"While the survey follow-ups were broken (late May to 14 Sept), these "
             f"{len(responses)} passive/detractor responses from the last {WEEKS} weeks "
             f"never reached you. Each patient has now had their normal follow-up "
             f"email (plus the apology text for detractors).", ""]
    for r in sorted(responses, key=lambda r: r["responded"]):
        lines.append(f"{r['responded']:%d %b}  {r['score_line']}  {r['full_name']} "
                     f"({r['clinic_name']}, seen by {r['physio_name'] or 'unknown'})")
        lines.append(f"    \"{r['open_text'] or 'no comment left'}\"")
        if r["category"] == "Detractor":
            lines.append(f"    Callback requested: {'Yes' if r['callback_wanted'] else 'No'} "
                         f"{r['callback_number']}")
        lines.append("")
    lines.append("All of these are also in NPS - Raw Data. From today, new ones "
                 "reach you within 10 minutes again.")
    return "\n".join(lines)


def main(really_send):
    if really_send and not (config.MARKETING_LIVE and not config.MARKETING_SAFE_MODE):
        sys.exit("--send needs MARKETING_LIVE=true and MARKETING_SAFE_MODE=false")
    if not really_send:
        config.MARKETING_LIVE = config.MARKETING_SAFE_MODE = False   # shadow
    since = common.now_utc().date() - timedelta(weeks=WEEKS)
    backlog = [r for r in followups.recent_responses(since)
               if r["category"] in ("Passive", "Detractor")]
    for r in backlog:
        r["score_line"] = f"{r['nps_score']}/10 {r['category']}"
    print(f"Passive/detractor responses since {since}: {len(backlog)} "
          f"| mode: {'SEND' if really_send else 'DRY RUN'}")

    followups.run(since=since, alerts=False, categories=("Passive", "Detractor"))

    text = digest(backlog)
    if really_send and backlog:
        ok, info = send.send_email(
            to=config.NPS_ALERT_EMAIL,
            subject=f"{len(backlog)} survey responses you missed (passive/detractor, last {WEEKS} weeks)",
            text=text, html=None)
        print(f"Sinead digest: {'OK' if ok else 'FAIL'} {info}")
    else:
        print(f"Sinead digest would list {len(backlog)} responses.")


if __name__ == "__main__":
    main("--send" in sys.argv)
