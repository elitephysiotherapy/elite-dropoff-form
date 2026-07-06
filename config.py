"""Editable configuration — update when team / clinic / appointment types change.

This is the only file a non-developer should ever need to edit.

After editing, no other files need to change. The script picks up new values on
the next run.
"""

import os

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

CLINIC_WEEKLY_HOURS = 198.25       # total available service hours per week
CLINIC_MONTHLY_HOURS = 891.3       # total available service hours per month

# Maps each Cliniko practitioner full name → short display name on the dashboard.
# "X CS" entities (e.g. Martin Loughran CS) are folded into the main person.
PRACTITIONER_DISPLAY_NAME = {
    "Martin Loughran": "Marty",
    "Martin Loughran CS": "Marty",
    "Julie McVey": "Julie",
    "Julie McVey CS": "Julie",
    "Sinead McGill": "Sinead",
    "Erin McNicholl": "Erin",
    "Daire McKenna": "Daire",
    "Aoife O'Kane": "Aoife",
    "Ciara O'Kane": "Ciara",
    "Molaí Smith": "Molaí",
    "Shannagh Conwell": "Shannagh",
}

# Row order in the Performance Dashboard, after Standard / Clinic Average / w/o M&J.
PRACTITIONER_DISPLAY_ORDER = [
    "Marty", "Julie", "Sinead", "Erin", "Daire", "Aoife", "Ciara", "Molaí", "Shannagh",
]

# Per-physio monthly available service hours (used for monthly Utilization KPI).
# Keys are the display name. Update if a physio's hours change.
PHYSIO_MONTHLY_HOURS = {
    "Marty": 83.1,
    "Julie": 36.7,
    "Erin": 128.6,
    "Daire": 128.6,
    "Sinead": 128.6,
    "Aoife": 128.6,
    "Ciara": 128.6,
    "Molaí": 128.6,
    "Shannagh": 128.6,
}

# Per-physio WEEKLY available service hours — derived from PHYSIO_MONTHLY_HOURS
# (monthly ÷ 4.345 average weeks per month). Powers the weekly per-physio
# Utilisation column on the "Weekly Team Stats" tab. These are DERIVED figures:
# to get exact weekly utilisation for any physio, replace their entry below with
# their true contracted weekly service hours.
PHYSIO_WEEKLY_HOURS = {
    name: round(hours / 4.345, 1) for name, hours in PHYSIO_MONTHLY_HOURS.items()
}

# Physios EXCLUDED from "w/o M&J" rollup (clinic minus owner-consultants).
EXCLUDE_FROM_MAIN_TEAM = {"Marty", "Julie"}


# ===========================================================================
# SLACK NOTIFICATION CONFIG
# ===========================================================================

# Map display name → email (used to look up Slack user ID at runtime).
# Update if a physio's Slack/clinic email changes.
PHYSIO_SLACK_EMAIL = {
    "Marty": "martin@elitephysiocookstown.co.uk",
    "Julie": "julie@elitephysiocookstown.co.uk",
    "Sinead": "sineadmcgill@elitephysiocookstown.co.uk",
    "Erin": "erin@elitephysiocookstown.co.uk",
    # Daire McKenna left 2 Jul 2026 — removed so no DMs are sent to his account.
    "Aoife": "aoifeokane@elitephysiocookstown.co.uk",
    "Ciara": "ciara@elitephysiocookstown.co.uk",
    "Molaí": "molai@elitephysiocookstown.co.uk",
    "Shannagh": "shannagh@elitephysiocookstown.co.uk",
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

# Slack channel where package-of-care sales are posted (used by the weekly
# packages count DM to Sinead Rocks). #packages.
PACKAGES_CHANNEL_ID = "C04G5CKN60Y"

# Spreadsheet URL used in DM links
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI/edit"

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
    "total_appts_Cookstown": 225,
    "total_appts_Maghera": 50,
    "ias_Cookstown": 40,
    "ias_Maghera": 10,
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
