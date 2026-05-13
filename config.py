"""Editable configuration — update when team / clinic / appointment types change.

This is the only file a non-developer should ever need to edit.

After editing, no other files need to change. The script picks up new values on
the next run.
"""

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
    "Molaí Smith": "Molaí",
    "Shannagh Conwell": "Shannagh",
}

# Row order in the Performance Dashboard, after Standard / Clinic Average / w/o M&J.
PRACTITIONER_DISPLAY_ORDER = [
    "Marty", "Julie", "Sinead", "Erin", "Daire", "Aoife", "Molaí", "Shannagh",
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
    "Molaí": 128.6,
    "Shannagh": 128.6,
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
    "Daire": "daire@elitephysiocookstown.co.uk",
    "Aoife": "aoifeokane@elitephysiocookstown.co.uk",
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

# Spreadsheet URL used in DM links
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI/edit"

# SAFE MODE: when True, every Slack message gets redirected to the CEO's DM
# (with a "[TEST]" prefix) instead of being sent to physios / reception.
# Flipped LIVE 2026-05-12 after Martin approved the message formats.
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
