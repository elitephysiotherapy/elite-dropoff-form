"""Tests for the monthly club drop-off alert (send_club_alert_monthly).

Pins the rules that decide whether Sinead is asked to ring a club:
  - which invoice items count as a club, and how variants group together
    (Pilates / renamed insurer items must never be tracked as "clubs"),
  - sharp drop vs the SAME season last year, not month-on-month,
  - gone quiet needs a real history, and ages out after a year,
  - clubs already flagged last month are marked as repeats.

Run: ./venv/bin/python test_club_alert.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import send_club_alert_monthly as alert  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}" + (f"   ({detail})" if detail else ""))
    else:
        failures += 1
        print(f"FAIL  {label}" + (f"   ({detail})" if detail else ""))


def sessions(club, start, n, every_days=7, item=None, patient="p1", price=45.0):
    return [{"club": club, "item": item or club, "date": start + timedelta(days=i * every_days),
             "qty": 1.0, "total": price, "patient_id": f"{patient}-{i % 3}"} for i in range(n)]


# --- item → club -----------------------------------------------------------
check("Pilates is not a club", alert.club_for_item("Pilates Reformer Cookstown (6 weeks)") is None)
check("renamed insurer item is not a club", alert.club_for_item("Axa - Physiotherapy - Review") is None)
check("online programme is not a club", alert.club_for_item("Knee Performance Online Programme") is None)
check("non-affiliated charge has no named club (prefix match)",
      alert.club_for_item("Non-Affiliated Club Charge. Please avoid using this") is None)
check("case/whitespace tolerant", alert.club_for_item("  pilates  matwork maghera ") is None)
check("Bellaghy Review → Bellaghy Wolfe Tones", alert.club_for_item("Bellaghy Review") == "Bellaghy Wolfe Tones")
check("Kildress rename lands on the same club",
      alert.club_for_item("Kildress Youth (underage)") == alert.club_for_item("Kildress underage"))
check("Tyrone Ladies is NOT folded into Tyrone GAA",
      alert.club_for_item("Tyrone Ladies") != alert.club_for_item("Tyrone GAA"))
check("unlisted item is its own club", alert.club_for_item("Lissan") == "Lissan")

# Parity with the finance category list, when that repo is on this machine.
fin = Path.home() / "elite-finance-officer" / "config" / "cliniko_category_mapping.json"
if fin.exists():
    m = json.loads(fin.read_text())["item_to_category"]
    finance_non_club = {alert._norm(i) for cat, items in m.items()
                        if not cat.startswith("_") and isinstance(items, list) for i in items}
    ours = {alert._norm(i) for i in config.CLUB_ALERT_NON_CLUB_ITEMS}
    check("non-club list matches finance category list",
          finance_non_club == ours,
          f"only finance: {sorted(finance_non_club - ours)}; only here: {sorted(ours - finance_non_club)}")

# --- windows ---------------------------------------------------------------
w = alert.windows(date(2026, 10, 1))
check("current window = last 3 complete months", w["cur"] == (date(2026, 7, 1), date(2026, 10, 1)))
check("baseline = same 3 months last year", w["prev"] == (date(2025, 7, 1), date(2025, 10, 1)))

AS_OF = date(2026, 10, 1)

# --- sharp drop ------------------------------------------------------------
rows = sessions("Dropper", date(2025, 7, 1), 26, every_days=3) + sessions("Dropper", date(2026, 7, 1), 6, every_days=14)
res = alert.evaluate(rows, AS_OF)
check("40%+ drop on a real baseline is flagged", [d["club"] for d in res["drops"]] == ["Dropper"])
check("value lost uses last year's price", round(res["drops"][0]["value_lost"]) == (26 - 6) * 45)

rows = sessions("Small", date(2025, 7, 1), 8, every_days=10) + sessions("Small", date(2026, 7, 1), 2, every_days=10)
check("small club under the baseline is not flagged", not alert.evaluate(rows, AS_OF)["drops"])

rows = sessions("Seasonal", date(2025, 7, 1), 20, every_days=4) + sessions("Seasonal", date(2026, 7, 1), 18, every_days=4)
check("steady vs same season last year is not flagged", not alert.evaluate(rows, AS_OF)["drops"])

# --- gone quiet ------------------------------------------------------------
rows = (sessions("Quiet", date(2025, 6, 1), 40, every_days=7)       # busy through the year before…
        + sessions("Quiet", date(2026, 6, 20), 1))                 # …last seen 20 Jun, nothing since
res = alert.evaluate(rows, AS_OF)
check("8+ weeks silent after a busy year is flagged", [q["club"] for q in res["quiet"]] == ["Quiet"])
check("gone quiet is not double-listed as a drop", "Quiet" not in [d["club"] for d in res["drops"]])

rows = sessions("OffSeason", date(2025, 1, 5), 20, every_days=7) + sessions("OffSeason", date(2026, 1, 5), 20, every_days=7)
check("quiet in a stretch that was also quiet last year is not flagged",
      not alert.evaluate(rows, date(2026, 11, 1))["quiet"])

rows = sessions("LongGone", date(2024, 6, 1), 40, every_days=7)     # last session ~Mar 2025
check("club silent for over a year has aged out", not alert.evaluate(rows, AS_OF)["quiet"])

rows = sessions("Sporadic", date(2025, 8, 20), 4, every_days=7)
check("club with <10 sessions in the prior year is not flagged as quiet", not alert.evaluate(rows, AS_OF)["quiet"])

# --- repeats + DM ----------------------------------------------------------
REPEAT_AS_OF = date(2026, 11, 1)   # after CLUB_ALERT_FIRST_RUN, so repeats apply
rows = sessions("Quiet", date(2025, 5, 1), 45, every_days=7) + sessions("Quiet", date(2026, 6, 20), 1)
rep = alert.build_report(rows, REPEAT_AS_OF)
flags = {f["club"]: f["repeat"] for f in rep["drops"] + rep["quiet"]}
check("club flagged last month is a repeat", flags == {"Quiet": True}, str(flags))
text = alert.build_dm_text(rep, REPEAT_AS_OF)
check("repeat goes on the one-line summary", "*Still flagged from last month:* Quiet" in text)
check("repeat isn't also listed in full", "Gone quiet" not in text)
check("DM greets Sinead", text.startswith("Good morning Sinead,"))
check("no flags → reassuring message",
      "No clubs flagged" in alert.build_dm_text(alert.build_report([], AS_OF), AS_OF))

first = alert.build_report(rows, date.fromisoformat(config.CLUB_ALERT_FIRST_RUN))
check("first ever run lists every club in full (no repeats)",
      not any(f["repeat"] for f in first["drops"] + first["quiet"]))

print()
print("ALL PASS" if not failures else f"{failures} FAILURE(S)")
sys.exit(1 if failures else 0)
