"""Runs the always-on bots — as its own Render Background Worker.

Why this exists (2026-11-15): until now speed-to-lead, the team-sheet sync,
the daily comms check and the campaign sender ran as background threads
inside the web server (server.py). gunicorn started them in its master
process and then forked the worker; the worker inherited the threads' open
/tmp/*.lock files, and because flock is per open file the idle worker held
all four locks forever. From Sun 8 Nov every bot tick found its lock "taken"
and silently skipped — for six days — while the web routes kept answering.

The fix is structural, not a watchdog:
  * No gunicorn, no threads, no forking of a process that holds locks. This
    supervisor runs NO bot code and makes no network calls itself.
  * Every run of every bot is a brand-new Python process (close_fds, own
    process group) with a hard wall-clock limit. Anything a run holds — locks,
    connections — dies with it. If a run hangs for any reason it is killed at
    its limit and the next run starts clean a minute (or a few) later.
  * Each bot keeps its own error alerts (exceptions -> DM); a bot that can't
    take its lock now says so in the log instead of skipping silently.
    Memory between runs (failure counts, once-a-day markers, campaign send
    guards) is in bot_state.py.

Usage:
  python -u bot_worker.py            run forever (Render start command)
  python -u bot_worker.py --dry      speed-to-lead only, dry run (no texts, no
                                     Slack, no sheet writes), for local testing
  python -u bot_worker.py --alert "msg"   (internal) DM Martin, then exit
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

HANDLED_ERROR = 3     # child exit code: error handled + alerted by the bot itself
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


@dataclass
class Job:
    name: str
    args: list[str]            # arguments after `python -u`
    every: int                 # seconds between run STARTS
    timeout: int               # hard wall-clock limit for one run
    first_delay: int = 0       # stagger so jobs don't all start together
    alert_after: tuple = ()    # consecutive timeouts that trigger a DM
    alert_text: str = ""
    # runtime
    proc: subprocess.Popen | None = None
    started: float = 0.0
    next_at: float = 0.0
    timeouts: int = field(default=0)   # consecutive timeouts/crashes
    hb: dict = field(default_factory=lambda: {"ok": 0, "crash": 0, "timeout": 0})


def build_jobs(dry: bool) -> list[Job]:
    if dry:
        return [Job("speed_to_lead", ["speed_to_lead.py", "--once", "--dry"],
                    every=60, timeout=50)]
    jobs = []
    if os.environ.get("STL_ENABLED", "1") == "1":
        jobs.append(Job(
            "speed_to_lead", ["speed_to_lead.py", "--once"],
            every=60, timeout=50, first_delay=0,
            alert_after=(5, 60),
            alert_text="Speed-to-lead runs keep timing out — new Meta leads are "
                       "*not* being texted or posted."))
    if os.environ.get("TEAM_SHEET_SYNC", "1") == "1":
        jobs.append(Job(
            "team_sheet", ["team_sheet.py", "--once"],
            every=1800, timeout=240, first_delay=20,
            alert_after=(2, 16),
            alert_text="Team drop-off sheet sync keeps timing out — physio "
                       "feedback is *not* reaching the master sheet."))
    if os.environ.get("COMMS_CHECK_ENABLED", "1") == "1":
        jobs.append(Job(
            "comms_check", ["comms_check.py", "--once-if-due"],
            every=300, timeout=600, first_delay=40,
            alert_after=(3,),
            alert_text="The daily comms check keeps timing out and hasn't run."))
    if os.environ.get("DAY_BEFORE_CALLS_ENABLED", "1") == "1":
        # 09:00 Mon-Fri DM to reception: tomorrow's higher-risk IAs to ring.
        # Runs every 5 min; the script itself posts once per day in 09:00-10:59.
        jobs.append(Job(
            "day_before_calls", ["send_day_before_calls.py", "--once-if-due"],
            every=300, timeout=300, first_delay=50,
            alert_after=(3,),
            alert_text="The 9am day-before IA call list keeps failing — reception "
                       "has *not* been sent today's list."))
    if os.environ.get("SAVE_CALL_ENABLED", "1") == "1":
        # Cancelled / no-show first visits -> #leads straight away (Script 3).
        jobs.append(Job(
            "save_calls", ["save_call_alert.py", "--once"],
            every=300, timeout=240, first_delay=70,
            alert_after=(3,),
            alert_text="The save-call alert keeps timing out — cancelled first "
                       "visits are *not* being flagged to the desk."))
    if os.environ.get("REACT_ENABLED", "1") == "1":
        # 10:00 Mon–Fri reactivation call cards in #reactivationcalls.
        # Runs every 5 min; the script posts once per working day 10:00–11:59.
        jobs.append(Job(
            "reactivation_calls", ["reactivation_calls.py", "--once-if-due"],
            every=300, timeout=290, first_delay=80,
            alert_after=(3,),
            alert_text="The 10am reactivation call cards keep timing out — "
                       "reception has *not* had today's list."))
    if os.environ.get("NOT_NOW_ENABLED", "1") == "1":
        # 09:30 weekdays: DM Sinéad Rocks leads who said "not now" 30 days ago.
        jobs.append(Job(
            "not_now_reminders", ["not_now_reminders.py", "--once-if-due"],
            every=300, timeout=180, first_delay=90,
            alert_after=(3,),
            alert_text="The day-30 \"not now\" reminders keep timing out."))
    if os.environ.get("CAMPAIGN_MODE", "off").strip().lower() != "off":
        # A full send round paces at 4 s/send (~410 texts on the busiest day
        # ≈ 30 min). The limit leaves room; a killed round is safe because
        # each send claims its cell ("sending …") before it goes.
        jobs.append(Job(
            "campaign", ["campaign.py", "--once"],
            every=300, timeout=3000, first_delay=60,
            alert_after=(1,),
            alert_text="A campaign run hit its 50-min limit and was stopped. "
                       "Anyone marked 'sending …' in the Campaign tab may not "
                       "have been texted — check before clearing."))
    return jobs


HEARTBEAT_SECS = 3600


def heartbeat(jobs: list[Job]) -> None:
    """One line an hour: runs finished per job since the last line. Read by
    chief_of_staff.py as positive proof each bot is actually running."""
    parts = []
    for j in jobs:
        parts.append(f"{j.name} ok={j.hb['ok']} crash={j.hb['crash']} timeout={j.hb['timeout']}")
        j.hb = {"ok": 0, "crash": 0, "timeout": 0}
    log("heartbeat: " + " | ".join(parts))


def log(msg: str) -> None:
    print(f"[bot_worker] {msg}", flush=True)


def _kill(proc: subprocess.Popen) -> None:
    """Kill the child and anything it started (own process group)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _start(job: Job, env: dict) -> None:
    # stdout/stderr are inherited (Render captures them); -u = unbuffered.
    job.proc = subprocess.Popen([PY, "-u", *job.args], cwd=HERE, env=env,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                close_fds=True)
    job.started = time.time()


def _alert(text: str, env: dict) -> None:
    """Send a DM in a throwaway process with its own hard limit, so the
    supervisor itself never touches the network."""
    try:
        p = subprocess.Popen([PY, "-u", os.path.join(HERE, "bot_worker.py"), "--alert", text],
                             cwd=HERE, env=env, stdin=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            _kill(p)
    except Exception as exc:                      # never let an alert stop the loop
        log(f"could not send alert: {exc}")


def step(jobs: list[Job], env: dict, now: float) -> None:
    """One supervisor pass: reap finished runs, kill overrunning ones, start
    due ones. Never blocks."""
    for job in jobs:
        if job.proc is not None:
            rc = job.proc.poll()
            if rc is None:
                if now - job.started > job.timeout:
                    _kill(job.proc)
                    job.proc = None
                    job.timeouts += 1
                    job.hb["timeout"] += 1
                    log(f"{job.name} timed out after {job.timeout}s — "
                        f"next run starts clean")
                    if job.timeouts in job.alert_after:
                        _alert(f":warning: {job.alert_text} ({job.timeouts} in a row; "
                               f"each is stopped after {job.timeout}s and retried.)", env)
                continue
            job.proc = None
            if rc == 0 or rc == HANDLED_ERROR:     # handled errors alert themselves
                job.timeouts = 0
                job.hb["ok" if rc == 0 else "crash"] += 1
            else:                                  # crashed before its own alerting
                job.timeouts += 1
                job.hb["crash"] += 1
                log(f"{job.name} crashed (exit code {rc}) — next run starts clean")
                if job.timeouts in job.alert_after:
                    _alert(f":warning: {job.alert_text} ({job.timeouts} runs in a row "
                           f"crashed or timed out; last exit code {rc}.)", env)
        if job.proc is None and now >= job.next_at:
            # Schedule from the planned time, not the finish time, so a slow
            # run doesn't drift the cadence; skip missed slots rather than
            # bunching them up.
            job.next_at = max(job.next_at + job.every, now + 1) if job.next_at else now + job.every
            try:
                _start(job, env)
            except Exception as exc:
                log(f"could not start {job.name}: {exc}")


def run(dry: bool = False, max_seconds: float | None = None, tick: float = 1.0,
        jobs: list[Job] | None = None) -> list[Job]:
    env = dict(os.environ)
    env.setdefault("BOT_WORKER_STARTED", str(time.time()))
    env["PYTHONUNBUFFERED"] = "1"
    jobs = jobs if jobs is not None else build_jobs(dry)
    t0 = time.time()
    # Render overlaps old and new instances briefly on a deploy. Wait so the
    # old worker has stopped before this one ticks (no double texts).
    startup = 0 if (dry or max_seconds is not None) else int(os.environ.get(
        "BOT_WORKER_STARTUP_DELAY", "90"))
    for job in jobs:
        job.next_at = t0 + startup + job.first_delay
    log(f"started: {', '.join(f'{j.name}/{j.every}s(limit {j.timeout}s)' for j in jobs)}"
        f"; first runs in {startup}s")

    stopping = {"flag": False}
    next_hb = t0 + HEARTBEAT_SECS

    def _stop(signum, _frame):
        stopping["flag"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stopping["flag"]:
        now = time.time()
        if max_seconds is not None and now - t0 >= max_seconds:
            break
        step(jobs, env, now)
        if now >= next_hb:                         # proof of life for chief_of_staff.py
            heartbeat(jobs)
            next_hb = now + HEARTBEAT_SECS
        time.sleep(tick)

    for job in jobs:                               # shutting down: stop children
        if job.proc is not None and job.proc.poll() is None:
            try:
                os.killpg(job.proc.pid, signal.SIGTERM)
                job.proc.wait(timeout=20)
            except Exception:
                _kill(job.proc)
    log("stopped")
    return jobs


def _send_alert_cli(text: str) -> int:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
    import config
    import slack_notifier
    try:
        slack_notifier._send_dm(config.CEO_SLACK_EMAIL, text, target_label="bot worker")
        return 0
    except Exception as exc:
        print(f"[bot_worker] alert failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    if "--alert" in sys.argv:
        sys.exit(_send_alert_cli(sys.argv[sys.argv.index("--alert") + 1]))
    if "--dry" in sys.argv:
        # Local check only: forces safe mode whatever .env says.
        os.environ["STL_LIVE"] = "false"
        secs = None
        if "--seconds" in sys.argv:
            secs = float(sys.argv[sys.argv.index("--seconds") + 1])
        run(dry=True, max_seconds=secs)
    else:
        run()
