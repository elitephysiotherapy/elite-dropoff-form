"""Rule-tests for plan_week_tab_order — where each 'W/C ' tab belongs.

Cover for the tab that went missing in plain sight: add_worksheet appends, so
'W/C 21 Sep 2026' was created on time but sat at the far right of the workbook,
past every dashboard, instead of beside 'W/C 14 Sep 2026' (Martin, 2026-09-22).

Run: venv/bin/python test_tab_order.py
"""
import sys

import phase1_fetch as p1


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}\n        got={got!r} want={want!r}")
    return ok


results = []

# ---- 1. The real workbook on 22 Sep 2026: new tab stranded at the end ----
real = ["Untitled", "IA Rebook Rate", "Monthly Summary",
        "W/C 07 Sep 2026", "W/C 14 Sep 2026",
        "Performance Dashboard", "Omagh - Reply Index",
        "W/C 21 Sep 2026"]
results.append(check("this week's tab moves in beside last week's",
                     p1.plan_week_tab_order(real),
                     ["Untitled", "IA Rebook Rate", "Monthly Summary",
                      "W/C 07 Sep 2026", "W/C 14 Sep 2026", "W/C 21 Sep 2026",
                      "Performance Dashboard", "Omagh - Reply Index"]))

# ---- 2. Idempotent: a workbook already in order is left exactly as it is ----
tidy = ["Untitled", "W/C 14 Sep 2026", "W/C 21 Sep 2026", "Dashboard"]
results.append(check("nothing moves when the order is already right",
                     p1.plan_week_tab_order(tidy), tidy))

# ---- 3. Sorted by date, not by the string ----
# "W/C 04 May" < "W/C 27 Apr" alphabetically, and Sep < Oct as text but the
# years run the other way. Both must come out in true date order.
results.append(check("dates sort as dates, not as text",
                     p1.plan_week_tab_order(
                         ["W/C 04 May 2026", "W/C 27 Apr 2026",
                          "W/C 05 Oct 2026", "W/C 28 Sep 2026"]),
                     ["W/C 27 Apr 2026", "W/C 04 May 2026",
                      "W/C 28 Sep 2026", "W/C 05 Oct 2026"]))

# ---- 4. Non-weekly tabs keep their own order, and the block's position ----
# The weekly block returns to where the FIRST weekly tab was, so the dashboards
# reception has arranged either side of it stay where they were put.
results.append(check("other tabs keep their relative order",
                     p1.plan_week_tab_order(
                         ["Leads", "W/C 14 Sep 2026", "Dashboard",
                          "W/C 07 Sep 2026", "Physio Trends"]),
                     ["Leads", "W/C 07 Sep 2026", "W/C 14 Sep 2026",
                      "Dashboard", "Physio Trends"]))

# ---- 5. A 'W/C ' tab with an unreadable date is left alone, not guessed at ----
results.append(check("an unparseable W/C tab stays put",
                     p1.plan_week_tab_order(
                         ["W/C old", "W/C 14 Sep 2026", "W/C 07 Sep 2026"]),
                     ["W/C old", "W/C 07 Sep 2026", "W/C 14 Sep 2026"]))

# ---- 6. Degenerate cases don't crash ----
results.append(check("a workbook with no weekly tabs is untouched",
                     p1.plan_week_tab_order(["Dashboard", "Leads"]),
                     ["Dashboard", "Leads"]))
results.append(check("a single weekly tab is untouched",
                     p1.plan_week_tab_order(["Dashboard", "W/C 21 Sep 2026"]),
                     ["Dashboard", "W/C 21 Sep 2026"]))
results.append(check("an empty workbook is untouched", p1.plan_week_tab_order([]), []))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
