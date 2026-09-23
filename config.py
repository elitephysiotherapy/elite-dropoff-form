"""Editable configuration — update when team / clinic / appointment types change.

This is the only file a non-developer should ever need to edit.

After editing, no other files need to change. The script picks up new values on
the next run.
"""

import os
from datetime import date, datetime, timedelta

# ===========================================================================
# IA APPOINTMENT TYPE IDS
# ===========================================================================

# Real IAs used for DROP-OFF detection (these are expected to have follow-ups).
# Don't add one-and-done types here — they would create false drop-offs.
PHASE1_DROPOFF_IA_TYPE_IDS = {
    "382563815654429852",   # 1. Initial Appointment
    "392015278608749674",   # 3. Club Initial Assessment
    "1558530673046721630",  # 5. Private Health Insurance Initial Assessment
    "945551547020874765",   # 7. ACL Initial Assessment
}

# Broader list — used to identify the start of a patient's current episode of care
# so the AI body-area categoriser knows which clinical notes are relevant.
# Includes one-and-done types (Pelvic Health Assessment, etc.).
PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS = PHASE1_DROPOFF_IA_TYPE_IDS | {
    "1521627460095973060",  # 2. Sports & MSK Clinical Consultation
    "1118674052857206233",  # Mummy MOT Initial Assessment
    "1194028405859816854",  # Pelvic Health Assessment
    "1396206071189608060",  # Club Consultation
}

# Even broader — used for the weekly/monthly "New Patients" (NPs) count.
# Includes diagnostic, profiling and screening types that Martin counts as
# new-patient touches but don't have follow-up expectations.
NEW_PATIENT_TYPE_IDS = PHASE2_EPISODE_ANCHOR_IA_TYPE_IDS | {
    "1206575759565526893",  # 3. Ultrasound Assessment
    "1031259844406941435",  # 1. ACL Profiling
    "765760828145145406",   # Injury Update Testing (ACL/Hamstring/Groin) 30 mins
    "765761537334842944",   # Injury Update Testing (ACL/Hamstring/Groin) 60 mins
    "1810765504990680283",  # 4. Lab 60 Screening
}

# Sports Massage cancellations / DNAs never counted toward physio drop-off
# stats in Martin's manual tracker — apply the same exclusion here so the
# Performance Dashboard stays consistent. Sports Massage IS still a 1-to-1
# appointment (counts toward Total Appointments / utilization); it just
# doesn't generate drop-off rows in the sheet or hits on CNA % / DNA %.
EXCLUDED_FROM_DROPOFF_STATS = {
    "752219543803270402",   # Sports Massage Offer (30 Mins)
    "1820239945827096402",  # Sports Massage Offer (60 mins)
    "1882529999999735591",  # Sports Massage
}


# ===========================================================================
# APPOINTMENT TYPES EXCLUDED FROM "Total Appointments Seen"
# ===========================================================================
# Classes, workshops, group sessions and events that don't count as 1-to-1 patient
# throughput. Add new IDs here when new class types are created in Cliniko.

EXCLUDED_FROM_TOTAL_APPTS = {
    # Classes & group sessions
    "707099260440548575",   # ACL Class
    "818885039084279107",   # Back Class
    "818884579573110082",   # Groin Class
    "843440687104923293",   # Pilates Class
    "1071111451323668029",  # Back To Performance
    "1071111886474319422",  # Gaelic Groin
    "1797752808691210009",  # Knee Performance Rebuild
    # Change of Direction series (4 sessions)
    "1873431585462687176", "1873432124044875209",
    "1873432703815128522", "1873433620337661387",
    # Foot & Ankle series (3 sessions)
    "1873365083959072181", "1873378810800379321", "1873387227803817406",
    # Lateral Hip & Trunk series (3 sessions)
    "1873373812632851894", "1873379540030461370", "1873387690838201791",
    # Plyometrics series (3 sessions)
    "1873381748558009789", "1873392265070646722", "1873430585003742662",
    # Posterior Knee series (3 sessions)
    "1873378004948751800", "1873380445085767100", "1873390241243469249",
    # Running Mechanics series (3 sessions)
    "1873393022234793411", "1873393414351885764", "1873431038357673415",
    # Spinal Engine series (3 sessions)
    "1873375161068033463", "1873380051257398715", "1873388244335334848",
    # Workshop
    "1878506742912915097",  # Lower Limb Workshop Phase 1 - Immediate Post-Op
    # Events
    "1792229376889198157",  # Pitchside Management & First Aid Course
    "1792169872860386892",  # A Level Physio Open Morning
    # Other
    "1172140869495559675",  # Recovery Suite 30mins
}


# ===========================================================================
# CLINIC CAPACITY (service hours)
# ===========================================================================
# Update when team members join/leave or contract hours change.

# DEPRECATED — hand-maintained clinic capacity. No longer read anywhere: the
# Weekly Snapshot now derives capacity per week from the live roster via
# clinic_weekly_hours_on() (which excludes annual leave and tracks joiners/
# leavers automatically). Kept only so external references don't break.
CLINIC_WEEKLY_HOURS = 198.25       # total available service hours per week
CLINIC_MONTHLY_HOURS = 891.3       # total available service hours per month

# ---------------------------------------------------------------------------
# THE TEAM ROSTER — the ONE place to edit when someone joins or leaves.
# ---------------------------------------------------------------------------
# Everything below (display names, dashboard row order, capacity hours, Slack
# DMs) is DERIVED from this list. You should never need to edit those by hand.
#
#   SOMEONE JOINS →  add an entry with "start" set to their first day.
#                    Leave "end" as None.
#   SOMEONE LEAVES → set "end" to their last working day. DO NOT DELETE THEM.
#
# ⚠️  NEVER delete a leaver from this list, and never remove them from Cliniko's
# records. Deleting them here would wipe their name off historical dashboards,
# NPS history and drop-off rows. Setting "end" is all that's needed: they keep
# every historical figure they earned, stop receiving Slack DMs, and stop
# consuming clinic capacity from the day after they leave.
#
# Deactivating a leaver in CLINIKO (to stop their per-practitioner monthly bill)
# is safe — practitioner names are resolved from ALL staff, active and inactive
# (see all_practitioners() in phase2.py / phase1_fetch.py).
#
# Fields:
#   display          short name shown on every dashboard/DM
#   full_names       their Cliniko practitioner name(s); "X CS" secondary
#                    profiles fold into the same person
#   practitioner_ids their Cliniko IDs (stable even if they change their name)
#   start / end      "YYYY-MM-DD" or None. end = last working day.
#   monthly_hours    contracted monthly service hours (capacity/utilisation)
#   hours_periods    optional temporary hours, e.g. a phased return:
#                    [{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "monthly_hours": N}]
#                    ("to" inclusive). Used for any week/month that STARTS
#                    inside the range; otherwise monthly_hours applies.
#   clinic_email     their real mailbox — where the weekly team email is sent
#   slack_email      ONLY if the address on their Slack account differs from
#                    their clinic email. Slack is looked up by email, so if this
#                    is wrong they silently receive no DMs at all. Verify with
#                    Slack's users.lookupByEmail before setting it. Defaults to
#                    clinic_email.
#   owner_consultant True = excluded from the "w/o M&J" rollup
#
TEAM = [
    {"display": "Marty", "full_names": ["Martin Loughran", "Martin Loughran CS"],
     "practitioner_ids": ["382563813490168854", "1521620301232739358"],
     "start": None, "end": None, "monthly_hours": 83.1,
     "clinic_email": "martin@elitephysiocookstown.co.uk", "owner_consultant": True},

    {"display": "Julie", "full_names": ["Julie McVey", "Julie McVey CS"],
     "practitioner_ids": ["382564987693962263", "1648706640343471203"],
     "start": None, "end": None, "monthly_hours": 36.7,
     "clinic_email": "julie@elitephysiocookstown.co.uk", "owner_consultant": True},

    {"display": "Sinead", "full_names": ["Sinead McGill"],
     "practitioner_ids": ["1172138415936771374"],
     "start": "2022-01-01", "end": None, "monthly_hours": 128.6,
     "clinic_email": "sineadmcgill@elitephysiocookstown.co.uk"},

    # ON LEAVE 13 Jul – 1 Sep 2026. While on leave she had ZERO available hours,
    # so she dropped out of the utilisation denominator — utilisation = hours
    # delivered ÷ hours physios were AVAILABLE, and leave is not availability
    # (Martin 2026-07-27). Back seeing patients from Wed 2 Sep 2026 (confirmed by
    # Martin 2026-09-23; first appointment back 2 Sep). Keep the block — it's what
    # keeps her July/August capacity at zero on rebuilt tabs.
    {"display": "Erin", "full_names": ["Erin McNicholl"],
     "practitioner_ids": ["1168999178748040474"],
     "start": "2023-01-01", "end": None, "monthly_hours": 128.6,
     "leave": [{"from": "2026-07-13", "to": "2026-09-01"}],
     # Phased return after injury: capped at 50% capacity through September,
     # back to 100% from 1 Oct 2026 (Martin 2026-09-23). Starts 31 Aug so the
     # w/c 31 Aug week (her first week back) is on 50% too.
     "hours_periods": [{"from": "2026-08-31", "to": "2026-09-30", "monthly_hours": 64.3}],
     "clinic_email": "erin@elitephysiocookstown.co.uk"},

    # LEFT 2 Jul 2026. Kept here on purpose — this is what preserves her name on
    # every historical dashboard, NPS column and drop-off row. Do not delete.
    {"display": "Daire", "full_names": ["Daire McKenna"],
     "practitioner_ids": ["1501275397424158535"],
     "start": "2023-06-01", "end": "2026-07-02", "monthly_hours": 128.6,
     "clinic_email": "daire@elitephysiocookstown.co.uk"},

    {"display": "Aoife", "full_names": ["Aoife O'Kane"],
     "practitioner_ids": ["1592625921783764576"],
     "start": "2025-01-01", "end": None, "monthly_hours": 128.6,
     "clinic_email": "aoifeokane@elitephysiocookstown.co.uk"},

    {"display": "Ciara", "full_names": ["Ciara O'Kane"],
     "practitioner_ids": ["1965915462512416363"],
     "start": "2026-06-08", "end": None, "monthly_hours": 128.6,
     "clinic_email": "ciara@elitephysiocookstown.co.uk"},

    # Annual leave Mon 17 – Sun 23 Aug 2026 (confirmed by Martin 2026-08-24:
    # zero appointments all week, verified against Cliniko). "to" is the
    # INCLUSIVE last day off. Without this she kept full capacity for a week she
    # wasn't there, which is what dragged her weekly utilisation to "—" while
    # still counting the hours against her.
    {"display": "Molaí", "full_names": ["Molaí Smith"],
     "practitioner_ids": ["1719373338607883970"],
     "start": "2025-09-01", "end": None, "monthly_hours": 128.6,
     "leave": [{"from": "2026-08-17", "to": "2026-08-23"}],
     "clinic_email": "molai@elitephysiocookstown.co.uk"},

    {"display": "Shannagh", "full_names": ["Shannagh Conwell"],
     "practitioner_ids": ["1818200739135100480"],
     "start": "2025-11-01", "end": None, "monthly_hours": 128.6,
     "clinic_email": "shannagh@elitephysiocookstown.co.uk"},

    # Started seeing patients 10 Aug 2026, full-time. Slack account is on his
    # clinic address (verified 2026-08-10), so no slack_email override needed.
    {"display": "Conor", "full_names": ["Conor O'Hagan"],
     "practitioner_ids": ["2003886838393083747"],
     "start": "2026-08-10", "end": None, "monthly_hours": 128.6,
     "clinic_email": "conor@elitephysiocookstown.co.uk"},

    # Starts full-time 11 Aug 2026 (was pencilled in for 1 Aug; confirmed 10 Aug
    # she actually begins the 11th). She has scattered earlier appointments in
    # Cliniko (a placement: 25 in Nov 2025, a handful since) — those resolve to
    # "Kelly" straight away, but she stays off the dashboard and out of the
    # capacity/DM lists until her start date, then appears on her own.
    # ⚠️ Her SLACK account is registered to a personal address — there is no
    # Slack account for kelly@elitephysiocookstown.co.uk (verified against the
    # workspace 2026-07-14). Her clinic mailbox gets the weekly team email; her
    # Slack DMs must go to the iCloud address or they silently vanish. Remove
    # the slack_email override once her Slack is moved to the clinic address.
    {"display": "Kelly", "full_names": ["Kelly Scott"],
     "practitioner_ids": ["1810741098981627376"],
     "start": "2026-08-11", "end": None, "monthly_hours": 128.6,
     "clinic_email": "kelly@elitephysiocookstown.co.uk",
     "slack_email": "kellyscott0208@icloud.com"},
]


# ---------------------------------------------------------------------------
# Roster helpers — date-aware "was this person on the team then?"
# ---------------------------------------------------------------------------

def _as_date(v):
    return date.fromisoformat(v) if isinstance(v, str) else v


def has_started(member, on=None):
    """True if `member` had started by `on` (default: today)."""
    on = on or date.today()
    start = _as_date(member.get("start"))
    return start is None or start <= on


def is_active_on(member, on=None):
    """True if `member` was on the team on `on` (default: today)."""
    on = on or date.today()
    end = _as_date(member.get("end"))
    return has_started(member, on) and (end is None or end >= on)


def is_active_in_period(member, period_start, period_end):
    """True if `member` worked at ANY point during [period_start, period_end].

    This is what keeps a leaver's numbers honest: Daire left 2 Jul 2026, so she
    still has capacity (and a real utilisation %) for July, but none from August
    onward. Same rule the CFO tool uses (clinician_roi._is_active).
    """
    start, end = _as_date(member.get("start")), _as_date(member.get("end"))
    if start and start > period_end:
        return False
    if end and end < period_start:
        return False
    return True


def _norm_date(v):
    """Accept a date, datetime or ISO string and return a plain date."""
    if isinstance(v, str):
        return date.fromisoformat(v)
    if isinstance(v, datetime):
        return v.date()
    return v


def display_order_for_period(period_start, period_end, with_data=()):
    """PRACTITIONER_DISPLAY_ORDER, trimmed to the people who were actually on
    the team during [period_start, period_end].

    This is what stops a leaver haunting a dashboard: Daire left 2 Jul 2026, so
    she still has a row for June and July (she worked them) but none for August
    onward, instead of a permanent row of "—"/0 that reads like a physio who
    saw nobody. Anyone who hasn't started yet is excluded the same way.

    `with_data` is a safety net: any display name in it is kept even if the
    roster says they'd gone. Callers pass the physios who have real numbers for
    the period, so a wrong `end` date in TEAM can never silently delete data
    from a tab — the row comes back and the date is obviously wrong.

    Accepts dates or datetimes; `period_end` may be inclusive or exclusive
    (a one-day difference never changes who was on the team).
    """
    ps, pe = _norm_date(period_start), _norm_date(period_end)
    keep = set(with_data)
    out = []
    for m in TEAM:
        if m["display"] not in PRACTITIONER_DISPLAY_ORDER:
            continue
        if is_active_in_period(m, ps, pe) or m["display"] in keep:
            out.append(m["display"])
    return out


def _member(display):
    for m in TEAM:
        if m["display"] == display:
            return m
    return None


def on_leave_whole_period(member, period_start, period_end):
    """True if `member` was on annual leave for the ENTIRE [period_start, period_end].

    Utilisation is hours delivered ÷ hours the physio was AVAILABLE, and annual
    leave is not availability — so a physio off for a whole week/month has zero
    available hours and drops out of the denominator entirely. A part-worked
    period keeps full capacity (we don't pro-rate mid-period leave here)."""
    for lv in member.get("leave") or []:
        lf = _as_date(lv.get("from"))
        lt = _as_date(lv.get("to"))
        # "to" is the INCLUSIVE last day off (what the roster comments tell you
        # to enter), but callers pass an EXCLUSIVE period_end — a week w/c Mon
        # 17th arrives as (17th, 24th). Comparing the two directly was off by a
        # day: a full Mon–Sun leave entered as 17→23 failed to register at all
        # and the physio kept full capacity for a week they weren't there.
        # Normalise to the same basis before comparing. (Martin 2026-08-24.)
        if lf and lf <= period_start and (
            lt is None or lt >= period_end - timedelta(days=1)
        ):
            return True
    return False


def monthly_hours_on(display, period_start, period_end):
    """Monthly capacity for `display` during a period — None if they weren't on
    the team then (or were on annual leave for the whole period), which makes
    utilisation render as "—" instead of a false 0% and removes them from the
    clinic denominator."""
    m = _member(display)
    if not m or not is_active_in_period(m, period_start, period_end):
        return None
    if on_leave_whole_period(m, period_start, period_end):
        return None
    ps = _norm_date(period_start)
    for hp in m.get("hours_periods") or []:
        if _as_date(hp["from"]) <= ps <= _as_date(hp["to"]):
            return hp["monthly_hours"]
    return m.get("monthly_hours")


def weekly_hours_on(display, period_start, period_end):
    """Weekly capacity for `display` during a period (see monthly_hours_on)."""
    hrs = monthly_hours_on(display, period_start, period_end)
    return round(hrs / 4.345, 1) if hrs else None


def clinic_weekly_hours_on(period_start, period_end):
    """Total clinic capacity for a week = sum of every physio's AVAILABLE weekly
    hours, skipping anyone not on the team then or on annual leave for the whole
    week. This is the utilisation denominator for the Weekly Snapshot; it tracks
    the live roster (and reconciles with the Weekly Team Stats roll-up) instead
    of the old hand-maintained CLINIC_WEEKLY_HOURS constant."""
    total = 0.0
    for m in TEAM:
        h = weekly_hours_on(m["display"], period_start, period_end)
        if h:
            total += h
    return round(total, 2)


# ---------------------------------------------------------------------------
# DERIVED FROM TEAM — do not edit by hand.
# ---------------------------------------------------------------------------

# Every Cliniko full name → short display name. Includes LEAVERS, so their
# historical appointments still resolve to their name rather than "?".
PRACTITIONER_DISPLAY_NAME = {
    full: m["display"] for m in TEAM for full in m["full_names"]
}

# Row order in the Performance Dashboard, after Standard / Clinic Average / w/o M&J.
# Includes leavers (their history stays on the board); excludes anyone who hasn't
# started yet — they appear automatically on their start date.
PRACTITIONER_DISPLAY_ORDER = [m["display"] for m in TEAM if has_started(m)]

# Per-physio monthly/weekly available service hours (Utilisation KPI).
# Prefer monthly_hours_on()/weekly_hours_on(), which are date-aware and stop a
# leaver from eating capacity after they've gone. These flat dicts are the
# "as of today" view, kept for callers that have no period to hand.
PHYSIO_MONTHLY_HOURS = {
    m["display"]: m["monthly_hours"] for m in TEAM if has_started(m)
}
PHYSIO_WEEKLY_HOURS = {
    name: round(hours / 4.345, 1) for name, hours in PHYSIO_MONTHLY_HOURS.items()
}

# Physios EXCLUDED from "w/o M&J" rollup (clinic minus owner-consultants).
EXCLUDE_FROM_MAIN_TEAM = {m["display"] for m in TEAM if m.get("owner_consultant")}


# ===========================================================================
# SLACK NOTIFICATION CONFIG
# ===========================================================================

# DERIVED FROM TEAM — display name → the address on their SLACK account, used to
# look up their Slack user ID at runtime. Falls back to their clinic email, which
# is right for everyone whose Slack uses their work address.
# Only people currently on the team: setting someone's "end" date in TEAM
# automatically stops all DMs to them from the next day. (This was hand-
# maintained before — Daire was deleted from this list when she left 2 Jul 2026;
# now her "end" date does it.)
PHYSIO_SLACK_EMAIL = {
    m["display"]: (m.get("slack_email") or m.get("clinic_email"))
    for m in TEAM
    if is_active_on(m) and (m.get("slack_email") or m.get("clinic_email"))
}

# DERIVED FROM TEAM — display name → real mailbox. This is where actual EMAIL
# goes (the weekly team email), as opposed to Slack DMs above. The two differ
# whenever someone's Slack is registered to a personal address.
PHYSIO_CLINIC_EMAIL = {
    m["display"]: m["clinic_email"] for m in TEAM
    if is_active_on(m) and m.get("clinic_email")
}

# Safe-mode redirect target — when SLACK_SAFE_MODE is True, every Slack message
# is rerouted here instead of being delivered to its real recipients.
CEO_SLACK_EMAIL = "martin@elitephysiocookstown.co.uk"

# Ops Manager Summary — daily digest goes to Marty + Sinead Rocks (Ops Manager).
OPS_MANAGER_SLACK_EMAILS = [
    "martin@elitephysiocookstown.co.uk",
    "sinead@elitephysiocookstown.co.uk",  # Sinéad Rocks (Ops Manager)
]

# Reactivation call list — goes to reception profile (whoever's at front desk)
# AND Sinéad Rocks (oversees the reactivation workflow).
RECEPTION_LIST_SLACK_EMAILS = [
    "reception@elitephysiocookstown.co.uk",
    "sinead@elitephysiocookstown.co.uk",
]

# Diary summary (send_diary_summary.py): per-physio IAs / classes / total
# appointments, 08:00 Mon, Wed and Fri (Fri adds next week's diary).
# Sinead and reception can't see Cliniko's practitioner reports on their logins.
DIARY_SUMMARY_SLACK_EMAILS = [
    "sinead@elitephysiocookstown.co.uk",      # Sinéad Rocks (Ops Manager)
    "reception@elitephysiocookstown.co.uk",   # Reception Slack profile
]

# Slack channel where package-of-care sales are posted (used by the weekly
# packages count DM to Sinead Rocks). #packages.
PACKAGES_CHANNEL_ID = "C04G5CKN60Y"

# Spreadsheet URL used in DM links
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI/edit"

# ===========================================================================
# TEAM SHEET (separate spreadsheet — "Elite Drop-offs — Team")
# ===========================================================================
# Physio-facing view of the drop-offs. A SEPARATE FILE on purpose: Google
# Sheets permissions are per-file, never per-tab, so anything in the master
# workbook is visible to anyone the master is shared with — protecting or
# hiding a tab only stops edits, not viewing (File > Download and a copy both
# expose it). The physios get this file and only this file; the per-physio
# performance tabs stay in the master, which they cannot open.
TEAM_SPREADSHEET_ID = "15ZeDIdxogVZqaC8Z-hAb6nOfAzBqLdJ6LFokByVGWDg"
TEAM_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/15ZeDIdxogVZqaC8Z-hAb6nOfAzBqLdJ6LFokByVGWDg/edit"
)
TEAM_SHEET_TAB = "Drop-offs"
# How many W/C weeks of drop-offs the physios see at once.
TEAM_SHEET_WEEKS = 4

# ===========================================================================
# LEADS SHEET (separate spreadsheet — "Elite Physio — New Patient Bookings")
# ===========================================================================
LEADS_SPREADSHEET_ID = "1zoFhXPGzDnrCVTgTYs-YRm8EUzx91LL5Q_8L5Bd7_iU"
LEADS_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1zoFhXPGzDnrCVTgTYs-YRm8EUzx91LL5Q_8L5Bd7_iU/edit"
)

# SAFE MODE: when True, every Slack message gets redirected to the CEO's DM
# (with a "[TEST]" prefix) instead of being sent to physios / reception.
# Flipped LIVE 2026-05-12 after Martin approved the message formats.
# Phase 4 (interactive buttons) verified working 2026-05-13.
# Set to True temporarily if you need to test prompt/format changes without the team being pinged.
SLACK_SAFE_MODE = False


# ===========================================================================
# REFERRER NORMALISATION (monthly referrer analysis → Sinead)
# ===========================================================================
# Reception free-types the referrer into the Cliniko booking note ("Ref: …"),
# so one source arrives spelled many ways. Measured Aug 2026: 471 bookings
# carried 152 distinct strings, which split real totals across the list —
# "Past patient" and "past pt" sat as separate rows six apart in the July DM.
#
# Case AND punctuation are folded automatically (send_referrers_monthly._ref_key),
# so "clonoe", "Clonoe", "Fr.Rocks" and "Fr Rocks" need no entry here. This map
# is only for what folding can't catch: abbreviations and misspellings.
#
# canonical name → the variants that mean it.
#
# DELIBERATELY CONSERVATIVE. These counts feed club-level analysis, and the
# clubs here are also billing entities (see the split-bill clubs — Bellaghy,
# Clonoe), so a wrong merge is worse than a split count. Rules of thumb:
#   - Named individuals are never merged; they ARE distinct referrers.
#   - A shared word is NOT evidence of the same club. Rock St Patrick's and
#     Cookstown Fr Rocks are two different clubs; Glen and Glenullin likewise.
#     Only merge where someone who knows the clubs has confirmed it.
#   - Genuinely ambiguous entries are left alone rather than guessed, until
#     someone who knows settles it.
#   - Compound entries ("past pt (lavey)", "Donaghmore / pt") name two sources,
#     so they're left for a human rather than attributed to one — unless the
#     second half is noise rather than a second source ("Rock / Marty" is just
#     Rock, per Martin).
# Unmapped near-misses are reported at the end of every run, so new spellings
# surface here for review instead of quietly splitting a total.
REFERRER_ALIASES = {
    "Past patient":  ["past pt", "p pt", "previous", "p past"],
    "Self referral": ["self ref", "self refferal"],
    # The Performance Lab (own gym/S&C service), written every which way.
    "Performance Lab": ["per lab", "per lab 60", "perf lab", "plab", "p lab",
                        "lab", "plab member", "plab coach"],
    "Walk-in": ["walk in"],
    # Cookstown Fr Rocks GAA. Per Martin (2026-08-17): "Cookstown", "Cookstown
    # Fr Rocks" and "Fr Rocks" are all this one club.
    "Cookstown Fr Rocks": ["fr rocks", "fr rocks cookstown", "cookstown fr roks",
                           "cookstown", "fr rocks ladies"],
    # ⚠️ Rock St Patrick's is a DIFFERENT club to Cookstown Fr Rocks — never
    # fold "Rock" into Fr Rocks on the strength of the shared word (Martin,
    # 2026-08-17). "Sinead Rocks" is the Ops Manager, a third thing again.
    "Rock St Patrick's": ["rock", "rock st patricks", "rock marty"],
    # ⚠️ Two separate Dungannon clubs. "Dungannon" on its own means Dungannon
    # Clarkes; Eoghan Ruadh is its own club and never folds into it (Martin,
    # 2026-08-17).
    "Dungannon Clarkes": ["dungannon", "dungannon clarkes"],
    # Underage/ladies teams fold into the club: the referral source is the club.
    "Bellaghy": ["ballaghy", "bellaghy u16", "bellaghy underage"],
    "Galbally": ["galbally underage ladies"],
    "Eoghan Ruadh Dungannon": ["dungannan eoghan rua", "eoghan ruadh"],
    "Drumsurn": ["drumsern"],
    "Mum": ["mum aislene"],
}

# Entries that mean "nobody recorded one" — counted as NO referrer rather than
# as a referrer named "?", so the coverage line ("N of M bookings had a
# referrer") tells the truth instead of counting a question mark as a source.
REFERRER_NOT_RECORDED = ["?", "unknown", "not given", "n a", "none", "no ref"]

# Spellings confirmed to be their own referrer, NOT a variant of anything else.
# The near-miss reporter skips these, so a decision once made stops resurfacing
# every month. Add here when a suggestion is reviewed and rejected.
#   - "Marty": an individual who refers in his own right. Gets flagged against
#     "Rock / Marty" (which is just Rock), but he is not the club. Martin,
#     2026-08-17.
REFERRER_KEEP_SEPARATE = ["Marty"]


# ===========================================================================
# GOLD STANDARDS (for Performance Dashboard conditional formatting)
# ===========================================================================

STANDARDS = {
    "utilization_pct_min": 75,
    "utilization_pct_max": 85,
    "ias_per_month_clinic": 192,
    "dna_pct_max": 2,
    "cna_pct_max": 8,
    "dna_cna_combined_pct_max": 10,
    "pva_min": 6,
    "cna_dna_first_pct_max": 2,
    "nps_pct_min": 85,
    "gen_pop_pva_min": 6,
}

# Cliniko appointment type IDs used in Gen Pop PVA = (Initial + Review) / Initial.
GENPOP_INITIAL_TYPE_ID = "382563815654429852"  # 1. Initial Appointment
GENPOP_REVIEW_TYPE_ID = "382563815511823515"   # 2. Review Appointment


# ===========================================================================
# MARKETING / NPS SYSTEM
# ===========================================================================
# Replaces Cliniq Apps. See Elite_Marketing_Replacement_Plan.md,
# patient_communication_system.md and tally_nps_form.md for the design.

# Master switch. While False, the poller runs and logs what it WOULD send but
# sends nothing real. Flip True only at go-live. Env-controlled so the cutover
# (and instant rollback) is a Render env toggle, not a code change.
MARKETING_LIVE = os.environ.get("MARKETING_LIVE", "false").strip().lower() == "true"

# Safe mode: when True, every email/SMS is rerouted to the test contacts below
# (subject/body prefixed "[TEST → real_recipient]"). Use for channel testing.
MARKETING_SAFE_MODE = os.environ.get("MARKETING_SAFE_MODE", "true").strip().lower() == "true"
MARKETING_TEST_EMAIL = "martin@elitephysiocookstown.co.uk"
MARKETING_TEST_PHONE = "+447740280274"   # Martin's mobile, E.164 format

# The "Elite Physio — NPS & Marketing" Google Sheet (built by nps_sheet_setup.gs).
MARKETING_SPREADSHEET_ID = "1LYqkrOgwYUQR2AsU03y7h4G97BgMsROJiaeaVUfFuX8"

# Tally NPS survey form — the code from the published form URL (tally.so/r/XXXXX).
TALLY_FORM_ID = "lbYWjk"        # tally.so/r/lbYWjk — Elite Physiotherapy Feedback

# SMS URL shortener — base URL of the standalone elite-sms-shortener Render
# web service. NPS SMS go through /r/<token> redirects to cut the ~400-char
# Tally URL down to ~40 chars. Env var SHORTENER_BASE_URL overrides this.
SHORTENER_BASE_URL = os.environ.get(
    "SHORTENER_BASE_URL", "https://elite-sms-shortener.onrender.com")

# ---- Email (Resend) ----
EMAIL_FROM_NAME = "Elite Physiotherapy"
EMAIL_FROM_ADDRESS = "info@elitephysiocookstown.co.uk"   # front desk monitors this
# Named senders for the personal templates (name, address).
EMAIL_SINEAD = ("Sinead Rocks", "sinead@elitephysiocookstown.co.uk")
EMAIL_MARTIN = ("Martin Loughran", "martin@elitephysiocookstown.co.uk")

# ---- SMS (Twilio) ----
SMS_SENDER_ID = "ElitePhysio"   # alphanumeric sender — ONE-WAY, patients can't reply

# Two-way number bought for the Omagh launch (Aug 2026). Sending from THIS
# instead of SMS_SENDER_ID is what lets patients reply — their replies hit
# /twilio/inbound on the Render app and land in #omagh-replies.
# Campaign sends pass sender=config.SMS_SENDER_NUMBER; everything else keeps
# the branded one-way ID.
SMS_SENDER_NUMBER = "+447727712191"

# ---- Detractor / passive internal alert recipient ----
NPS_ALERT_EMAIL = "sinead@elitephysiocookstown.co.uk"   # Sinead Rocks, Ops Manager

# ---- Per-clinic details (used to fill template variables) ----
CLINICS = {
    "Cookstown": {
        "phone": "028 8644 0995",
        "address": "133 Moneymore Road, Cookstown, BT80 9UU",
        "google_review_url": "https://g.page/r/CfpgA6cxZez1EAE/review",
    },
    "Maghera": {
        # Cliniko has the Cookstown number against both sites — confirm whether
        # Maghera has its own line; update here if so.
        "phone": "028 8644 0995",
        "address": "86 Main Street, Maghera, BT46 5AF",
        "google_review_url": "https://g.page/r/Cccza5z-M6UtEAE/review",
    },
    # Opened 8 Sept 2026. Address taken from the Cliniko business record.
    # Phone is the central booking line, shared with Cookstown and Maghera
    # (confirmed by Martin 2026-08-27) — deliberate, not a placeholder.
    # Omagh has its OWN Google Business Profile, so promoter reviews land on the
    # Omagh listing rather than Cookstown's.
    "Omagh": {
        "phone": "028 8644 0995",
        "address": "Blackwater Private Clinic, 43 Dublin Road, Omagh, BT78 1HE",
        "google_review_url": "https://g.page/r/CX9MX-CfFf3gECE/review",
    },
}
DEFAULT_CLINIC = "Cookstown"

# Shared links (same for every clinic).
BOOKING_LINK = "https://linktr.ee/ElitePhysiotherapy"
EXERCISE_LIBRARY_LINK = "https://patient.thegotoclinichub.com/index.php"
PRE_ASSESSMENT_FORM_LINK = ""   # Not needed — Cliniko auto-attaches the pre-assessment
                                # form to new-patient appointment confirmation emails.

# Maps Cliniko business (location) ID → clinic key in CLINICS above.
CLINIKO_BUSINESS_TO_CLINIC = {
    "382563815931253999": "Cookstown",
    "1751489684669732550": "Maghera",
    "2021155774603990189": "Omagh",
}

# ===========================================================================
# NEW PATIENT BOOKINGS TRACKER
# ===========================================================================
# bookings_fetch.py trawls Cliniko 6x/day for newly-booked initial assessments
# and logs them to a dedicated Google Sheet (weekly Sun-Sat tabs + Dashboard +
# manual Leads tab). See bookings_build_status.md.

# The "Elite Physio — New Patient Bookings" Google Sheet.
BOOKINGS_SPREADSHEET_ID = "1zoFhXPGzDnrCVTgTYs-YRm8EUzx91LL5Q_8L5Bd7_iU"

# The reception Slack profile that gets the "new bookings" DM after each trawl.
# This is the profile's MEMBER ID (a "U…" code), not an email — in Slack:
# open the reception profile → ⋮ (more) → Copy member ID.
BOOKINGS_SLACK_USER_ID = "U02LWFA64J3"   # reception Slack profile

# Insurers recognised when parsing "Auth:" in the booking note (so the insurer
# name and the auth code land in separate columns).
KNOWN_INSURERS = ["AXA", "Aviva", "WPA", "Bupa", "Vitality", "Healix",
                  "Cigna", "VHI", "Laya", "Irish Life"]

# A booking counts as an "initial assessment" for the tracker if its appointment
# type is in this set — reuse the broad new-patient list the stats already use.
BOOKINGS_IA_TYPE_IDS = NEW_PATIENT_TYPE_IDS


# ---- Poller behaviour ----
# Hour (clinic local time) at which the daily lifecycle flows run (30/90/180-day,
# birthday). The poller runs these once per day, in the first 10-min slot.
MARKETING_LIFECYCLE_HOUR = 9
# No patient messages are sent between QUIET_START and QUIET_END (24h clock).
MARKETING_QUIET_START = 21
MARKETING_QUIET_END = 8
# Birthday flow scans the whole patient base — leave off until volume is known.
MARKETING_BIRTHDAY_ENABLED = False

# ---- Injection Therapy check-ins (Day 14 / Day 28) ----
# These check-ins are sent by CLINIKO, not by this poller. The injection flow
# was migrated to Cliniko's native automations rather than rebuilt here, and
# went live 2026-08-24 (the same day Cliniq Apps was switched off).
#
# True = something else is already checking in on injection patients, so this
# system must stay out of their way. Set to False only if Cliniko stops sending
# them, which would make the 30-day email the right thing to send again.
INJECTION_CHECKINS_LIVE = True

INJECTION_TYPE_IDS = {
    "1192928323588592985",   # 1. Injection Therapy
}

# Appointment types whose patients should NOT get the generic 30-day follow-up,
# because a more specific check-in already covers them. Injection patients get
# Cliniko's Day 28 check-in; without this they would get both, ~2 days apart.
# Measured 2026-08-24: 72% of injection patients have nothing booked in the 35
# days after, so nearly all of them would have collided.
#
# ACL is deliberately NOT in this set. Its Cliniko journey has no 30-day
# check-in (Martin 2026-08-24), so those patients should keep getting the
# generic email — and they rarely qualify anyway: only 7% of ACL Initial
# Assessment patients have nothing booked in the following 35 days, because
# they are in active rehab.
#
# Only applied while INJECTION_CHECKINS_LIVE is True.
THIRTY_DAY_SUPPRESSED_TYPE_IDS = INJECTION_TYPE_IDS


# ===========================================================================
# END-OF-DAY STATS REPORT
# ===========================================================================
# eod_stats.py posts a ready-to-paste stats table to a Slack channel several
# times a day (see EOD_REPORT_TIMES) for the end-of-shift handover email.
# All figures are aggregate counts — no patient data, no AI.

# Bookwhen private iCal feed — Pilates class bookings. No API key needed: each
# class event's title carries a [booked/capacity] count.
BOOKWHEN_ICAL_URL = ("https://feeds.bookwhen.com/ical/bcfxa253kavk/"
                     "12mt81u2cm5oflmppthrkiy0spj1/private.ics")

# Slack channel the EOD report posts to (the bot must be invited to it).
EOD_SLACK_CHANNEL = "#eod-claude"

# Fixed weekly targets — may change in future; edit here.
EOD_TARGETS = {
    # Raised 2026-08-27 after Conor and Kelly joined. Maghera's 114 is a PLANNED
    # CAPACITY INCREASE, not a stretch on the current setup (best week to date
    # was 62) — expect it to read red until the extra capacity is actually in.
    "total_appts_Cookstown": 258,
    "total_appts_Maghera": 114,
    "total_appts_Omagh": 7,     # opening target, Sept 2026 — low on purpose
    "ias_Cookstown": 43,
    "ias_Maghera": 19,
    "ias_Omagh": 2,             # opening target, Sept 2026 — low on purpose
    "pilates_matwork_cookstown": 31,
    "pilates_matwork_maghera": 44,
    "pilates_reformer_cookstown": 34,
}

# Reactivation target = this fraction of the PREVIOUS week's drop-off count.
REACTIVATION_TARGET_FRACTION = 0.40
# Drop-off types excluded from that base — the pre-IA drop-offs where the
# patient never attended (IACNA = cancelled IA, IADNA = did-not-attend IA).
REACTIVATION_TARGET_EXCLUDE = {"iacna", "iadna"}

# Manual Pilates corrections added to the "this week" Bookwhen count. A
# booking-sheet error in the CURRENT block means Bookwhen under-counts
# Cookstown Matwork by 4 — set this back to 0 once the block ends.
EOD_PILATES_ADJUSTMENTS = {
    "pilates_matwork_cookstown": 4,
}

# IA appointment types counted as "expected IAs" in the report.
EOD_IA_TYPE_IDS = PHASE1_DROPOFF_IA_TYPE_IDS

# Times the EOD report is generated/posted (clinic-local, 24h) by weekday
# (Mon=0 … Sun=6). These mirror the cron times in server.crontab. The LAST
# entry of a day is that day's final stat collection: the Reschedules/CDNR
# window runs from the previous working day's final collection up to the
# moment the report is built.
EOD_REPORT_TIMES = {
    0: ["12:00", "16:00", "20:00"],            # Monday
    1: ["12:00", "16:00", "20:00"],            # Tuesday
    2: ["10:00", "12:00", "16:00", "20:00"],   # Wednesday
    3: ["12:00", "16:00", "20:00"],            # Thursday
    4: ["15:30"],                              # Friday
}


# ===========================================================================
# MONTHLY CLUB DROP-OFF ALERT (→ Sinead, 1st of the month)
# ===========================================================================
# send_club_alert_monthly.py flags clubs that are sending us noticeably less
# work. The club is only recorded on the Cliniko INVOICE ITEM name (appointment
# types are generic "Club Follow Up" etc.), so the alert reads invoice items.
#
# Anything not listed in CLUB_ALERT_NON_CLUB_ITEMS or CLUB_ALERT_NO_NAMED_CLUB
# is treated as a club. ⚠️ Keep CLUB_ALERT_NON_CLUB_ITEMS in step with the
# non-Club lists in elite-finance-officer/config/cliniko_category_mapping.json.
# If reception adds a new non-club item and it isn't added here, it gets tracked
# as a "club". It only causes an alert if it later drops, and a brand-new item
# name is listed in the DM footer so a rename or new item is easy to spot.
CLUB_ALERT_NON_CLUB_ITEMS = [
    # Initial Assessment
    "Initial Consultation", "Omagh Initial Assessment Offer",
    # Follow up
    "Friends & Family", "Gold POC session", "PerformLab Members", "Review Appointment",
    "Platinum POC session", "Sports POC", "Sports POC 2025",
    # Insurance
    "Aviva", "Axa New", "Axa Review", "Santry", "WPA", "H3 Insurance", "Independence Works",
    "Aviva - Physiotherapy - IA", "Aviva - Physiotherapy - Review",
    "Axa - Physiotherapy - Initial Assessment", "Axa - Physiotherapy - Review",
    "Axa -Physiotherapy - Review", "WPA - Physiotherapy - Initial Assessment",
    "WPA - Physiotherapy - Review", "Vitality", "Innovate", "The Permanent Health Company",
    # Big Ticket
    "Clinical Specialist Consultation", "Injection Therapy", "POCUS Ultrasound Assessment",
    "Profiling Assessment", "Testing 60 mins", "Testing (30mins)", "Testing (40MINS)",
    "Ultrasound Assessment", "Medico Legal Report", "Mummy MOT IA", "Mummy MOT Review",
    "Pelvic Health Assessment",
    # Massage
    "Sports Massage", "Sports Massage Offer - 30mins", "Sports massage offer - 60mins",
    "Sports Massage Offer",
    # Pilates
    "Pilates", "Pilates PAYG", "Pilates Matwork Cookstown", "Pilates Matwork Cookstown (5weeks)",
    "Pilates Matwork Cookstown PAYG", "Pilates Matwork Cookstown (4weeks)",
    "Pilates Matwork Cookstown (6 weeks)", "Pilates Reformer Cookstown",
    "Pilates Reformer Cookstown (5 weeks)", "Pilates Reformer Cookstown (4 weeks)",
    "Pilates Reformer Cookstown (6 weeks)", "Pilates Reformer PAYG", "Pilates Matwork Maghera",
    "Pilates Matwork Maghera (5weeks)", "Pilates Matwork Maghera PAYG",
    "Pilates Matwork Maghera (4weeks)", "Pilates Matwork Maghera (6 weeks)",
    # ACL / rehab classes
    "Lower Limb Workshop", "Lower Limb Workshops x5", "Lower LimbWorkshops x4",
    "ACL Class (8)", "Pitch class", "Knee Class",
    # Online / group programmes (priced £249–£343, one-off)
    "Bulletproof Program 2024", "Groin Restore Online 2025", "Knee Performance Online Programme",
    # Other
    "Lab 60 Screening", "Shockwave", "Taping", "Compex", "TAPING ONLY", "Game Ready",
    "Resistance Band", "Recovery Suite 30", "1-1 Free Strategy session", "Free Call Back Service",
    "Pitchside Management & First Aid 2025", "A Level Physio Open Morning",
]

# Club-rate items that aren't tied to one named club, so there's nobody to call.
# Matched as a PREFIX (the non-affiliated item's full name is very long).
CLUB_ALERT_NO_NAMED_CLUB = [
    "Non-Affiliated Club Charge",
    "Club Initial Assessment -  Booked Online",
    "Club Ultra Sound Scans",
    "Physiotherapy Treatment (club charge)",
]

# Several Cliniko items → one club. Only same-club variants (Ladies / Camogie /
# Underage, alternative spellings), deliberately conservative like
# REFERRER_ALIASES: Tyrone GAA ≠ Tyrone Ladies, Cookstown Fr Rocks ≠ Cookstown
# Youth, St Brigids Belfast ≠ St.Bridgets, Dungannon Swifts is a soccer club.
# An item not listed here is its own club, under its Cliniko item name.
# Grouping agreed in the YTD club review, 2026-09-15.
CLUB_ALERT_GROUPS = {
    "Bellaghy Wolfe Tones": ["Bellaghy Initial", "Bellaghy Review", "Bellaghy Camogie", "Bellaghy Self pay", "Bellaghy IA"],
    "Kildress Wolfe Tones": ["Kildress Senior Men/Ladies", "Kildress underage", "Kildress Youth (underage)"],
    "Magherafelt O'Donovan Rossa": ["Magherafelt O'Donovan Rossa", "Magherafelt O'Donovan Rossa - MEN",
                                    "Magherafelt O'Donovan Rossa Camogie",
                                    "Magherafelt O'Donovan Rossa Ladies Football", "Magherafelt Ladies"],
    "Ardboe": ["Ardboe", "Ardboe Ladies"],
    "Ballinascreen": ["Ballinascreen", "Ballinascreen Ladies"],
    "Lavey": ["Lavey", "Lavey Ladies", "Lavey Camogie"],
    "Dungannon Clarkes": ["Dungannon Club Appointment", "Dungannon Ladies"],
    "Ballerin": ["Ballerin", "Ballerin Camogie"],
    "Swatragh": ["Swatragh", "Swatragh Camogie"],
    "Donaghmore": ["Donaghmore", "Donaghmore Ladies"],
    "Greencastle": ["Greencastle", "Greencastle Ladies"],
    "Portglenone": ["Portglenone Roger Casements", "Portglenone Ladies"],
    "Glen Maghera": ["Glen Maghera", "Glen Ladies"],
    "Dungiven": ["Dungiven St Canices GAC", "Dungiven Camogie"],
    "Kilrea Patrick Pearses": ["Kilrea Patrick Pearses", "Patrick Pearses Kilrea"],
    "Carrickmore": ["Carrickmore", "Eire Og  Carrickmore"],
    "Derry county squads": ["Derry GAA", "Derry Camogie", "Derry Oga"],
    "Clonoe O'Rahilly's": ["Clonoe Club Appointment"],
    "Derrylaughan": ["Derrylaughan Club Appointment"],
    "Fianna": ["Fianna Club Appointment"],
}

# Alert thresholds.
# "Sharp drop": last 3 complete months vs the SAME 3 months a year earlier
# (GAA work is seasonal, so a month-on-month compare would cry wolf every winter).
CLUB_ALERT_WINDOW_MONTHS = 3
CLUB_ALERT_DROP_MIN_BASELINE = 15      # sessions in last year's window, to be worth flagging
CLUB_ALERT_DROP_MAX_RATIO = 0.60       # this year ≤ 60% of last year (a 40%+ drop)…
CLUB_ALERT_DROP_MIN_LOST = 10          # …AND at least this many sessions fewer
# "Gone quiet": no session in the last 8 weeks, from a club that sent at least
# 10 sessions in the 12 months before that, and at least one in the same 8 weeks
# last year (so an off-season lull isn't flagged). Clubs drop off the list once
# their last session is over a year old, so a long-lost club isn't raised forever.
CLUB_ALERT_QUIET_DAYS = 56
CLUB_ALERT_QUIET_MIN_PRIOR_12M = 10
CLUB_ALERT_QUIET_MIN_LAST_YEAR = 1
# Backtest Jun 2025 → Sep 2026 at these settings: 0–7 NEW clubs flagged a month
# (usually 1–3), plus 5–10 repeats from the previous month on one line.
# First month the DM actually goes to Sinead. On that run nothing is a "repeat"
# (she never received last month's), so every flagged club is listed in full.
CLUB_ALERT_FIRST_RUN = "2026-10-01"
