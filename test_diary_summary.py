"""Tests for the diary summary DM targets (send_diary_summary).

Pins the rules Sinead and reception rely on:
  - each physio's target comes from config.TEAM "diary_target" and the team
    target is the sum of the bottom of each range (46 IAs | 285 appts),
  - a reduced-hours period scales the target (Erin's 50% phased return),
  - appts include Pilates/rehab class sessions,
  - physios are listed in Martin's fixed order; directors have no target,
  - reception's plain diary never shows targets (they're for Sinead only).

Run: ./venv/bin/python test_diary_summary.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import send_diary_summary as ds  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}" + (f"   ({detail})" if detail else ""))
    else:
        failures += 1
        print(f"FAIL  {label}" + (f"   ({detail})" if detail else ""))


def member(display):
    return next(m for m in config.TEAM if m["display"] == display)


OCT = (date(2026, 10, 5), date(2026, 10, 12))
SEP = (date(2026, 9, 21), date(2026, 9, 28))

# Team floor matches Martin's figures once Erin is back to full hours.
ia_floor = appt_floor = 0
for m in config.TEAM:
    t = ds.week_target(m, *OCT)
    if t:
        ia_floor += t["ias"][0]
        appt_floor += t["appts"][0]
check("team floor in October = 46 IAs | 285 appts",
      (ia_floor, appt_floor) == (46, 285), f"{ia_floor} | {appt_floor}")

check("directors have no target",
      ds.week_target(member("Marty"), *OCT) is None
      and ds.week_target(member("Julie"), *OCT) is None)

check("Erin full target from October", ds.week_target(member("Erin"), *OCT)
      == {"ias": (7, 8), "appts": (40, 40)})
check("Erin halved during her 50% phased return",
      ds.week_target(member("Erin"), *SEP) == {"ias": (4, 4), "appts": (20, 20)},
      str(ds.week_target(member("Erin"), *SEP)))


def c(ias, one2one, pilates=0, rehab=0):
    return {"ias": ias, "one2one": one2one, "pilates": pilates, "rehab": rehab}


counts = {
    "Sinead McGill": c(8, 45), "Erin McNicholl": c(7, 38, pilates=2),
    "Aoife O'Kane": c(7, 30), "Molaí Smith": c(7, 40),
    "Ciara O'Kane": c(6, 35), "Shannagh Conwell": c(4, 30),
    "Kelly Scott": c(4, 30), "Conor O'Hagan": c(4, 18),
    "Martin Loughran": c(1, 20),
}
now = datetime(2026, 10, 7, 8, tzinfo=ds.LONDON)
msg = ds.build_target_message("this", *OCT, counts, now=now)
lines = msg.splitlines()
check("team line sums targeted physios only (excludes directors)",
      "*Physio team: 268 / 285 appts (94%) · 47 / 46 IAs*" in msg,
      next(l for l in lines if l.startswith("*Physio team")))
check("gap line", "17 appts to fill · IAs on target ✅" in lines)
table = [l.strip("`") for l in lines if l.startswith("```") or
         (l and l.split()[0] in config.DIARY_TARGET_ORDER)]
order = [l.split()[0] for l in table[1:]]
check("physios in Martin's fixed order", order == config.DIARY_TARGET_ORDER, str(order))
erin = next(l for l in table if l.startswith("Erin"))
check("Pilates counts toward the appt target (Erin 38 + 2 = 40 → ✓)",
      erin.split()[2] == "40/40" and erin.endswith("✓"), erin)
conor = next(l for l in table if l.startswith("Conor"))
check("gap = short of the bottom of the range", conor.split()[-1] == "12", conor)
check("directors listed underneath without a target",
      "Also on the diary: Marty 20 · Julie 0" in lines,
      next((l for l in lines if l.startswith("Also")), ""))
check("table fits a phone (≤ 34 chars a row)",
      max(len(l) for l in table) <= 34, str(max(len(l) for l in table)))

plain = ds.build_message("this", *OCT, counts, now=now)
check("reception's plain diary has no targets",
      "target" not in plain.lower() and "Martin Loughran" in plain)

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
