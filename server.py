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
import base64
import time
import threading
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests as http
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
TALLY_SIGNING_SECRET = os.environ.get("TALLY_SIGNING_SECRET", "")

# Column positions in W/C tabs (1-indexed for gspread):
COL_PATIENT = 3              # C
COL_PHYSIO = 4               # D
COL_BODY_AREA = 11           # K
COL_CLINICAL_NON_CLINICAL = 12  # L
COL_NEXT_STEP = 13              # M
COL_REACTIVATION_STATUS = 14    # N
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


def update_classification(appt_id, clinical=None, next_step=None, reactivation_status=None):
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
    if reactivation_status is not None:
        updates.append({"range": gspread.utils.rowcol_to_a1(row, COL_REACTIVATION_STATUS),
                        "values": [[reactivation_status]]})
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


def notify_reception(ctx, decision="reactivate"):
    """DM reception + Sinéad Rocks with the physio's decision on a non-clinical drop-off."""
    if decision == "reactivate":
        text = (f"🟢 *{ctx['physio']}* marked *{ctx['patient']}* ({ctx['body_area']}) "
                f"as *non-clinical* → please reactivate")
    elif decision == "not_appropriate":
        text = (f"⚠️ *{ctx['physio']}* marked *{ctx['patient']}* ({ctx['body_area']}) "
                f"as *non-clinical / not appropriate for physio* — "
                f"no reactivation call needed")
    else:
        return
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


def actions_block_non_clinical_next_step(appt_id):
    """Second-stage buttons after Non-clinical is clicked."""
    return {
        "type": "actions",
        "block_id": f"actions_{appt_id}",
        "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "📞 Reactivate"},
             "style": "primary",
             "action_id": f"non_clinical_next:{appt_id}:reactivate"},
            {"type": "button",
             "text": {"type": "plain_text", "text": "⚠️ Not Appropriate for Physio"},
             "action_id": f"non_clinical_next:{appt_id}:not_appropriate"},
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


# ---------------- Keep-alive ----------------
# Render's free instance type spins down after 15 min without traffic; waking
# takes ~45s, far beyond Slack's 3s button-click window, so clicks die until
# the server is warm. Self-pinging /health every 10 min during clinic waking
# hours keeps the instance up while staying inside the free tier's monthly
# instance-hours allowance.
KEEP_ALIVE_URL = os.environ.get(
    "RENDER_EXTERNAL_URL", "https://elite-dropoff-form.onrender.com"
)
UK_TZ = ZoneInfo("Europe/London")


def _keep_alive_loop():
    while True:
        now = datetime.now(UK_TZ)
        in_waking_hours = (
            (now.hour, now.minute) >= (6, 45) and (now.hour, now.minute) <= (21, 30)
        )
        if in_waking_hours:
            try:
                http.get(f"{KEEP_ALIVE_URL}/health", timeout=30)
            except Exception as exc:
                print(f"keep-alive ping failed: {exc}")
        time.sleep(600)


threading.Thread(target=_keep_alive_loop, daemon=True).start()


# ---------------- Routes ----------------
@app.route("/", methods=["GET"])
def index():
    return "Elite Drop-off Form — POST Slack interactions to /slack/interactive", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


def _process_action_async(action_id, response_url, original_blocks, fallback_text):
    """Heavy work runs in background. We POST the updated message back to Slack's
    response_url when done — Slack waits up to ~30 min for this and updates the
    user's message in place. Required because Sheet updates take >3s, exceeding
    Slack's synchronous response window."""
    try:
        parts = action_id.split(":")
        if len(parts) != 3:
            return
        kind, appt_id, choice = parts

        new_block = None
        if kind == "classify":
            if choice == "non_clinical":
                # Mark Non-clinical, then ask: reactivate or not appropriate?
                # Clear any stale next_step in case they previously clicked Clinical.
                update_classification(appt_id, clinical="non clinical", next_step="")
                new_block = actions_block_non_clinical_next_step(appt_id)
            elif choice == "clinical":
                # Clear stale next_step in case they previously clicked Non-clinical.
                update_classification(appt_id, clinical="clinical", next_step="")
                new_block = actions_block_next_step(appt_id)
        elif kind == "non_clinical_next":
            if choice == "reactivate":
                ctx = update_classification(appt_id, next_step="reactivate")
                if ctx:
                    notify_reception(ctx, decision="reactivate")
                new_block = context_block_done(
                    appt_id,
                    "✅ *Non-clinical → Reactivate* — reception notified",
                )
            elif choice == "not_appropriate":
                # Final state — no reception call needed. Auto-close the reactivation
                # workflow by marking reactivation_status='leave' (red in the manual sheet).
                ctx = update_classification(
                    appt_id,
                    next_step="not appropriate",
                    reactivation_status="leave",
                )
                if ctx:
                    notify_reception(ctx, decision="not_appropriate")
                new_block = context_block_done(
                    appt_id,
                    "✅ *Non-clinical → Not appropriate for physio* — reception notified, no call needed",
                )
        elif kind == "next_step":
            if choice == "contact":
                update_classification(appt_id,
                                      next_step="physio contacting patient directly")
                new_block = context_block_done(
                    appt_id, "✅ *Clinical* → physio contacting directly"
                )
            elif choice == "tricky":
                update_classification(appt_id, next_step="tricky patient form")
                new_block = context_block_done(
                    appt_id,
                    f"✅ *Clinical* → completing "
                    f"<{TRICKY_PATIENT_URL}|Tricky Patient Form>",
                )

        if new_block is None:
            return

        updated = replace_actions_block(original_blocks, appt_id, new_block)
        http.post(response_url, json={
            "replace_original": True,
            "text": fallback_text,
            "blocks": updated,
        }, timeout=10)
    except Exception as exc:
        print(f"async-process error for {action_id}: {exc}")


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

    response_url = payload.get("response_url")
    original_blocks = payload.get("message", {}).get("blocks", [])
    fallback_text = payload.get("message", {}).get("text", "Drop-offs")

    # ACK Slack within 3s, do the heavy lifting in a background thread.
    threading.Thread(
        target=_process_action_async,
        args=(action_id, response_url, original_blocks, fallback_text),
        daemon=True,
    ).start()
    return make_response("", 200)


# ---------------- Tally NPS survey webhook ----------------
def verify_tally_request(raw_body):
    """Verify Tally's HMAC-SHA256 signature. If no secret is configured the
    check is skipped (with a warning) so the endpoint still works in testing."""
    if not TALLY_SIGNING_SECRET:
        print("WARN: TALLY_SIGNING_SECRET not set — skipping Tally signature check")
        return True
    sig = request.headers.get("Tally-Signature", "")
    digest = hmac.new(TALLY_SIGNING_SECRET.encode(), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, sig)


def _tally_fields(payload):
    """Flatten Tally's data.fields list into {lowercased label: value}."""
    out = {}
    for f in (payload.get("data") or {}).get("fields", []) or []:
        label = (f.get("label") or "").strip().lower()
        if label:
            out[label] = f.get("value")
    return out


def _tally_find(fields, *exact, contains=None):
    for name in exact:
        if name.lower() in fields:
            return fields[name.lower()]
    if contains:
        for label, val in fields.items():
            if contains.lower() in label:
                return val
    return None


def _normalise_tally(payload):
    """Tally webhook payload -> the dict marketing.detractor.handle_response wants.

    Hidden fields are matched by exact label; the score and branch questions by
    keyword (confirm the form's question labels match when the Tally form is built).
    """
    f = _tally_fields(payload)
    data = payload.get("data") or {}
    # Tally's stable per-submission id — used downstream as an idempotency key so a
    # webhook retry / double-submit can't write the same NPS response to the sheet twice.
    response_id = data.get("responseId") or data.get("submissionId") or ""
    raw_score = _tally_find(f, "nps_score", contains="recommend")
    try:
        score = int(float(raw_score)) if raw_score not in (None, "") else None
    except (ValueError, TypeError):
        score = None
    callback_raw = _tally_find(f, "callback_wanted", contains="call you")
    callback_wanted = "yes" in str(callback_raw or "").lower()
    open_text = (_tally_find(f, "detractor_feedback", contains="went wrong")
                 or _tally_find(f, "passive_feedback", contains="9 or 10") or "")
    return {
        "response_id": response_id,
        "patient_id": _tally_find(f, "patient_id") or "",
        "patient_name": _tally_find(f, "patient_name") or "",
        "patient_email": _tally_find(f, "patient_email") or "",
        "patient_phone": _tally_find(f, "patient_phone") or "",
        "physio_name": _tally_find(f, "physio_name") or "",
        "clinic_name": _tally_find(f, "clinic_name") or "",
        "trigger_type": _tally_find(f, "trigger_type") or "",
        "appointment_date": _tally_find(f, "appointment_date") or "",
        "nps_score": score,
        "open_text": open_text or "",
        "callback_wanted": callback_wanted,
        "callback_number": _tally_find(f, "callback_number",
                                       contains="number to reach") or "",
    }


def _process_tally_async(resp):
    """Heavy work (sheet writes, sends) runs off the request thread."""
    try:
        from marketing import detractor
        status = detractor.handle_response(resp)
        print(f"tally webhook: {status} (patient {resp.get('patient_id')})")
    except Exception as exc:
        print(f"tally webhook error: {exc}")


@app.route("/tally/webhook", methods=["POST"])
def tally_webhook():
    raw = request.get_data()
    if not verify_tally_request(raw):
        return make_response("Bad signature", 401)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return make_response("Bad payload", 400)
    resp = _normalise_tally(payload)
    if resp.get("nps_score") is None:
        print("tally webhook: no score in payload — ignoring")
        return make_response("", 200)
    threading.Thread(target=_process_tally_async, args=(resp,), daemon=True).start()
    return make_response("", 200)


if __name__ == "__main__":
    # Local dev runner
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
