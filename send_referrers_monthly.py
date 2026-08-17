"""Monthly referrer analysis Slack DM to Sinead Rocks (runs on the 1st).

Sinead wants a monthly read on who the clinic's best referrers are. The data
lives in the bookings Google Sheet (NOT the drop-off sheet): every booked IA row
carries a "Referrer" column (filled from reception's `Ref: …` booking note, or
"Online" for self-bookings). This script aggregates that column across the
previous calendar month and DMs Sinead a ranked list.

Scope: booked appointments only (the W/C weekly tabs) — the Leads tab is excluded.

Modes:
  python send_referrers_monthly.py            preview only — prints the DM
  python send_referrers_monthly.py --post     send the DM to Sinead

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DM is rerouted to the CEO
with a "[TEST → …]" prefix (handled by slack_notifier._send_dm).
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import difflib
import re

import gspread
from dotenv import load_dotenv
load_dotenv(override=True)

import config
import bookings_fetch as bk
import sheets_retry

LONDON = ZoneInfo("Europe/London")

# Sinead Rocks (Ops Manager) — same address used for the drop-off Ops digest.
SINEAD_EMAIL = "sinead@elitephysiocookstown.co.uk"


def previous_month_window(now=None):
    """(start_local, end_local) for the previous calendar month, Europe/London."""
    if now is None:
        now = datetime.now(LONDON)
    end = datetime(now.year, now.month, 1, tzinfo=LONDON)
    if end.month == 1:
        start = datetime(end.year - 1, 12, 1, tzinfo=LONDON)
    else:
        start = datetime(end.year, end.month - 1, 1, tzinfo=LONDON)
    return start, end


def _parse_dt(s):
    """Parse a bookings-sheet date cell ('YYYY-MM-DD HH:MM' or 'YYYY-MM-DD')."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _ref_key(s):
    """Fold a referrer string to a comparison key: lowercase, punctuation to
    spaces, whitespace collapsed. Handles "Fr.Rocks" ≡ "Fr Rocks" ≡ "fr rocks"
    without needing an alias entry for every spelling."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def _build_alias_lookup():
    """Flatten config.REFERRER_ALIASES into {folded key → canonical name}.

    The canonical name maps to itself too, so "Past patient" and "past pt" land
    on the same entry. Raises on a variant claimed by two canonicals — that's a
    config typo that would otherwise silently attribute bookings to whichever
    key happened to be iterated last.
    """
    lookup = {}
    for canonical, variants in config.REFERRER_ALIASES.items():
        for raw in [canonical, *variants]:
            key = _ref_key(raw)
            if key in lookup and lookup[key] != canonical:
                raise ValueError(
                    f"referrer alias {raw!r} is claimed by both "
                    f"{lookup[key]!r} and {canonical!r} — fix config.REFERRER_ALIASES")
            lookup[key] = canonical
    return lookup


ALIAS_LOOKUP = _build_alias_lookup()
NOT_RECORDED_KEYS = {_ref_key(s) for s in config.REFERRER_NOT_RECORDED}


def _contains_words(haystack, needle):
    """True if every word of `needle` appears in `haystack` as a whole word.

    Both are already folded keys (lowercase, space-separated), so this is a
    word-sequence check rather than a substring one — "glen" does NOT count as
    contained in "glenullin".
    """
    if not needle:
        return False
    hay = haystack.split()
    words = needle.split()
    return any(hay[i:i + len(words)] == words
               for i in range(len(hay) - len(words) + 1))


def suggest_aliases(unmapped, known):
    """Flag unmapped spellings that look like something we already know, so new
    typos surface for review instead of quietly splitting a total.

    Suggests only — nothing is merged on the strength of a similarity score.
    Local club names are genuinely close to each other (Ballinascreen /
    Ballinderry / Ballaghy are three different clubs), so an automatic
    near-match would be exactly the wrong-merge risk the alias map avoids.
    """
    out, seen_pairs = [], set()
    for key, spelling in sorted(unmapped.items()):
        best, score = None, 0.0
        for other in known:
            if other == key:
                continue
            # Whole-word containment ("moortown" inside "ryan ferris moortown")
            # is a strong signal. It must be word-boundary, not any substring:
            # "glen" sits inside "glenullin" but Glen and Glenullin are two
            # different clubs, and "rock" inside "sinead rocks" is a person.
            if _contains_words(other, key) or _contains_words(key, other):
                ratio = 0.95
            else:
                ratio = difflib.SequenceMatcher(None, key, other).ratio()
            if ratio > score:
                best, score = other, ratio
        if best and score >= 0.86:
            # Report each pair once — both halves are usually unmapped, so
            # without this every near-miss is listed twice, mirrored.
            pair = tuple(sorted((key, best)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            out.append((spelling, best, score))
    return out


def _tab_sunday(title):
    """Parse the Sunday date out of a 'W/C DD Mon YYYY' tab title (or None)."""
    try:
        return datetime.strptime(title.replace("W/C ", "").strip(), "%d %b %Y")
    except ValueError:
        return None


def _read_week_tab(ws):
    """Read one W/C week tab, retrying a transient Sheets 429/5xx.

    This read used to be wrapped in `except Exception: WARN … continue`, so a
    quota brush did NOT fail the job — it silently dropped that week's bookings
    and the monthly DM went out undercounted. That's the worst failure mode
    available here: wrong numbers presented as right, with nothing to notice.

    The cron runs at BOTH 07:00 and 08:00 UTC on the 1st, and 07:00 collides
    with the drop-off refresh and two other weeklies on the shared Sheets quota
    (see sheets_retry). So failing loudly is genuinely cheaper than guessing —
    the 08:00 run picks it up an hour later, once the quota window is long clear.

    A tab that has genuinely vanished mid-run is still tolerated: nothing can be
    recovered from it and it's a deliberate deletion, not a quota problem.
    """
    try:
        return sheets_retry.gs_retry(ws.get_all_values, f"{ws.title} read",
                                     prefix="  ")
    except gspread.exceptions.APIError as e:
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            print(f"  WARN {ws.title} vanished mid-run — skipping")
            return []
        raise RuntimeError(
            f"couldn't read {ws.title} after retries — referrer counts would be "
            f"undercounted, so failing instead of sending wrong numbers: {e}"
        ) from e


def collect_referrers(start_local, end_local):
    """Return (counts, total_with_ref, total_rows, audit) for booked IAs whose
    Appointment Date falls in [start_local, end_local). counts: referrer → n,
    with spellings folded onto their canonical name (see config.REFERRER_ALIASES).

    `audit` carries what normalisation did — {"merged": canonical → {raw spellings},
    "not_recorded": n, "suggestions": [(spelling, looks_like, score)]} — so a run
    can show its working rather than just asserting a total."""
    sh = bk.open_spreadsheet()
    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    counts = defaultdict(int)
    display_name = {}          # folded key → first-seen original spelling
    merged = defaultdict(set)  # canonical → raw spellings folded into it
    unmapped = {}              # folded key → first-seen spelling (no alias entry)
    not_recorded = 0
    total_with_ref = 0
    total_rows = 0

    # worksheets() is itself a Sheets metadata read, so it can 429 too.
    all_tabs = sheets_retry.gs_retry(sh.worksheets, "list worksheets", prefix="  ")

    for ws in all_tabs:
        if not ws.title.startswith("W/C "):
            continue
        sunday = _tab_sunday(ws.title)
        # A Sunday-anchored week spans Sunday..Saturday; only read tabs that can
        # overlap the target month (cheap filter to avoid reading every tab).
        if sunday is not None and not (
                start_naive - timedelta(days=8) <= sunday <= end_naive):
            continue
        values = _read_week_tab(ws)
        if not values:
            continue
        header = values[0]
        try:
            ref_i = header.index("Referrer")
            appt_i = header.index("Appointment Date")
        except ValueError:
            continue
        booked_i = header.index("Date Booked") if "Date Booked" in header else None

        for row in values[1:]:
            if len(row) <= ref_i:
                continue
            dt = _parse_dt(row[appt_i] if len(row) > appt_i else "")
            if dt is None and booked_i is not None and len(row) > booked_i:
                dt = _parse_dt(row[booked_i])       # fall back to Date Booked
            if dt is None or not (start_naive <= dt < end_naive):
                continue
            total_rows += 1
            ref = (row[ref_i] or "").strip()
            if not ref:
                continue
            key = _ref_key(ref)
            if not key or key in NOT_RECORDED_KEYS:
                # "?", "unknown", "not given" mean nobody logged one. Counting
                # them as a referrer named "?" overstated the coverage line.
                not_recorded += 1
                continue
            total_with_ref += 1
            canonical = ALIAS_LOOKUP.get(key)
            if canonical:
                key = _ref_key(canonical)
                display_name[key] = canonical      # canonical spelling always wins
                if _ref_key(ref) != key:
                    merged[canonical].add(ref)
            else:
                display_name.setdefault(key, ref)
                unmapped.setdefault(key, ref)
            counts[key] += 1

    named = {display_name[k]: n for k, n in counts.items()}
    audit = {
        "merged": {k: sorted(v) for k, v in sorted(merged.items())},
        "not_recorded": not_recorded,
        "suggestions": suggest_aliases(unmapped, set(ALIAS_LOOKUP) | set(unmapped)),
    }
    return named, total_with_ref, total_rows, audit


def build_dm_text(counts, total_with_ref, total_rows, month_label, audit=None):
    audit = audit or {}
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    lines = [
        f"Good morning Sinead,",
        "",
        f"*Referrer analysis — {month_label}*",
        f"(Based on booked appointments in the bookings sheet, by appointment date.)",
        "",
    ]
    if not ranked:
        lines.append("No referrers were recorded for booked appointments last month.")
    else:
        lines.append(f"Top referrers ({total_with_ref} of {total_rows} bookings had a "
                     f"referrer recorded):")
        lines.append("")
        for i, (ref, n) in enumerate(ranked, 1):
            share = (n / total_with_ref * 100) if total_with_ref else 0
            lines.append(f"  {i}. {ref} — {n} ({share:.0f}%)")
        no_ref = total_rows - total_with_ref
        if no_ref:
            lines.append("")
            unlogged = no_ref - audit.get("not_recorded", 0)
            detail = f"⚠️ {no_ref} booking(s) had no referrer logged"
            if audit.get("not_recorded"):
                detail += (f" ({unlogged} left blank, "
                           f"{audit['not_recorded']} logged as '?'/unknown)")
            lines.append(detail + " — these are excluded. Reception adds the "
                         "referrer via `Ref: …` in the Cliniko booking note, so "
                         "coverage depends on that being filled.")
        # Say when spellings were folded together, so a total that looks higher
        # than last month's is explainable rather than mysterious.
        merged = audit.get("merged") or {}
        if merged:
            n_spellings = sum(len(v) for v in merged.values())
            lines.append("")
            lines.append(f"({n_spellings} alternative spelling(s) were merged into "
                         f"{len(merged)} referrer(s) — e.g. \"past pt\" counts as "
                         f"\"Past patient\".)")
    return "\n".join(lines)


def print_normalisation_audit(audit):
    """Show what normalisation did, for the operator running a preview — kept out
    of Sinead's DM, where it would be noise."""
    merged = audit.get("merged") or {}
    if merged:
        print("--- spellings merged ---")
        for canonical, raws in merged.items():
            print(f"  {canonical} ← {', '.join(repr(r) for r in raws)}")
        print()
    suggestions = audit.get("suggestions") or []
    if suggestions:
        print("--- possible new aliases (NOT merged — review, then add to "
              "config.REFERRER_ALIASES if right) ---")
        for spelling, looks_like, score in suggestions:
            print(f"  {spelling!r} looks like {looks_like!r} ({score:.0%})")
        print()


def main():
    post = "--post" in sys.argv
    start_local, end_local = previous_month_window()
    month_label = start_local.strftime("%B %Y")
    print(f"Building referrer analysis for {month_label}…", flush=True)

    counts, total_with_ref, total_rows, audit = collect_referrers(start_local, end_local)
    text = build_dm_text(counts, total_with_ref, total_rows, month_label, audit)
    print()
    print_normalisation_audit(audit)
    print(f"--- Referrer analysis → Sinead ({SINEAD_EMAIL}) ---")
    print(text)
    print()

    if not post:
        print("(Preview only — re-run with --post to send to Sinead.)")
        return

    import slack_notifier
    ok = slack_notifier._send_dm(
        SINEAD_EMAIL, text,
        target_label=f"Monthly referrer analysis ({month_label})",
    )
    print("Sent." if ok else "FAILED to send.")


if __name__ == "__main__":
    main()
