"""Phase 3 — Slack notifications.

Three message types per daily run:
  - notify_physios(rows)   one DM per physio with their drop-offs
  - notify_reception(rows) one DM to the reception profile with full call list
  - notify_ceo(rows, mtd_pct) one summary DM to Marty

When config.SLACK_SAFE_MODE is True, EVERY message is rerouted to the CEO's DM
prefixed with "[TEST → would have gone to <recipient>]" so Martin can review
the format end-to-end without spamming the team.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import config

load_dotenv(override=True)

LONDON = ZoneInfo("Europe/London")
_client = None
_email_to_userid = {}


def _get_client():
    global _client
    if _client is None:
        _client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    return _client


def _user_id_for_email(email):
    if not email:
        return None
    if email in _email_to_userid:
        return _email_to_userid[email]
    try:
        r = _get_client().users_lookupByEmail(email=email)
        uid = r["user"]["id"]
        _email_to_userid[email] = uid
        return uid
    except SlackApiError as e:
        print(f"  WARN Slack lookup failed for {email}: {e.response['error']}")
        return None


def _send_dm(email, text, *, target_label, blocks=None):
    """Send DM to ONE recipient, honouring SAFE_MODE.
    If `blocks` is provided, sends Block Kit message (text is the fallback)."""
    actual_email = email
    if config.SLACK_SAFE_MODE:
        actual_email = config.CEO_SLACK_EMAIL
        text = f"*[TEST → would have gone to {target_label} ({email})]*\n\n{text}"
    uid = _user_id_for_email(actual_email)
    if not uid:
        return False
    kwargs = {"channel": uid, "text": text, "unfurl_links": False}
    if blocks is not None:
        kwargs["blocks"] = blocks
    try:
        _get_client().chat_postMessage(**kwargs)
        print(f"  Slack DM sent → {target_label}"
              + (" (rerouted to CEO via SAFE_MODE)" if config.SLACK_SAFE_MODE else ""))
        return True
    except SlackApiError as e:
        print(f"  WARN Slack send failed to {actual_email}: {e.response['error']}")
        return False


def _send_dm_to_recipients(emails, text, *, target_label):
    """Send DM to MULTIPLE recipients. In SAFE_MODE, sends one prefixed DM
    to the CEO that lists the intended recipients."""
    if config.SLACK_SAFE_MODE:
        recipients = ", ".join(emails)
        prefixed = f"*[TEST → would have gone to {target_label} ({recipients})]*\n\n{text}"
        uid = _user_id_for_email(config.CEO_SLACK_EMAIL)
        if not uid:
            return False
        try:
            _get_client().chat_postMessage(channel=uid, text=prefixed, unfurl_links=False)
            print(f"  Slack DM (safe) → {target_label} — would-be recipients: {recipients}")
            return True
        except SlackApiError as e:
            print(f"  WARN Slack send failed: {e.response['error']}")
            return False
    for email in emails:
        _send_dm(email, text, target_label=f"{target_label} → {email}")
    return True


# ---------------- Message composition ----------------

_DROPOFF_LABELS = {
    "cancelled": "Cancelled",
    "did_not_attend": "DNA",
    "iadnr": "IADNR (never returned after IA)",
    "iacna": "IACNA (cancelled IA before attending)",
    "iadna": "IADNA (DNA'd IA)",
}


def _format_dropoff(t):
    return _DROPOFF_LABELS.get(t, t)


def _format_patient_line(idx, r, *, with_physio=False):
    patient = r.get("patient", "?")
    body = r.get("body_area") or "(uncategorised)"
    sess = r.get("session_number") or "?"
    dt = _format_dropoff(r.get("dropoff_type", ""))
    notice = r.get("notice") or ""
    parts = [f"{idx}. {patient}"]
    if with_physio:
        parts[-1] += f" ({r.get('physio', '?')}'s patient)"
    parts.append(f"{body}")
    if r.get("dropoff_type") in ("iadnr", "iacna", "iadna"):
        # pre-treatment patterns don't always have a meaningful session count
        parts.append(f"{dt}")
    else:
        parts.append(f"Session {sess} | {dt}")
    if notice and r.get("dropoff_type") in ("cancelled", "iacna"):
        parts.append(f"{notice} notice")
    return " | ".join(parts)


def _physio_dm_text(name, rows):
    """Plain-text fallback (Slack falls back to this if blocks fail to render)."""
    lines = [f"Good morning {name} — yesterday's drop-offs from your caseload:"]
    sorted_rows = sorted(rows, key=lambda r: r.get("appointment_date", ""))
    for i, r in enumerate(sorted_rows, 1):
        lines.append(_format_patient_line(i, r))
    return "\n".join(lines)


def _physio_dm_blocks(name, rows):
    """Block Kit version — each patient gets a [Clinical] [Non-clinical] button row."""
    sorted_rows = sorted(rows, key=lambda r: r.get("appointment_date", ""))
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"Good morning {name} 👋"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": "Here are yesterday's drop-offs from your caseload. "
                          "Tap *Clinical* or *Non-clinical* for each."}},
        {"type": "divider"},
    ]
    for r in sorted_rows:
        appt_id = r.get("appointment_id", "")
        patient = r.get("patient", "?")
        body = r.get("body_area") or "(uncategorised)"
        sess = r.get("session_number") or "?"
        dt = _format_dropoff(r.get("dropoff_type", ""))
        notice = r.get("notice") or ""

        bullets = [f"*{body}*", f"Session {sess}", dt]
        if notice and r.get("dropoff_type") in ("cancelled", "iacna"):
            bullets.append(f"{notice} notice")
        summary = f"*{patient}*\n" + " | ".join(bullets)

        blocks.append({
            "type": "section",
            "block_id": f"patient_{appt_id}",
            "text": {"type": "mrkdwn", "text": summary},
        })
        blocks.append({
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
        })
        blocks.append({"type": "divider"})

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                      "text": f"<{config.SPREADSHEET_URL}|Full drop-off sheet>"}],
    })
    return blocks


def _reception_dm_text(rows):
    lines = [f"📞 Reactivation calls for today — {len(rows)} patients to contact:",
             ""]
    sorted_rows = sorted(rows, key=lambda r: r.get("physio", ""))
    for i, r in enumerate(sorted_rows, 1):
        lines.append(_format_patient_line(i, r, with_physio=True))
    lines.append("")
    lines.append(f"Full details and contact info: {config.SPREADSHEET_URL}")
    return "\n".join(lines)


def _ops_manager_dm_text(rows, ia_rebook_mtd_pct=None, leads_summary=None):
    yesterday = (datetime.now(LONDON) - timedelta(days=1)).strftime("%a %d %b")
    by_type = {k: 0 for k in ("cancelled", "did_not_attend", "iadnr", "iacna", "iadna")}
    for r in rows:
        t = r.get("dropoff_type", "")
        if t in by_type:
            by_type[t] += 1
    by_physio = {}
    for r in rows:
        p = r.get("physio", "?")
        by_physio[p] = by_physio.get(p, 0) + 1

    lines = [f"📊 Drop-off summary for yesterday — {yesterday}",
             ""]
    lines.append(
        f"Total: {len(rows)}  |  "
        f"Cancelled: {by_type['cancelled']}  |  "
        f"DNA: {by_type['did_not_attend']}  |  "
        f"IADNR: {by_type['iadnr']}  |  "
        f"IACNA: {by_type['iacna']}  |  "
        f"IADNA: {by_type['iadna']}"
    )
    lines.append("")
    if by_physio:
        lines.append("By physio:")
        sorted_physios = sorted(by_physio.items(), key=lambda x: -x[1])
        lines.append("  " + " | ".join(f"{p}: {n}" for p, n in sorted_physios))
        lines.append("")
    if ia_rebook_mtd_pct is not None:
        month_label = datetime.now(LONDON).strftime("%B")
        lines.append(f"IA Rebook Rate ({month_label} MTD): {ia_rebook_mtd_pct:.0f}%")
        lines.append("")
    if leads_summary:
        not_booked = leads_summary.get("not_booked", 0)
        parts = []
        for k in ("pending", "lost", "declined"):
            n = leads_summary.get(k, 0)
            if n:
                parts.append(f"{n} {k}")
        breakdown = " (" + ", ".join(parts) + ")" if parts else ""
        lines.append(f"Leads this week: {not_booked} not booked{breakdown}")
        lines.append("")
    lines.append(f"Full trend sheet: {config.SPREADSHEET_URL}")
    return "\n".join(lines)


# ---------------- Public API ----------------

def notify_physios(rows):
    """One DM per physio with their drop-offs (interactive Block Kit buttons)."""
    by_physio = {}
    for r in rows:
        by_physio.setdefault(r.get("physio", "?"), []).append(r)
    for full_physio_name, physio_rows in by_physio.items():
        display = config.PRACTITIONER_DISPLAY_NAME.get(full_physio_name, full_physio_name)
        email = config.PHYSIO_SLACK_EMAIL.get(display)
        if not email:
            print(f"  No Slack email for physio '{display}' — skipping DM")
            continue
        text = _physio_dm_text(display, physio_rows)        # plain-text fallback
        blocks = _physio_dm_blocks(display, physio_rows)    # interactive buttons
        _send_dm(email, text, target_label=f"physio {display}", blocks=blocks)


def notify_reception(rows):
    """Send the daily reactivation call list to reception + Ops Manager."""
    if not rows:
        return
    text = _reception_dm_text(rows)
    _send_dm_to_recipients(
        config.RECEPTION_LIST_SLACK_EMAILS,
        text,
        target_label="reception + Ops Manager",
    )


def notify_ops_manager(rows, ia_rebook_mtd_pct=None, leads_summary=None,
                       summary_rows=None):
    """Send the daily Ops Manager Summary to CEO + Sinéad Rocks.

    `summary_rows`, when given, is the full set of drop-offs *dated yesterday*
    (regardless of when they were first written to the sheet). The summary
    counts those so it reflects what actually happened yesterday — not just the
    rows this run happened to add. Falls back to `rows` if not supplied.
    """
    counted = summary_rows if summary_rows is not None else rows
    text = _ops_manager_dm_text(counted, ia_rebook_mtd_pct=ia_rebook_mtd_pct,
                                leads_summary=leads_summary)
    _send_dm_to_recipients(
        config.OPS_MANAGER_SLACK_EMAILS,
        text,
        target_label="Ops Manager Summary (Marty + Sinéad Rocks)",
    )


def send_all(rows, ia_rebook_mtd_pct=None, leads_summary=None, summary_rows=None):
    if config.SLACK_SAFE_MODE:
        print(f"Slack SAFE_MODE is ON — all messages will go to {config.CEO_SLACK_EMAIL}")
    if not rows:
        print("  No NEW drop-offs to notify physios/reception about. Still sending Ops Manager summary.")
    # Physio DMs + reception call list are driven off NEW rows only — we don't
    # re-ping people about drop-offs already captured and actioned. The Ops
    # Manager summary, by contrast, reports yesterday's full picture.
    #
    # Sports Massage drop-offs now appear in the sheet (Martin 2026-07-20) but
    # aren't clinical, so they don't earn a physio a chase-up DM. They stay in
    # the reception call list and the Ops Manager summary.
    clinical_rows = [r for r in rows
                     if str(r.get("_appointment_type_id")) not in
                     {str(x) for x in config.EXCLUDED_FROM_DROPOFF_STATS}]
    notify_physios(clinical_rows)
    notify_reception(rows)
    notify_ops_manager(rows, ia_rebook_mtd_pct=ia_rebook_mtd_pct,
                       leads_summary=leads_summary, summary_rows=summary_rows)
