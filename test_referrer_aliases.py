"""Tests for referrer normalisation in send_referrers_monthly.

Reception free-types the referrer, so one source arrives spelled many ways and
the counts split. These pin BOTH directions of the risk:
  - the variants that must fold together ("past pt" → "Past patient"), and
  - the near-misses that must NOT ("Glen" is not "Glenullin"; "Sinead Rocks" is
    a person, not Cookstown Fr Rocks). Club counts feed billing-adjacent
    analysis, so a wrong merge is worse than a split count.

Run: ./venv/bin/python test_referrer_aliases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import send_referrers_monthly as ref  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"PASS  {label}" + (f"   ({detail})" if detail else ""))
    else:
        failures += 1
        print(f"FAIL  {label}" + (f"   ({detail})" if detail else ""))


def canonical(s):
    """What the report will count this spelling as."""
    key = ref._ref_key(s)
    if key in ref.NOT_RECORDED_KEYS:
        return "[not recorded]"
    return ref.ALIAS_LOOKUP.get(key, s)


# ─── Folding: case and punctuation need no alias entry ──────────────────────
check("case folds", ref._ref_key("CLONOE") == ref._ref_key("clonoe"))
check("punctuation folds", ref._ref_key("Fr.Rocks") == ref._ref_key("Fr Rocks"))
check("whitespace collapses", ref._ref_key("  Past   pt ") == "past pt")

# ─── Must merge ─────────────────────────────────────────────────────────────
MERGE = [
    ("past pt", "Past patient"),
    ("Past pt", "Past patient"),
    ("p pt", "Past patient"),
    ("Previous", "Past patient"),
    ("self ref", "Self referral"),
    ("self refferal", "Self referral"),
    ("plab", "Performance Lab"),
    ("Per Lab 60", "Performance Lab"),
    ("Perf Lab", "Performance Lab"),
    ("Fr.Rocks", "Cookstown Fr Rocks"),
    ("Cookstown Fr Roks", "Cookstown Fr Rocks"),
    ("Cookstown", "Cookstown Fr Rocks"),
    ("Rock", "Rock St Patrick's"),
    ("Rock St Patricks", "Rock St Patrick's"),
    ("Ballaghy", "Bellaghy"),
    ("Bellaghy U16", "Bellaghy"),
    ("Drumsern", "Drumsurn"),
    ("Eoghan Ruadh", "Eoghan Ruadh Dungannon"),
    ("Walk in", "Walk-in"),
]
for spelling, want in MERGE:
    got = canonical(spelling)
    check(f"{spelling!r} → {want!r}", got == want, f"got {got!r}")

# ─── Must NOT merge — the wrong-merge risks ─────────────────────────────────
KEEP = [
    "Sinead Rocks",     # Ops Manager — neither Rock nor Fr Rocks
    "Glen",             # Watty Graham's Glen — a different club to Glenullin
    "Glenullin",
    "Ballinascreen",    # three genuinely different clubs, all close spellings
    "Ballinderry",
    "Dungannon",        # the town or the club? ambiguous, left visible
    "Lavey",
    "Clonoe",
    "Ann Boylan",       # named individuals are distinct referrers
    "Dr Faulkner",
    "past pt (lavey)",  # compound — names two sources, left for a human
    "Donaghmore / pt",
]
for spelling in KEEP:
    got = canonical(spelling)
    check(f"{spelling!r} left alone", got == spelling, f"became {got!r}")

# ─── Rock St Patrick's vs Cookstown Fr Rocks ────────────────────────────────
# Two different clubs that share the word "Rocks". Combined on the first pass;
# Martin corrected it 2026-08-17. This is the regression guard for that.
rock = canonical("Rock")
frrocks = canonical("Fr Rocks")
check("'Rock' is Rock St Patrick's, NOT Fr Rocks",
      rock == "Rock St Patrick's", f"got {rock!r}")
check("Rock and Fr Rocks stay two separate clubs", rock != frrocks,
      f"{rock!r} vs {frrocks!r}")
check("'Cookstown' IS the Fr Rocks club",
      canonical("Cookstown") == "Cookstown Fr Rocks",
      f"got {canonical('Cookstown')!r}")
check("'Sinead Rocks' is neither club",
      canonical("Sinead Rocks") not in (rock, frrocks),
      f"got {canonical('Sinead Rocks')!r}")
check("'Rock / Marty' compound left for a human",
      canonical("Rock / Marty") == "Rock / Marty",
      f"got {canonical('Rock / Marty')!r}")

# ─── "No referrer" entries are not a referrer named "?" ─────────────────────
for spelling in ["?", "unknown", "not given"]:
    check(f"{spelling!r} counts as no referrer",
          canonical(spelling) == "[not recorded]", f"got {canonical(spelling)!r}")

# ─── The alias map itself must be unambiguous ──────────────────────────────
try:
    ref._build_alias_lookup()
    check("alias map has no variant claimed twice", True)
except ValueError as e:
    check("alias map has no variant claimed twice", False, str(e))

clashes = [c for c in config.REFERRER_ALIASES
           if ref._ref_key(c) in {ref._ref_key(s) for s in config.REFERRER_NOT_RECORDED}]
check("no canonical name is also a 'not recorded' marker", not clashes, str(clashes))

# ─── Suggestions: flag real candidates, stay quiet on the false ones ────────
unmapped = {ref._ref_key(s): s for s in
            ["Sinead Rocks", "Glen", "Glenullin", "Moortown",
             "Ryan Ferris (moortown)", "Ballinascreen", "Ballinderry"]}
sugg = ref.suggest_aliases(unmapped, set(ref.ALIAS_LOOKUP) | set(unmapped))
flagged = {s for s, _, _ in sugg}
check("suggests the real candidate (Ryan Ferris (moortown) ↔ Moortown)",
      any("moortown" in s.lower() for s in flagged), str(flagged))
check("does NOT suggest Glen ↔ Glenullin",
      not any(s in ("Glen", "Glenullin") for s in flagged), str(flagged))
check("does NOT suggest Sinead Rocks ↔ Rock", "Sinead Rocks" not in flagged,
      str(flagged))
check("does NOT suggest Ballinascreen ↔ Ballinderry",
      not any(s.startswith("Ballin") for s in flagged), str(flagged))
check("each pair reported once, not mirrored",
      len(sugg) == len({tuple(sorted((ref._ref_key(s), o))) for s, o, _ in sugg}),
      f"{len(sugg)} suggestion(s)")

# ─── Counting: merged spellings sum, and the audit shows its working ───────
rows = ["Online", "past pt", "Past pt", "Past patient", "p pt",
        "Fr Rocks", "Cookstown", "Rock", "?", "Sinead Rocks"]
counts, with_ref, audit_merged = {}, 0, {}
for raw in rows:
    key = ref._ref_key(raw)
    if key in ref.NOT_RECORDED_KEYS:
        continue
    with_ref += 1
    name = ref.ALIAS_LOOKUP.get(key, raw)
    counts[name] = counts.get(name, 0) + 1
    if ref._ref_key(name) != key:
        audit_merged.setdefault(name, set()).add(raw)

check("four past-patient spellings sum to one count of 4",
      counts.get("Past patient") == 4, str(counts))
check("two Fr Rocks spellings sum to 2", counts.get("Cookstown Fr Rocks") == 2,
      str(counts))
check("Rock counted separately from Fr Rocks, not added to it",
      counts.get("Rock St Patrick's") == 1, str(counts))
check("'?' excluded from the coverage count", with_ref == len(rows) - 1,
      f"{with_ref} of {len(rows)}")
check("Sinead Rocks counted separately", counts.get("Sinead Rocks") == 1)
check("audit records what was folded",
      audit_merged.get("Past patient") == {"past pt", "Past pt", "p pt"},
      str(audit_merged.get("Past patient")))

print("-" * 60)
print("ALL PASS" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
