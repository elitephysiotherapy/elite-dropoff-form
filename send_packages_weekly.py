"""Weekly packages-of-care count → Slack DM to Sinead Rocks (Monday mornings).

Reception/physios post package-of-care sales into the #packages Slack channel.
Every Monday this script counts how many NEW packages were sold in the previous
Mon–Sun week, attributes each to the @-mentioned physio, and DMs Sinead Rocks the
total + a per-physio breakdown.

What counts as one package (Martin's rule, 2026-06-04):
  - NEW package sales only — a post mentioning a POC / package / "paid in full"
    / "purchased" / a 1st instalment.
  - Follow-up instalments ("2nd payment", "second payment", "3rd instalment" …)
    are NOT counted — they're payments toward a package already counted.
  - Pricing announcements (brochures / "unchanged") are ignored.
  - A post with no physio @-mention is still counted, under "Unattributed".

Attribution: the physio @-mentioned in the post. Physio Slack user IDs are
resolved at runtime from config.PHYSIO_SLACK_EMAIL (no hardcoded IDs).

Modes:
  python send_packages_weekly.py            preview only — prints the DM
  python send_packages_weekly.py --post     send the DM to Sinead

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DM is rerouted to the CEO.
"""

import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import slack_notifier

LONDON = ZoneInfo("Europe/London")
SINEAD_EMAIL = "sinead@elitephysiocookstown.co.uk"

# A post is a NEW package sale if it names a package/POC or an outright purchase…
_POC_RE = re.compile(r"\b(poc|package of care|package|paid in full|purchased)\b", re.I)
# …and is NOT a follow-up instalment (an ordinal ≥2 directly before payment/instalment)…
_FOLLOWUP_RE = re.compile(
    r"\b(2nd|3rd|4th|5th|second|third|fourth|fifth|final)\s+(payment|instal?ments?)\b", re.I)
# …and is NOT a pricing announcement.
_ANNOUNCE_RE = re.compile(r"\b(brochures?|unchanged)\b", re.I)
# Slack encodes mentions as <@U123> or <@U123|Display Name>.
_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")


def previous_week_window(now=None):
    """(start_local, end_local) for the previous Mon 00:00 → this Mon 00:00."""
    if now is None:
        now = datetime.now(LONDON)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    return last_monday, this_monday


def _physio_by_userid():
    """Map Slack user_id → physio display name, resolved from clinic emails."""
    out = {}
    for display, email in config.PHYSIO_SLACK_EMAIL.items():
        uid = slack_notifier._user_id_for_email(email)
        if uid:
            out[uid] = display
    return out


def _is_new_package(text):
    if _ANNOUNCE_RE.search(text):
        return False
    if _FOLLOWUP_RE.search(text):
        return False
    return bool(_POC_RE.search(text))


def fetch_week_messages(start_local, end_local):
    """All top-level #packages messages posted in the window (handles paging)."""
    client = slack_notifier._get_client()
    oldest = start_local.timestamp()
    latest = end_local.timestamp()
    messages, cursor = [], None
    while True:
        resp = client.conversations_history(
            channel=config.PACKAGES_CHANNEL_ID,
            oldest=f"{oldest:.6f}", latest=f"{latest:.6f}",
            inclusive=False, limit=200, cursor=cursor)
        messages.extend(resp.get("messages", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return messages


def count_packages(messages, uid_to_physio):
    """Return (per_physio dict, total, examples list) for new-package posts."""
    per_physio = defaultdict(int)
    examples = []
    for m in messages:
        if m.get("subtype"):                    # joins/leaves/etc. — skip
            continue
        text = m.get("text") or ""
        if not _is_new_package(text):
            continue
        physio = "Unattributed"
        for uid in _MENTION_RE.findall(text):
            if uid in uid_to_physio:
                physio = uid_to_physio[uid]
                break
        per_physio[physio] += 1
        examples.append((physio, " ".join(text.split())[:80]))
    total = sum(per_physio.values())
    return per_physio, total, examples


def build_dm_text(per_physio, total, week_start, week_end):
    week_label = f"W/C {week_start.strftime('%d %b %Y')}"
    span = f"{week_start.strftime('%a %d %b')} – {(week_end - timedelta(days=1)).strftime('%a %d %b')}"
    physio_counts = {p: n for p, n in per_physio.items() if p != "Unattributed"}
    n_physios = len(physio_counts)

    lines = [
        "Good morning Sinead,",
        "",
        f"*Packages of care sold — {week_label}*  ({span})",
        "",
        f"• Total new packages sold: *{total}*",
        f"• Physios who sold packages: *{n_physios}*",
    ]
    if total:
        lines.append("")
        # Named physios first (highest first), then Unattributed last.
        ranked = sorted(physio_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        for physio, n in ranked:
            lines.append(f"   • {physio} — {n}")
        if per_physio.get("Unattributed"):
            lines.append(f"   • Unattributed (no physio tagged) — {per_physio['Unattributed']}")
    else:
        lines.append("")
        lines.append("No new packages of care were posted in #packages last week.")
    lines.append("")
    lines.append("_Counts new package sales posted in #packages; follow-up instalment "
                 "payments aren't counted._")
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv
    week_start, week_end = previous_week_window()
    print(f"Counting packages for W/C {week_start.strftime('%d %b %Y')}…", flush=True)

    uid_to_physio = _physio_by_userid()
    try:
        messages = fetch_week_messages(week_start, week_end)
    except Exception as e:
        print(f"ERROR reading #packages: {e}")
        print("The Slack bot must be a MEMBER of #packages and the token needs the "
              "`channels:history` scope. Invite the bot to the channel, then re-run.")
        sys.exit(1)

    per_physio, total, examples = count_packages(messages, uid_to_physio)

    text = build_dm_text(per_physio, total, week_start, week_end)
    print()
    print(f"--- Packages weekly → Sinead ({SINEAD_EMAIL}) ---")
    print(text)
    print()
    if examples:
        print(f"Matched {total} package post(s):")
        for physio, snippet in examples:
            print(f"   [{physio}] {snippet}")
        print()

    if not post:
        print("(Preview only — re-run with --post to send to Sinead.)")
        return

    ok = slack_notifier._send_dm(
        SINEAD_EMAIL, text,
        target_label=f"Weekly packages count (W/C {week_start.strftime('%d %b %Y')})",
    )
    print("Sent." if ok else "FAILED to send.")


if __name__ == "__main__":
    main()
