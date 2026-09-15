"""Monthly club drop-off alert — Slack DM to Sinead Rocks (runs on the 1st).

Flags clubs sending us noticeably less work, so someone can pick up the phone
while the relationship is still warm, rather than spotting it at year end. Built
after the 2026-09-15 YTD club review found Ballinderry, Magherafelt and
Carrickmore had halved and Derrytresk/Greenlough had stopped entirely, all
hidden inside a club total that was still growing.

Two flags (thresholds in config.CLUB_ALERT_*):
  Sharp drop  last 3 complete months vs the same 3 months a year earlier.
  Gone quiet  no session in 8 weeks, from a club that sent 10+ sessions in the
              year before and at least one in the same 8 weeks last year.
Both compare against the same season last year, because GAA work is seasonal and
a month-on-month compare would flag half the county every November. Clubs that
were already flagged last month go on one "still flagged" line, not in full.

Data: Cliniko invoice items. The club is only recorded on the invoice-item name
(appointment types are generic), and each item = one session at full value
(the club's share is billed separately via Xero — don't add Xero on top).
Dated by invoice issue date, same as the finance review.

Modes:
  python send_club_alert_monthly.py                     preview only — prints the DM
  python send_club_alert_monthly.py --post              send the DM to Sinead
  python send_club_alert_monthly.py --as-of 2026-09-01  preview as if run that day

SAFE_MODE: when config.SLACK_SAFE_MODE is True, the DM is rerouted to the CEO
with a "[TEST → …]" prefix (handled by slack_notifier._send_dm).
"""

import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config

LONDON = ZoneInfo("Europe/London")
SINEAD_EMAIL = "sinead@elitephysiocookstown.co.uk"
_ID_RE = re.compile(r"/(\d+)/?$")


# ---------------------------------------------------------------------------
# Item → club
# ---------------------------------------------------------------------------
def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


_NON_CLUB = {_norm(n) for n in config.CLUB_ALERT_NON_CLUB_ITEMS}
_NO_NAMED = [_norm(n) for n in config.CLUB_ALERT_NO_NAMED_CLUB]
_GROUP_OF = {_norm(item): club
             for club, items in config.CLUB_ALERT_GROUPS.items() for item in items}


def club_for_item(item_name):
    """The club an invoice item belongs to, or None if it isn't named-club work."""
    key = _norm(item_name)
    if not key or key in _NON_CLUB:
        return None
    if any(key.startswith(p) for p in _NO_NAMED):
        return None
    return _GROUP_OF.get(key, item_name.strip())


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def _add_months(d, n):
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def windows(as_of):
    """All date windows for a run on `as_of` (a date; normally the 1st).

    Half-open [start, end). The "current" window is the last N complete months."""
    month_start = as_of.replace(day=1)
    n = config.CLUB_ALERT_WINDOW_MONTHS
    cur = (_add_months(month_start, -n), month_start)
    prev = (_add_months(cur[0], -12), _add_months(cur[1], -12))
    quiet_start = as_of - timedelta(days=config.CLUB_ALERT_QUIET_DAYS)
    quiet = (quiet_start, as_of)
    quiet_ly = (_shift_year(quiet_start, -1), _shift_year(as_of, -1))
    prior_12m = (_shift_year(quiet_start, -1), quiet_start)
    active_12m = (_shift_year(as_of, -1), as_of)
    earliest = min(prev[0], quiet_ly[0], prior_12m[0])
    return {"cur": cur, "prev": prev, "quiet": quiet, "quiet_ly": quiet_ly,
            "prior_12m": prior_12m, "active_12m": active_12m, "earliest": earliest}


def previous_run_date(as_of):
    """The 1st of the month before `as_of` — last month's run, for new-vs-repeat."""
    return _add_months(as_of.replace(day=1), -1)


def fetch_start(as_of):
    """Earliest invoice date needed for this run AND last month's comparison run."""
    return windows(previous_run_date(as_of))["earliest"]


def _shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:                  # 29 Feb
        return d.replace(year=d.year + years, day=28)


def _in(d, w):
    return w[0] <= d < w[1]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def fetch_club_sessions(start, end):
    """[{club, item, date, qty, total, patient_id}] for club items invoiced in [start, end)."""
    import phase1_fetch
    invoices = {
        inv["id"]: inv for inv in phase1_fetch.fetch_all("/invoices", [
            ("q[]", f"issue_date:>={start.isoformat()}"),
            ("q[]", f"issue_date:<{end.isoformat()}"),
        ])
    }
    print(f"  {len(invoices)} invoices {start} → {end}", flush=True)
    # Items are created when the invoice is; the buffer covers a backdated issue date.
    created_from = (start - timedelta(days=62)).isoformat()
    rows, seen_items = [], 0
    for it in phase1_fetch.fetch_all("/invoice_items",
                                     [("q[]", f"created_at:>={created_from}T00:00:00Z")]):
        seen_items += 1
        m = _ID_RE.search(((it.get("invoice") or {}).get("links") or {}).get("self", ""))
        inv = invoices.get(m.group(1)) if m else None
        if not inv:
            continue
        club = club_for_item(it.get("name", ""))
        if not club:
            continue
        pm = _ID_RE.search(((inv.get("patient") or {}).get("links") or {}).get("self", ""))
        rows.append({
            "club": club,
            "item": (it.get("name") or "").strip(),
            "date": date.fromisoformat(inv["issue_date"][:10]),
            "qty": float(it.get("quantity") or 0),
            "total": float(it.get("total_including_tax") or 0),
            "patient_id": pm.group(1) if pm else None,
        })
    print(f"  {seen_items} invoice items scanned, {len(rows)} club items", flush=True)
    return rows


# ---------------------------------------------------------------------------
# Flags (pure — unit-tested in test_club_alert.py)
# ---------------------------------------------------------------------------
def evaluate(rows, as_of):
    """Flags for a run on as_of, ignoring any row dated on/after as_of.

    Returns {"drops": [...], "quiet": [...], "windows": {...}}."""
    w = windows(as_of)
    stats = defaultdict(lambda: {"cur": 0.0, "prev": 0.0, "prev_value": 0.0,
                                 "cur_players": set(), "prev_players": set(),
                                 "quiet": 0.0, "quiet_ly": 0.0, "prior_12m": 0.0,
                                 "last": None})
    for r in rows:
        d = r["date"]
        if d >= as_of:
            continue
        s = stats[r["club"]]
        if r["qty"] > 0 and (s["last"] is None or d > s["last"]):
            s["last"] = d
        if _in(d, w["cur"]):
            s["cur"] += r["qty"]
            if r["patient_id"]:
                s["cur_players"].add(r["patient_id"])
        if _in(d, w["prev"]):
            s["prev"] += r["qty"]
            s["prev_value"] += r["total"]
            if r["patient_id"]:
                s["prev_players"].add(r["patient_id"])
        for key in ("quiet", "quiet_ly", "prior_12m"):
            if _in(d, w[key]):
                s[key] += r["qty"]

    quiet, drops = [], []
    for club, s in stats.items():
        if (s["quiet"] <= 0
                and s["last"] is not None and _in(s["last"], w["active_12m"])
                and s["prior_12m"] >= config.CLUB_ALERT_QUIET_MIN_PRIOR_12M
                and s["quiet_ly"] >= config.CLUB_ALERT_QUIET_MIN_LAST_YEAR):
            quiet.append({"club": club, "last": s["last"], "prior_12m": s["prior_12m"],
                          "same_period_last_year": s["quiet_ly"]})
            continue                    # one flag per club — "gone quiet" is the stronger one
        lost = s["prev"] - s["cur"]
        if (s["prev"] >= config.CLUB_ALERT_DROP_MIN_BASELINE
                and s["cur"] <= s["prev"] * config.CLUB_ALERT_DROP_MAX_RATIO
                and lost >= config.CLUB_ALERT_DROP_MIN_LOST):
            per_session = s["prev_value"] / s["prev"] if s["prev"] else 0
            drops.append({"club": club, "cur": s["cur"], "prev": s["prev"], "lost": lost,
                          "value_lost": lost * per_session,
                          "cur_players": len(s["cur_players"]),
                          "prev_players": len(s["prev_players"]), "last": s["last"]})
    drops.sort(key=lambda x: (-x["value_lost"], x["club"]))
    quiet.sort(key=lambda x: (-x["prior_12m"], x["club"]))
    return {"drops": drops, "quiet": quiet, "windows": w}


def build_report(rows, as_of):
    """This month's flags, each marked new or repeated from last month's run,
    plus item names first used in the current window (a renamed club item makes
    the old name look like a club that stopped)."""
    now = evaluate(rows, as_of)
    last = evaluate(rows, previous_run_date(as_of))
    flagged_before = {f["club"] for f in last["drops"] + last["quiet"]}
    if as_of <= date.fromisoformat(config.CLUB_ALERT_FIRST_RUN):
        flagged_before = set()          # Sinead never got a previous alert
    for f in now["drops"] + now["quiet"]:
        f["repeat"] = f["club"] in flagged_before
    first_seen = {}
    for r in rows:
        if r["qty"] > 0 and r["date"] < as_of:
            first_seen[r["item"]] = min(first_seen.get(r["item"], r["date"]), r["date"])
    cur = now["windows"]["cur"]
    # Only meaningful when the fetched history reaches back past the window start.
    now["new_items"] = sorted(i for i, d in first_seen.items() if _in(d, cur))
    return now


# ---------------------------------------------------------------------------
# DM
# ---------------------------------------------------------------------------
def _month_range_label(w):
    start, end = w
    last = end - timedelta(days=1)
    if start.year == last.year:
        return f"{start:%b}–{last:%b %Y}"
    return f"{start:%b %Y}–{last:%b %Y}"


def _n(x):
    return f"{int(round(x))}"


def build_dm_text(result, as_of):
    w = result["windows"]
    cur_label = _month_range_label(w["cur"])
    prev_label = _month_range_label(w["prev"])
    new_quiet = [q for q in result["quiet"] if not q["repeat"]]
    new_drops = [d for d in result["drops"] if not d["repeat"]]
    repeats = [f["club"] for f in result["quiet"] + result["drops"] if f["repeat"]]
    lines = ["Good morning Sinead,", "", f"*Club check-in: {as_of:%B %Y}*", ""]

    if not result["quiet"] and not result["drops"]:
        lines.append("No clubs flagged this month. Every club that was sending us regular work "
                     "is still broadly doing so. 👍")
    elif not new_quiet and not new_drops:
        lines.append("No new clubs flagged this month.")
    else:
        lines.append("These clubs are sending us noticeably less work than this time last year. "
                     "Worth a call to find out why.")
    if new_quiet:
        lines += ["", f"*🔴 Gone quiet: no sessions in the last {config.CLUB_ALERT_QUIET_DAYS // 7} weeks*"]
        for q in new_quiet:
            lines.append(f"• *{q['club']}*: last session {q['last']:%-d %b %Y} "
                         f"({_n(q['prior_12m'])} sessions in the year before that)")
    if new_drops:
        lines += ["", f"*🟠 Sharp drop: {cur_label} vs {prev_label}*"]
        for d in new_drops:
            lines.append(f"• *{d['club']}*: {_n(d['cur'])} sessions vs {_n(d['prev'])} "
                         f"({d['cur'] / d['prev'] - 1:+.0%}), players {d['cur_players']} vs "
                         f"{d['prev_players']}, about £{d['value_lost']:,.0f} less work")
    if repeats:
        lines += ["", "*Still flagged from last month:* " + ", ".join(repeats)]
    if result["new_items"]:
        lines += ["", "_Club items used for the first time in over a year (if one is a renamed "
                  "item, the club under its old name may not really have dropped):_ "
                  + ", ".join(result["new_items"])]
    lines += ["", "_Sessions = club items invoiced in Cliniko, compared with the same "
              "period last year because club work is seasonal._"]
    return "\n".join(lines)


def main():
    post = "--post" in sys.argv
    as_of = datetime.now(LONDON).date()
    if "--as-of" in sys.argv:
        as_of = date.fromisoformat(sys.argv[sys.argv.index("--as-of") + 1])
        if post:
            sys.exit("--as-of is for previews only; refusing to --post a backdated alert.")
    start = fetch_start(as_of)
    print(f"Building club alert as of {as_of} (data from {start})…", flush=True)
    rows = fetch_club_sessions(start, as_of)
    result = build_report(rows, as_of)
    text = build_dm_text(result, as_of)
    print(f"\n--- Club alert → Sinead ({SINEAD_EMAIL}) ---\n{text}\n")
    if not post:
        print("(Preview only — re-run with --post to send to Sinead.)")
        return
    import slack_notifier
    ok = slack_notifier._send_dm(SINEAD_EMAIL, text,
                                 target_label=f"Monthly club alert ({as_of:%B %Y})")
    print("Sent." if ok else "FAILED to send.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
