"""Tests for bot_worker.py (the freeze-proof bot supervisor) — offline, no
network, no texts.

    venv/bin/python test_bot_worker.py

Proves:
  * a run that hangs is killed at its time limit and the next run still starts
  * a run that crashes doesn't stop the supervisor or the other jobs
  * the supervisor never blocks while a child is running
  * campaign send guards survive from one run (process) to the next
  * the campaign's 15-min post-deploy grace counts from the WORKER's start
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bot_worker  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   {detail}"))
    if not ok:
        failures += 1


tmp = Path(tempfile.mkdtemp(prefix="botworker-test-"))


def script(name, body):
    p = tmp / name
    p.write_text(body)
    return str(p)


hang = script("hang.py", f"""
import time, pathlib
pathlib.Path({str(tmp)!r}, "hang_starts").open("a").write("x")
time.sleep(600)
""")
crash = script("crash.py", f"""
import pathlib, sys
pathlib.Path({str(tmp)!r}, "crash_starts").open("a").write("x")
sys.exit(7)
""")
quick = script("quick.py", f"""
import pathlib
pathlib.Path({str(tmp)!r}, "quick_starts").open("a").write("x")
""")

jobs = [
    bot_worker.Job("hang", [hang], every=2, timeout=1),
    bot_worker.Job("crash", [crash], every=1, timeout=5),
    bot_worker.Job("quick", [quick], every=1, timeout=5),
]
t = time.time()
bot_worker.run(max_seconds=6.5, tick=0.2, jobs=jobs)
took = time.time() - t


def count(f):
    p = tmp / f
    return len(p.read_text()) if p.exists() else 0


check("hung run is killed and the next run still starts", count("hang_starts") >= 3,
      f"hang started {count('hang_starts')}x")
check("timeouts are counted for the hung job", jobs[0].timeouts >= 2, jobs[0].timeouts)
check("crashing job keeps being retried", count("crash_starts") >= 4,
      f"crash started {count('crash_starts')}x")
check("other jobs unaffected by a hang + a crash", count("quick_starts") >= 4,
      f"quick started {count('quick_starts')}x")
check("supervisor never blocked on a child (stopped on time)", took < 9, f"{took:.1f}s")
check("no hung child left behind", all(j.proc is None or j.proc.poll() is not None
                                       for j in jobs))

# A handled error (exit 3) is not counted as a crash.
handled = script("handled.py", "import sys; sys.exit(3)")
j = bot_worker.Job("handled", [handled], every=1, timeout=5)
bot_worker.run(max_seconds=2.5, tick=0.2, jobs=[j])
check("handled error (exit 3) isn't treated as a crash", j.timeouts == 0, j.timeouts)

# Campaign guards persist across processes.
state = tmp / "state"
env = dict(os.environ, BOT_STATE_DIR=str(state), CAMPAIGN_MODE="off", STL_LIVE="false")
w = subprocess.run([sys.executable, "-c", """
import campaign
from datetime import date
campaign._sent_in_process[(date(2026, 11, 18), "sms")] += 2
campaign._seen_this_process.add(("123", "Text 1"))
campaign._save_guards()
"""], cwd=HERE, env=env, capture_output=True, text=True)
r = subprocess.run([sys.executable, "-c", """
import campaign
from datetime import date
campaign._load_guards()
print(campaign._sent_in_process[(date(2026, 11, 18), "sms")], ("123", "Text 1") in campaign._seen_this_process)
"""], cwd=HERE, env=env, capture_output=True, text=True)
check("campaign send guards survive into the next run", r.stdout.strip().endswith("2 True"),
      (w.stderr[-300:], r.stdout, r.stderr[-300:]))

# Grace period counts from the worker start, not each run's start.
g = subprocess.run([sys.executable, "-c", """
import time, campaign
print(int(time.monotonic() - campaign._PROCESS_STARTED))
"""], cwd=HERE, env=dict(env, BOT_WORKER_STARTED=str(time.time() - 3600)),
    capture_output=True, text=True)
age = int(g.stdout.strip().splitlines()[-1]) if g.stdout.strip() else -1
check("campaign grace period counts from the worker start", 3590 <= age <= 3700,
      (age, g.stderr[-300:]))

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
