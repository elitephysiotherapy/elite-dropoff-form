"""Phase 4 — Flask app handling Slack interactive button clicks.

Hosted on Render. Slack POSTs button clicks to /slack/interactive.

Flow:
  - Physio clicks [Clinical] / [Non-clinical] on a patient block in their DM
  - Slack POSTs payload → this server
  - We verify signature, decode action_id (format: "<kind>:<appt_id>:<choice>")
  - Update the Google Sheet (clinical_non_clinical, next_step_physio columns)
  - If non-clinical: also DM reception
  - Return updated message blocks so the physio's DM reflects the new state
"""

import os
import json
import hmac
import hashlib
import time
from urllib.parse import urlencode

from flask import Flask, request, jsonify, make_response
import gspread
from google.oauth2.service_account import Credentials
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = Flask(__name__)

# ---------------- Configuration ----------------
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID",
                                "1RC7QkHGAa8dH5ShmwbFyswdrmMOo6HTgkcKZEvqoZbI")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON", "")
RECEPTION_EMAILS = os.environ.get(
    "RECEPTION_NOTIFY_EMAILS",
    "reception@elitephysiocookstown.co.uk,sinead@elitephysiocookstown.co.uk",
).split(",")
TRICKY_PATIENT_URL = (
    "https://app.thegotoclinichub.com/tools/the-difficult-patient-problem-solver.php"
)

# Column positions in W/C tabs (1-indexed for gspread):
COL_PATIENT = 3              # C
COL_PHYSIO = 4               # D
COL_BODY_AREA = 11           # K
COL_CLINICAL_NON_CLINICAL = 12  # L
COL_NEXT_STEP = 13              # M
COL_APPOINTMENT_ID = 18         # R

# ---------------- Lazy-init clients ----------------
_sheets_client = None
_slack_client = None


def get_sheet():
    global _sheets_client
    if _sheets_client is None:
        if not SERVICE_ACCOUNT_JSON:
            raise RuntimeError("SERVICE_ACCOUNT_JSON env var not set")
        creds_dict = json.loads(SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        _sheets_client = client.open_by_key(SPREADSHEET_ID)
    return _sheets_client


def get_slack():
    global _slack_client
    if _slack_client is None:
        _slack_client = WebClient(token=SLACK_BOT_TOKEN)
    return _slack_client


# ---------------- Slack signature verification ----------------
def verify_slack_request():
    ts = request.headers.get("X-Slack-Request-Timestamp", "0")
    try:
        if abs(time.time() - int(ts)) > 60 * 5:
            return False
    except ValueError:
        return False
    body = request.get_data()
    base = f"v0:{ts}:".encode() + body
    my_sig = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256
    ).hexdigest()
    their_sig = request.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(my_sig, their_sig)


# ---------------- Sheet helpers ----------------
def find_row_by_appt_id(sh, appt_id):
    """Locate W/C tab and 1-indexed row containing this appointment_id."""
    appt_id_str = str(appt_id)
    for ws in sh.worksheets():
        if not ws.title.startswith("W/C "):
            continue
        try:
            col = ws.col_values(COL_APPOINTMENT_ID)
        except Exception:
            continue
        for idx, val in enumerate(col, 1):
            if val == appt_id_str:
                return ws, idx
    return None, None


def update_classification(appt_id, clinical=None, next_step=None):
    sh = get_sheet()
    ws, row = find_row_by_appt_id(sh, appt_id)
    if not ws:
        return None
    updates = []
    if clinical is not None:
        updates.append({"range": gspread.utils.rowcol_to_a1(row, COL_CLINICAL_NON_CLINICAL),
                        "values": [[clinical]]})
    if next_step is not None:
        updates.append({"range": gspread.utils.rowcol_to_a1(row, COL_NEXT_STEP),
                        "values": [[next_step]]})
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    values = ws.row_values(row)
    return {
        "tab": ws.title,
        "row": row,
        "patient": values[COL_PATIENT - 1] if len(values) >= COL_PATIENT else "?",
        "physio": values[COL_PHYSIO - 1] if len(values) >= COL_PHYSIO else "?",
        "body_area": values[COL_BODY_AREA - 1] if len(values) >= COL_BODY_AREA else "?",
    }


def notify_reception(ctx):
    """DM reception + Sinéad Rocks when a non-clinical drop-off is confirmed."""
    text = (f"🟢 *{ctx['physio']}* marked *{ctx['patient']}* ({ctx['body_area']}) "
            f"as *non-clinical* → please reactivate")
    slack = get_slack()
    for email in RECEPTION_EMAILS:
        email = email.strip()
        if not email:
            continue
        try:
            u = slack.users_lookupByEmail(email=email)
            uid = u["user"]["id"]
            slack.chat_postMessage(channel=uid, text=text, unfurl_links=False)
        except SlackApiError as e:
            print(f"  reception DM failed for {email}: {e.response.get('error')}")


# ---------------- Block builders ----------------
def actions_block_initial(appt_id):
    return {
        "type": "actions",
        "block_id": f"actions_{appt_id}",
        "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "✅ Clinical"},
             "style": "primary",
             "action_id": f"classify:{appt_id}:clinical"},
            {"type": "button",
             "text": {"type": "plain_text", "text": "❌ Non-clinical"},
             "action_id": f"classify:{appt_id}:non_clinical"},
        ],
    }


def actions_block_next_step(appt_id):
    return {
        "type": "actions",
        "block_id": f"actions_{appt_id}",
        "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "📞 I'll contact directly"},
             "action_id": f"next_step:{appt_id}:contact"},
            {"type": "button",
             "text": {"type": "plain_text", "text": "🔗 Tricky Patient Form"},
             "action_id": f"next_step:{appt_id}:tricky",
             "url": TRICKY_PATIENT_URL},
        ],
    }


def context_block_done(appt_id, summary_md):
    return {
        "type": "context",
        "block_id": f"actions_{appt_id}",
        "elements": [{"type": "mrkdwn", "text": summary_md}],
    }


def replace_actions_block(blocks, appt_id, new_block):
    target = f"actions_{appt_id}"
    return [new_block if b.get("block_id") == target else b for b in blocks]


# ---------------- Routes ----------------
@app.route("/", methods=["GET"])
def index():
    return "Elite Drop-off Form — POST Slack interactions to /slack/interactive", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/slack/interactive", methods=["POST"])
def slack_interactive():
    if not verify_slack_request():
        return make_response("Bad signature", 401)
    try:
        payload = json.loads(request.form["payload"])
    except (KeyError, json.JSONDecodeError):
        return make_response("Bad payload", 400)

    action = payload.get("actions", [{}])[0]
    action_id = action.get("action_id", "")
    parts = action_id.split(":")
    if len(parts) != 3:
        return make_response("", 200)
    kind, appt_id, choice = parts

    original_blocks = payload.get("message", {}).get("blocks", [])

    if kind == "classify":
        if choice == "non_clinical":
            ctx = update_classification(appt_id,
                                        clinical="non clinical",
                                        next_step="reactivate")
            if ctx:
                notify_reception(ctx)
            new = context_block_done(
                appt_id,
                "✅ *Non-clinical* — reception notified for reactivation"
            )
        elif choice == "clinical":
            update_classification(appt_id, clinical="clinical")
            new = actions_block_next_step(appt_id)
        else:
            return make_response("", 200)
        return jsonify({"replace_original": "true",
                        "blocks": replace_actions_block(original_blocks, appt_id, new)})

    if kind == "next_step":
        if choice == "contact":
            update_classification(appt_id, next_step="physio contacting patient directly")
            summary = "✅ *Clinical* → physio contacting directly"
        elif choice == "tricky":
            update_classification(appt_id, next_step="tricky patient form")
            summary = (f"✅ *Clinical* → completing "
                       f"<{TRICKY_PATIENT_URL}|Tricky Patient Form>")
        else:
            return make_response("", 200)
        new = context_block_done(appt_id, summary)
        return jsonify({"replace_original": "true",
                        "blocks": replace_actions_block(original_blocks, appt_id, new)})

    return make_response("", 200)


if __name__ == "__main__":
    # Local dev runner
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
