"""Monthly per-physio Slack DM (runs on the 1st of each month).

For each practitioner in config.PRACTITIONER_DISPLAY_ORDER, sends a personalised
Slack DM with the previous calendar month's KPIs in the exact order Martin
asked for (2026-06-01):

  Utilization %
  IAs performed
  IAs DNR (= IADNRs)
  IA Rebook %
  DNAs
  CNAs
  CNA+DNA Drop %
  Total Drop off % (inc IADNRs)
  Total Appointments
  PVA
  Net Promoter Score

Modes:
  python send_monthly_physio_kpis.py            preview only — prints all DMs
  python send_monthly_physio_kpis.py --post     post the DMs to each physio

SAFE_MODE: when config.SLACK_SAFE_MODE is True, every DM is rerouted to the
CEO with a "[TEST → would have gone to <physio>]" prefix.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import phase1_fetch as p1
import phase2

LONDON = ZoneInfo("Europe/London")


def previous_month_window(now=None):
    """Return (start_local, end_local) for the previous calendar month
    (Europe/London). E.g. on 2026-06-01 returns 1 May 2026 → 1 Jun 2026."""
    if now is None:
        now = datetime.now(LONDON)
    end = datetime(now.year, now.month, 1, tzinfo=LONDON)
    if end.month == 1:
        start = datetime(end.year - 1, 12, 1, tzinfo=LONDON)
    else:
        start = datetime(end.year, end.month - 1, 1, tzinfo=LONDON)
    return start, end


def _fmt_pct(v, places=1):
    return f"{v:.{places}f}%" if v is not None else "—"


def _fmt_int(v):
    return str(int(v)) if v is not None else "—"


def _fmt_num(v, places=1):
    return f"{v:.{places}f}" if v is not None else "—"


def build_dm_text(display_name, stats, nps_entry, month_label):
    """The exact wording + ordered metric list Martin specified."""
    nps_val = (nps_entry or {}).get("nps")
    nps_str = str(nps_val) if nps_val is not None else "—"

    review = stats["total_apts"] - stats["nps"]
    total_drops = stats["cnas_review"] + stats["dnas_review"] + stats.get("iadnrs", 0)
    drop_pct = (total_drops / (total_drops + review) * 100) if (total_drops + review) else None

    lines = [
        f"Good morning {display_name},",
        "",
        f"Here are your stats for *{month_label}*. Please update the Therapist Monthly "
        f"KPI sheet on the hub ahead of your Scorecard meeting.",
        "",
        f"• Utilization %:           {_fmt_pct(stats.get('util_pct'))}",
        f"• IAs performed:           {_fmt_int(stats.get('nps'))}",
        f"• IAs DNR:                 {_fmt_int(stats.get('iadnrs'))}",
        f"• IA Rebook %:             {_fmt_pct(stats.get('ia_rebook_pct'))}",
        f"• DNAs:                    {_fmt_int(stats.get('dnas_review'))}",
        f"• CNAs:                    {_fmt_int(stats.get('cnas_review'))}",
        f"• CNA+DNA Drop %:          {_fmt_pct(stats.get('combined_pct'))}",
        f"• Total Drop off % (inc IADNRs): {_fmt_pct(drop_pct)}",
        f"• Total Appointments:      {_fmt_int(stats.get('total_apts'))}",
        f"• PVA:                     {_fmt_num(stats.get('pva'), 1)}",
        f"• Net Promoter Score:      {nps_str}",
    ]
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv

    start_local, end_local = previous_month_window()
    month_label = start_local.strftime("%B %Y")
    print(f"Building monthly KPI DMs for {month_label}…", flush=True)

    stats_by_display = phase2.monthly_stats_per_physio(
        start_local.astimezone(__import__('datetime').timezone.utc),
        end_local.astimezone(__import__('datetime').timezone.utc),
    )
    nps_by_physio = p1.compute_nps_by_physio(start_local, end_local)

    if post:
        import slack_notifier  # uses SLACK_BOT_TOKEN + SAFE_MODE logic

    sent = failed = skipped = 0
    for display_name in config.PRACTITIONER_DISPLAY_ORDER:
        s = stats_by_display.get(display_name)
        if not s or s.get("total_apts", 0) == 0:
            print(f"  [skip] {display_name}: no activity in {month_label}")
            skipped += 1
            continue

        email = config.PHYSIO_SLACK_EMAIL.get(display_name)
        if not email:
            print(f"  [skip] {display_name}: no Slack email configured")
            skipped += 1
            continue

        text = build_dm_text(display_name, s, nps_by_physio.get(display_name), month_label)
        print()
        print(f"--- {display_name} ({email}) ---")
        print(text)
        print()

        if not post:
            continue

        ok = slack_notifier._send_dm(
            email, text,
            target_label=f"Monthly KPI ({month_label}) → {display_name}",
        )
        if ok:
            sent += 1
        else:
            failed += 1

    print()
    print(f"Done. sent={sent} failed={failed} skipped={skipped}")
    if not post:
        print("(Preview only — re-run with --post to send to physios.)")


if __name__ == "__main__":
    main()
