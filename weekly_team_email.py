"""Monday morning Physio Team Development Thread — Gmail draft automation.

Builds and saves a draft email in Martin's Gmail every Monday at 06:00 UTC
(= 07:00 BST / 06:00 GMT). Martin reviews, adds the manual sections
(Scorecard week reminder, Team CPD topic), and sends.

Content auto-filled from last week's W/C tab on the drop-off sheet + a few
fresh Cliniko queries:
  • Stats — Reviews / drop-offs / drop-off rate / IAs performed / IA rebook %
  • Uncategorised count — rows missing Clinical / Non-Clinical
  • 3 inline charts — Clinical vs Non-Clinical pie, Drop-off Stage bar,
    Drop-offs by Body Part pie
  • Patients off course — clinical drop-offs with no Next Step yet,
    grouped by physio

Auth — uses the existing service account with Google Workspace
domain-wide delegation. The service account's client_id needs the
"https://www.googleapis.com/auth/gmail.compose" scope granted at
admin.google.com → Security → API controls → Domain-wide delegation.
Once that's set, the script impersonates martin@elitephysiocookstown.co.uk
and creates a draft in his mailbox.

Recipients in v1: hard-coded to the 8 physios + Sinead Rocks + Kelly
(addresses sourced from config.PHYSIO_SLACK_EMAIL plus
TEAM_EMAIL_EXTRA_RECIPIENTS below — edit if anyone joins/leaves).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")  # headless on Render
import matplotlib.pyplot as plt
import gspread
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build as gbuild

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import phase1_fetch  # noqa: E402
import phase2  # noqa: E402

LONDON = ZoneInfo("Europe/London")
SENDER = "martin@elitephysiocookstown.co.uk"

# Hardcoded recipients in v1. Pulled from config.PHYSIO_SLACK_EMAIL (8 physios)
# plus these non-physio extras (Sinead Rocks the Ops Manager + reception team):
TEAM_EMAIL_EXTRA_RECIPIENTS: list[str] = [
    "sinead@elitephysiocookstown.co.uk",   # Sinead Rocks (Ops Manager)
    "kelly@elitephysiocookstown.co.uk",
    "ciara@elitephysiocookstown.co.uk",
    "conor@elitephysiocookstown.co.uk",
]

SUBJECT_TEMPLATE = "w/b {monday_label} Physio Team Development Thread"


def _retry_transient(call, label: str, attempts: int = 5):
    """Retry call() on transient network errors (Connection reset, timeouts,
    Google 502/503/504). Exponential backoff: 2, 4, 8, 16 seconds.

    First run on Mon 8 Jun 2026 hit a Connection-reset-by-peer reading the
    drop-off Sheet — gspread doesn't auto-retry, so we wrap critical calls.
    """
    import time as _t
    import socket
    import requests as _req
    delay = 2
    for i in range(attempts):
        try:
            return call()
        except (_req.exceptions.ConnectionError,
                _req.exceptions.Timeout,
                ConnectionResetError,
                socket.timeout) as e:
            if i == attempts - 1:
                raise
            print(f"[email] {label}: transient {type(e).__name__}, "
                  f"retry {i+1}/{attempts-1} in {delay}s", flush=True)
            _t.sleep(delay)
            delay *= 2
        except _req.exceptions.HTTPError as e:
            code = getattr(e.response, "status_code", 0)
            if code in (429, 500, 502, 503, 504) and i < attempts - 1:
                print(f"[email] {label}: HTTP {code}, retry {i+1}/{attempts-1} "
                      f"in {delay}s", flush=True)
                _t.sleep(delay)
                delay *= 2
                continue
            raise

# Drop-off types that count toward "real" drop-offs in the team email.
# Excludes IACNA + IADNA — same convention as the Weekly Drop-off Analysis
# tab (the source of truth Martin uses), which only sums IADNR + CNA + DNA.
COUNTED_DROPOFF_TYPES = {"iadnr", "cancelled", "did_not_attend"}


def _is_counted_dropoff(record: dict) -> bool:
    return str(record.get("Drop-off Type") or "").strip().lower() in COUNTED_DROPOFF_TYPES

# ─── Stats from drop-off sheet ──────────────────────────────────────────────


def _ordinal_day(d: int) -> str:
    """1 → '1st', 2 → '2nd', 11 → '11th', 21 → '21st', 22 → '22nd'."""
    if 10 <= (d % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"{d}{suffix}"


def _last_week_window(today: Optional[datetime] = None) -> tuple[datetime, datetime, datetime]:
    """Return (this_mon, last_mon, last_sun_end).

    this_mon: Monday of the CURRENT week (used in the email subject —
              when the cron fires Monday morning, this_mon = today).
    last_mon: Monday of LAST week (when the stats are from).
    last_sun_end: last_mon + 7 days - 1 second (Sunday 23:59:59).
    All tz-aware Europe/London.

    Override via env var TEST_TODAY=YYYY-MM-DD for testing on non-Monday
    days (e.g. simulate a Monday-morning fire from a Sunday afternoon).
    """
    override = os.environ.get("TEST_TODAY")
    if override and not today:
        today = datetime.strptime(override, "%Y-%m-%d").replace(tzinfo=LONDON)
    else:
        today = (today or datetime.now(LONDON)).astimezone(LONDON)
    days_since_mon = today.weekday()  # Mon=0
    this_mon = (today - timedelta(days=days_since_mon)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    last_mon = this_mon - timedelta(days=7)
    last_sun_end = (last_mon + timedelta(days=7)) - timedelta(seconds=1)
    return this_mon, last_mon, last_sun_end


def _wc_tab_name_for(monday: datetime) -> str:
    """Match phase1_fetch's W/C tab name convention.

    Spotted in existing tabs: 'W/C 25 May 2026' (day not zero-padded).
    """
    return f"W/C {monday.strftime('%-d %b %Y')}"


def _read_weekly_dropoff_analysis(monday: datetime) -> dict:
    """Read EVERYTHING the team email needs from the Weekly Drop-off Analysis
    tab for the given week. Single source of truth (Martin 2026-06-07).

    Returns dict with:
      total           : int  — headline drop-off count for the week
      iadnr/cna/dna   : ints — practitioner-table totals
      stage           : list[(label, count)]  — IADNR + 3 stage buckets
      body            : list[(label, count)]  — legacy-notes rows excluded
      clinical        : list[(label, count)]  — Clinical / Non-Clinical split
    Empty/missing fields stay absent if the section can't be parsed.
    """
    out: dict = {}
    sh = phase1_fetch.open_spreadsheet()
    try:
        ws = sh.worksheet("Weekly Drop-off Analysis")
    except gspread.WorksheetNotFound:
        return out
    rows = _retry_transient(ws.get_all_values, "Weekly Drop-off Analysis read")
    candidate_labels = [
        monday.strftime("W/C %d %b %Y"),
        monday.strftime("W/C %-d %b %Y"),
    ]

    # Find the start of this week's section
    sec_start: Optional[int] = None
    for i, r in enumerate(rows):
        first = (r[0] or "").strip()
        if any(lbl in first for lbl in candidate_labels):
            sec_start = i
            break
    if sec_start is None:
        return out

    # Section ends at the next "W/C " header or after ~30 rows (safety)
    sec_end = len(rows)
    for j in range(sec_start + 1, min(len(rows), sec_start + 60)):
        first = (rows[j][0] or "").strip()
        if first.startswith("W/C ") and not any(lbl in first for lbl in candidate_labels):
            sec_end = j
            break
    section = rows[sec_start:sec_end]

    def _section_pairs(col_label: int, col_count: int) -> list[tuple[str, int]]:
        """Walk the section, collect (label, count) pairs from the two columns
        starting after the 'header' row that names the section.
        """
        # First find the header row within the section
        pairs: list[tuple[str, int]] = []
        header_found = False
        for r in section:
            label = (r[col_label] or "").strip() if len(r) > col_label else ""
            count = (r[col_count] or "").strip() if len(r) > col_count else ""
            if not header_found:
                if label and not count.isdigit():
                    header_found = True
                continue
            if not label:
                # blank → end of this small table
                if pairs:
                    break
                continue
            try:
                pairs.append((label, int(count)))
            except ValueError:
                continue
        return pairs

    # Practitioner table: col 0 (Practitioner) / col 4 (Total)
    for r in section:
        first = (r[0] or "").strip()
        if first == "Total" and len(r) >= 5:
            try:
                out["iadnr"] = int(r[1] or 0)
                out["cna"]   = int(r[2] or 0)
                out["dna"]   = int(r[3] or 0)
                out["total"] = int(r[4] or 0)
            except ValueError:
                pass
            break

    # Stage Drop-off section — cols 6/7
    out["stage"] = _section_pairs(col_label=6, col_count=7)
    # Body Area section — cols 9/10; filter legacy-notes entries
    body = _section_pairs(col_label=9, col_count=10)
    out["body"] = [(l, c) for (l, c) in body if "legacy notes" not in l.lower()]
    # Clinical Split section — cols 12/13
    out["clinical"] = _section_pairs(col_label=12, col_count=13)

    return out


def _read_off_track_review() -> list[tuple[str, str]]:
    """Read the Physio + Patient columns from the Off-Track Review tab.

    Returns a list of (physio, patient) tuples in tab order (the tab is
    already grouped by physio). The tab is refreshed daily by phase1_fetch;
    this Monday cron reads whatever the latest refresh produced.
    """
    sh = phase1_fetch.open_spreadsheet()
    try:
        ws = sh.worksheet("Off-Track Review")
    except gspread.WorksheetNotFound:
        return []
    rows = _retry_transient(ws.get_all_values, "Off-Track Review read")
    # Header is "Physio | Patient | …" — find that row, then collect below
    out: list[tuple[str, str]] = []
    in_data = False
    for r in rows:
        c0 = (r[0] or "").strip() if r else ""
        c1 = (r[1] or "").strip() if len(r) > 1 else ""
        if not in_data:
            if c0.lower() == "physio" and c1.lower() == "patient":
                in_data = True
            continue
        if not c0 or not c1:
            continue
        out.append((c0, c1))
    return out


def _get_wc_records(monday: datetime) -> list[dict]:
    """Read all data rows from the matching W/C tab (one row per drop-off)."""
    sh = phase1_fetch.open_spreadsheet()
    name = _wc_tab_name_for(monday)
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        print(f"[email] no W/C tab named {name!r} — checking close variants")
        # Try a couple of alternates (zero-pad / different month abbrev)
        alts = [
            f"W/C {monday.strftime('%d %b %Y')}",
            f"W/C {monday.strftime('%-d %B %Y')}",
        ]
        for alt in alts:
            try:
                ws = sh.worksheet(alt)
                print(f"[email]   matched {alt!r}")
                break
            except gspread.WorksheetNotFound:
                continue
        else:
            raise RuntimeError(f"no W/C tab found for week starting {monday.date()}")
    return _retry_transient(ws.get_all_records, "W/C tab read")


def _cliniko_count_in_window(
    start: datetime, end: datetime, type_ids: set[str], attended: bool = True,
) -> int:
    """Count individual_appointments of the given types completed in [start, end]."""
    iso_start = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso_end = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    apps = phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={iso_start}"),
        ("q[]", f"starts_at:<={iso_end}"),
    ])
    n = 0
    for a in apps:
        tid = phase2.id_from_link(a.get("appointment_type"))
        if tid not in type_ids:
            continue
        if attended and a.get("did_not_arrive"):
            continue
        n += 1
    return n


def _ia_rebook_rate(start: datetime, end: datetime) -> tuple[int, int, float]:
    """(IAs performed last week, IAs rebooked, percent).

    Reuses phase2's monthly_stats_per_physio at week granularity if it
    exposes that. Simpler fallback: count IAs in window via Cliniko,
    cross-check rebooked patients have a *future* booking.
    """
    iso_start = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso_end = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    apps = list(phase2.fetch_all("/individual_appointments", [
        ("q[]", f"starts_at:>={iso_start}"),
        ("q[]", f"starts_at:<={iso_end}"),
    ]))
    IA_IDS = set(config.PHASE1_DROPOFF_IA_TYPE_IDS)
    performed_ids: list[str] = []
    for a in apps:
        if phase2.id_from_link(a.get("appointment_type")) not in IA_IDS:
            continue
        if a.get("did_not_arrive") or a.get("cancelled_at"):
            continue
        pid = phase2.id_from_link(a.get("patient"))
        if pid:
            performed_ids.append(pid)
    performed = len(performed_ids)
    # Count rebooked = how many of those patients have any future appt at all
    rebooked = 0
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for pid in set(performed_ids):
        future = list(phase2.fetch_all("/individual_appointments", [
            ("q[]", f"patient_id:={pid}"),
            ("q[]", f"starts_at:>{iso_end}"),
        ]))
        if any(not f.get("cancelled_at") for f in future):
            rebooked += 1
    pct = (rebooked / performed * 100) if performed else 0.0
    return performed, rebooked, pct


# ─── Charts ────────────────────────────────────────────────────────────────


_CHART_KW = dict(figsize=(6, 4), dpi=110, facecolor="white")


def _chart_clinical_pie_from_pairs(pairs: list[tuple[str, int]]) -> bytes:
    """Pie of Clinical vs Non-Clinical — driven by the Weekly Drop-off
    Analysis tab's Clinical Split section."""
    cn = Counter()
    for label, count in pairs:
        if "non" in label.lower():
            cn["Non-Clinical"] += count
        else:
            cn["Clinical"] += count
    fig, ax = plt.subplots(**_CHART_KW)
    if sum(cn.values()) == 0:
        ax.text(0.5, 0.5, "(no categorised drop-offs)", ha="center")
        ax.axis("off")
    else:
        labels = ["Clinical", "Non-Clinical"]
        values = [cn["Clinical"], cn["Non-Clinical"]]
        ax.pie(values, labels=labels,
               colors=["#3b82f6", "#ef4444"], autopct="%1.0f%%")
        ax.set_title("Clinical v Non-Clinical")
    return _fig_to_png(fig)


def _chart_dropoff_stage_from_pairs(pairs: list[tuple[str, int]]) -> bytes:
    """Horizontal bar — read straight from the Weekly Drop-off Analysis tab's
    Stage Drop-off section (IADNR + Before Session 3 + Before Session 6 +
    After Session 6, in that order). Keeps Martin's existing convention
    where IADNR is its own bar AND IADNRs are also folded into the
    "Before Session 3" total."""
    cats = {label: count for label, count in pairs}
    fig, ax = plt.subplots(**_CHART_KW)
    labels = list(cats.keys())
    values = [cats[k] for k in labels]
    ax.barh(labels, values, color="#3b82f6")
    ax.set_xlabel("Drop-offs")
    ax.set_ylabel("Stage")
    ax.set_title("Drop-off Stage")
    ax.invert_yaxis()
    for i, v in enumerate(values):
        if v:
            ax.text(v + 0.1, i, str(v), va="center")
    return _fig_to_png(fig)


def _chart_body_part_pie_from_pairs(pairs: list[tuple[str, int]]) -> bytes:
    """Pie of Drop-offs by Body Area — driven by the Weekly Drop-off Analysis
    tab's Body Area section. Legacy-notes entries are already filtered out
    when the tab is read."""
    counts = dict(sorted(pairs, key=lambda x: -x[1]))
    fig, ax = plt.subplots(**_CHART_KW)
    if not counts:
        ax.text(0.5, 0.5, "(no categorised drop-offs)", ha="center")
        ax.axis("off")
    else:
        labels = list(counts.keys())
        values = list(counts.values())
        ax.pie(values, labels=labels, autopct="%1.1f%%", textprops={"fontsize": 8})
        ax.set_title("Drop-offs by Body Part")
    return _fig_to_png(fig)


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ─── HTML body composition ─────────────────────────────────────────────────


def _patients_off_course_rows(records: list[dict]) -> list[tuple[str, str]]:
    """Clinical drop-offs (IADNR/CNA/DNA only) that don't yet have a Next Step set,
    grouped by physio."""
    out: list[tuple[str, str]] = []
    by_physio = defaultdict(list)
    for r in records:
        if not _is_counted_dropoff(r):
            continue
        if str(r.get("Clinical / Non-Clinical") or "").strip().lower() != "clinical":
            continue
        if str(r.get("Next Step (Physio)") or "").strip():
            continue   # already actioned
        physio = str(r.get("Physio") or "?").strip()
        patient = str(r.get("Patient Name") or "?").strip()
        by_physio[physio].append(patient)
    for physio in sorted(by_physio.keys()):
        for patient in by_physio[physio]:
            out.append((physio, patient))
    return out


def _build_html(stats: dict, off_course: list[tuple[str, str]],
                uncategorised: int) -> str:
    """Assemble the email HTML with placeholder cid: references for inline charts."""
    rate_dropoff = (stats["dropoffs"] / stats["reviews"] * 100
                    if stats["reviews"] else 0)
    table_rows = "\n".join(
        f"<tr><td>{p}</td><td>{n}</td></tr>" for (p, n) in off_course
    )

    return f"""<html><body style="font-family: Arial, sans-serif; color: #111;">
<p>Good Morning</p>

<p style="background:#fff8d8;padding:8px;border-left:3px solid #d4a017;">
  <strong>[MANUAL] Scorecard week / non-stat reminders go here — delete this paragraph if not needed]</strong>
</p>

<p>You should have got your monthly stats for your SC sent through earlier there.</p>

<p><strong><u>Last week's stats</u></strong></p>

<p>{stats['reviews']} Reviews, {stats['dropoffs']} drop offs leaves drop off rate at {rate_dropoff:.1f}%</p>

<p>{stats['ias_performed']} IAs performed, {stats['ias_rebooked']} rebooked = {stats['ia_rebook_pct']:.1f}% IA Rebook rate</p>

<p><img src="cid:clinical_pie" style="max-width:540px;"></p>

<p><img src="cid:stage_bar" style="max-width:540px;"></p>

<p><em>[MANUAL] Commentary on drop-off stage split here</em></p>

<p><img src="cid:body_pie" style="max-width:540px;"></p>

<p><em>[MANUAL] Commentary on body-part distribution here</em></p>

<p><strong><u>Patients off course:</u></strong></p>

<table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse;">
  <tr style="background:#f4f4f4;"><th align="left">Physio</th><th align="left">Patient</th></tr>
  {table_rows or '<tr><td colspan="2"><em>(none — every clinical drop-off has a Next Step)</em></td></tr>'}
</table>

<p><strong><u>Team CPD</u></strong></p>
<p><em>[MANUAL] CPD topic for the week goes here]</em></p>

<p>-</p>
<p>Kind Regards</p>

<p>
  Martin Loughran MSc BSc (Hons)<br>
  Clinic Director<br>
  Elite Physiotherapy<br>
  133 Moneymore Rd<br>
  Cookstown<br>
  E: <a href="mailto:Martin@elitephysiocookstown.co.uk">Martin@elitephysiocookstown.co.uk</a><br>
  T: 02886440995<br>
  W: <a href="https://www.elitephysiocookstown.co.uk">www.elitephysiocookstown.co.uk</a>
</p>
</body></html>"""


def _build_recipients() -> tuple[list[str], list[str]]:
    """Return (to_list, cc_list).

    To = all 8 physios + the extras (Sinead Rocks + Kelly).
    CC = empty in v1.
    """
    physios = [email for email in config.PHYSIO_SLACK_EMAIL.values()
               if email and email != SENDER]
    extras = list(TEAM_EMAIL_EXTRA_RECIPIENTS)
    to = physios + extras
    # Dedup while preserving order
    seen: set[str] = set()
    to_unique = [x for x in to if not (x in seen or seen.add(x))]
    return to_unique, []


# ─── Gmail draft creation ─────────────────────────────────────────────────


def _gmail_service():
    """Build an authenticated Gmail API client.

    Uses the existing service_account.json with domain-wide delegation.
    The service account's client_id must be authorised in Workspace admin
    with scope 'https://www.googleapis.com/auth/gmail.compose'.
    """
    raw = os.environ.get("SERVICE_ACCOUNT_JSON")
    scopes = ["https://www.googleapis.com/auth/gmail.compose"]
    if raw:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=scopes)
    else:
        creds = service_account.Credentials.from_service_account_file(
            str(ROOT / "service_account.json"), scopes=scopes)
    delegated = creds.with_subject(SENDER)
    return gbuild("gmail", "v1", credentials=delegated, cache_discovery=False)


def _build_multipart_message(subject: str, to: list[str], cc: list[str],
                              html_body: str, inline_images: dict[str, bytes]) -> str:
    """Return a base64url-encoded MIME message ready for Gmail API."""
    container = MIMEMultipart("related")
    container["From"] = SENDER
    container["To"] = ", ".join(to)
    if cc:
        container["Cc"] = ", ".join(cc)
    container["Subject"] = subject

    alt = MIMEMultipart("alternative")
    container.attach(alt)
    alt.attach(MIMEText(re.sub("<[^>]+>", "", html_body), "plain"))
    alt.attach(MIMEText(html_body, "html"))

    for cid, png_bytes in inline_images.items():
        img = MIMEImage(png_bytes, _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        container.attach(img)

    return base64.urlsafe_b64encode(container.as_bytes()).decode()


# ─── Main ─────────────────────────────────────────────────────────────────


def main() -> int:
    this_mon, last_mon, last_sun = _last_week_window()
    print(f"[email] this week (subject): w/b {_ordinal_day(this_mon.day)} "
          f"{this_mon.strftime('%B %Y')}", flush=True)
    print(f"[email] last week (stats):   {last_mon.date()} → {last_sun.date()}",
          flush=True)

    print("[email] reading W/C tab…", flush=True)
    records = _get_wc_records(last_mon)
    print(f"[email]   {len(records)} drop-off rows")

    print("[email] querying Cliniko for IA + Review counts…", flush=True)
    REVIEW_IDS = {"382563815511823515",   # Review Appointment
                  "382589431795684515",   # Club Follow Up
                  "1558531409491006559",  # PHI Review
                  "1118674366867969498"}  # Mummy MOT Review
    reviews = _cliniko_count_in_window(last_mon, last_sun, REVIEW_IDS)
    ias_perf, ias_rebooked, ia_pct = _ia_rebook_rate(last_mon, last_sun)
    print(f"[email]   reviews={reviews}, ias={ias_perf}, rebooked={ias_rebooked}")

    counted_records = [r for r in records if _is_counted_dropoff(r)]

    # Headline drop-off total + all three chart datasets — read from the
    # Weekly Drop-off Analysis tab (Martin's single source of truth,
    # 2026-06-07). Falls back to W/C-tab counting only if that fails.
    analysis = _read_weekly_dropoff_analysis(last_mon)
    if analysis.get("total"):
        dropoffs_total = analysis["total"]
        print(f"[email]   drop-off total (Weekly Analysis): {dropoffs_total} "
              f"({analysis.get('iadnr',0)} IADNR + {analysis.get('cna',0)} CNA "
              f"+ {analysis.get('dna',0)} DNA)")
    else:
        dropoffs_total = len(counted_records)
        print(f"[email]   drop-off total (W/C fallback):   {dropoffs_total} "
              f"(Weekly Analysis section not found for {last_mon.date()})")

    uncategorised = sum(
        1 for r in counted_records
        if not str(r.get("Clinical / Non-Clinical") or "").strip())

    stats = {
        "reviews": reviews,
        "dropoffs": dropoffs_total,
        "ias_performed": ias_perf,
        "ias_rebooked": ias_rebooked,
        "ia_rebook_pct": ia_pct,
    }

    # Patients off course — read from the Off-Track Review tab (refreshed
    # daily by phase1_fetch). One row per patient flagged off-track this week.
    off_course = _read_off_track_review()
    print(f"[email]   patients off course (Off-Track Review tab): {len(off_course)}")

    print("[email] generating charts…", flush=True)
    images = {
        "clinical_pie": _chart_clinical_pie_from_pairs(analysis.get("clinical", [])),
        "stage_bar":    _chart_dropoff_stage_from_pairs(analysis.get("stage", [])),
        "body_pie":     _chart_body_part_pie_from_pairs(analysis.get("body", [])),
    }
    print("[email]   3 charts built")

    html = _build_html(stats, off_course, uncategorised)
    to, cc = _build_recipients()
    print(f"[email] recipients: {to}")

    # Subject uses THIS week's Monday (= the Monday we're sending in),
    # not last week's. E.g. cron fires Mon 8 Jun → subject "w/b 8th June…"
    subject = SUBJECT_TEMPLATE.format(
        monday_label=f"{_ordinal_day(this_mon.day)} {this_mon.strftime('%B')}"
    )
    raw = _build_multipart_message(subject, to, cc, html, images)

    print("[email] creating Gmail draft…", flush=True)
    svc = _gmail_service()
    res = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    draft_id = res.get("id")
    print(f"[email] draft created: id={draft_id}, subject={subject!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
