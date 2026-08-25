"""Report the outcome of an unattended Omagh wave into Slack.

An unattended send that fails silently is worse than one that never ran, so
this always posts something — success, failure, or refused-out-of-hours.

  python omagh_notify.py sent <logfile> <wave-name>
  python omagh_notify.py skipped "<when>" <wave-name>
"""

import os
import re
import sys

from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
            override=True)
CHANNEL = os.environ.get("OMAGH_REPLIES_CHANNEL", "C0BRN146XQ8")


def post(text):
    WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
        channel=CHANNEL, text=text, unfurl_links=False)
    print(f"slack: {text}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "sent"
    wave = sys.argv[3] if len(sys.argv) > 3 else "wave"

    if mode == "skipped":
        when = sys.argv[2] if len(sys.argv) > 2 else "an odd hour"
        post(f":warning: *Omagh {wave} did NOT send.* The scheduled run woke "
             f"at {when}, outside sending hours (09:00-17:00) - most likely "
             f"the Mac was asleep at 10:00. Nobody was texted. Ask Claude to "
             f"send it when you're ready.")
        return

    log = sys.argv[2] if len(sys.argv) > 2 else ""
    body = open(log).read() if log and os.path.exists(log) else ""
    m = re.search(r"done: (\d+) sent, (\d+) failed", body)
    if m:
        sent, failed = int(m.group(1)), int(m.group(2))
        note = "" if not failed else f" :warning: {failed} failed - see {log}"
        post(f":outbox_tray: *Omagh {wave} sent* - {sent} texts just went out "
             f"to previous patients in the Omagh area. Replies will land in "
             f"this channel over the next few hours.{note}")
    else:
        post(f":rotating_light: *Omagh {wave} may have failed.* The run "
             f"produced no result line. Log: `{log}`. Check before re-running "
             f"- the Sent Log prevents double-texting, so a retry is safe.")


if __name__ == "__main__":
    main()
