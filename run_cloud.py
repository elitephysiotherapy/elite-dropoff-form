#!/usr/bin/env python3
"""Cloud entry point for Render Cron Jobs.

Render cron schedules are UTC-only, but these jobs must run on Europe/London
local time (which shifts between GMT and BST). To avoid a one-hour drift twice
a year, each Render cron is scheduled to fire at *both* candidate UTC times for
its London target(s), and this dispatcher runs the real job only when the
current *London* time matches a scheduled target. Every other firing exits 0 as
a cheap no-op.

Each clinic job maps to the same command its local launchd wrapper used:
    dropoff   -> phase1_fetch.py --write      (run_daily.sh)
    eod       -> eod_stats.py --post          (eod_poll.sh)
    bookings  -> bookings_fetch.py --write     (bookings_poll.sh)
    progress  -> progress_scan.py             (progress_poll.sh)
    marketing -> python -m marketing.poller    (marketing_poll.sh)

Usage:  python run_cloud.py <job>
"""
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
PY = sys.executable

# Command to run for each job once the London-time guard passes.
COMMANDS = {
    "dropoff":      [PY, "phase1_fetch.py", "--write"],
    "eod":          [PY, "eod_stats.py", "--post"],
    "bookings":     [PY, "bookings_fetch.py", "--write"],
    "progress":     [PY, "progress_scan.py"],
    "marketing":    [PY, "-m", "marketing.poller"],
    "monthly_kpi":  [PY, "send_monthly_physio_kpis.py", "--post"],
}

# Jobs that fire only on the 1st of each calendar month (in addition to the
# London-time hour/minute check). Used by send_monthly_physio_kpis on day 1.
MONTHLY_DAY1_JOBS = {"monthly_kpi"}

# Intended schedule in Europe/London local time.
# Each entry: (weekdays | None, hour, minute).  weekdays is a set with Mon=0..Sun=6;
# None means every day.  marketing runs on a fixed interval, so it has no targets.
TARGETS = {
    "dropoff":  [(None, 7, 0)],                                   # 07:00 daily
    "progress": [({0}, 7, 30)],                                   # Mon 07:30
    "bookings": [(None, 6, 0), (None, 9, 0), (None, 12, 0),
                 (None, 15, 0), (None, 18, 0), (None, 20, 45)],   # 6 daily polls
    "eod":      [({0, 1, 2, 3}, 12, 45), ({0, 1, 2, 3}, 16, 15),
                 ({0, 1, 2, 3}, 20, 45), ({4}, 15, 45)],          # Mon-Thu x3, Fri x1
    "monthly_kpi": [(None, 9, 0)],                                # 09:00 on day 1 (see MONTHLY_DAY1_JOBS)
}

# Minutes after a target time during which a firing still counts as "on time".
# Render reuses the built image for scheduled runs, so the start delay is only a
# few seconds; 15 minutes is a generous safety margin and never overlaps the
# next target in the same hour.
TOLERANCE_MIN = 15


def should_run(job, now):
    """True if `now` (Europe/London) matches a scheduled target for `job`."""
    if job == "marketing":
        return True  # every 10 minutes — timezone-independent
    # Monthly KPI DM only fires on the 1st of the calendar month.
    if job in MONTHLY_DAY1_JOBS and now.day != 1:
        return False
    wd, h, m = now.weekday(), now.hour, now.minute
    for days, th, tm in TARGETS.get(job, []):
        if days is not None and wd not in days:
            continue
        if h == th and tm <= m < tm + TOLERANCE_MIN:
            return True
    return False


def selftest():
    """Read-only cloud connectivity/credentials check. No writes, no Slack
    messages. Returns True only if Google Sheets, Cliniko and Slack all respond."""
    import os
    print("[selftest] read-only cloud checks…", flush=True)
    ok = True
    try:
        import phase1_fetch
        print(f"[selftest] Google Sheets OK — opened {phase1_fetch.open_spreadsheet().title!r}")
    except Exception as e:
        ok = False; print(f"[selftest] Google Sheets FAILED: {e!r}")
    try:
        import phase2
        first = next(iter(phase2.fetch_all("/practitioners", [])), None)
        print(f"[selftest] Cliniko OK — API reachable (got a record: {first is not None})")
    except Exception as e:
        ok = False; print(f"[selftest] Cliniko FAILED: {e!r}")
    try:
        from slack_sdk import WebClient
        a = WebClient(token=os.environ["SLACK_BOT_TOKEN"]).auth_test()
        print(f"[selftest] Slack OK — bot {a.get('user')!r} on team {a.get('team')!r}")
    except Exception as e:
        ok = False; print(f"[selftest] Slack FAILED: {e!r}")
    print(f"[selftest] RESULT: {'ALL OK' if ok else 'FAILURES ABOVE'}")
    return ok


def main():
    if len(sys.argv) < 2:
        print(f"usage: run_cloud.py <{'|'.join(COMMANDS)}|selftest>")
        sys.exit(2)
    job = sys.argv[1]
    if job == "selftest":
        sys.exit(0 if selftest() else 1)
    if job not in COMMANDS:
        print(f"usage: run_cloud.py <{'|'.join(COMMANDS)}|selftest>")
        sys.exit(2)
    now = datetime.now(LONDON)
    stamp = now.strftime("%Y-%m-%d %H:%M %Z")

    if not should_run(job, now):
        print(f"[run_cloud] {job}: {stamp} is not a scheduled London run time — skipping (no-op).")
        return

    cmd = COMMANDS[job]
    print(f"[run_cloud] {job}: {stamp} — running: {' '.join(cmd)}", flush=True)
    rc = subprocess.call(cmd)
    print(f"[run_cloud] {job}: finished with exit code {rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
