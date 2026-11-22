"""Weekly "Successfully Discharged" wins → private clinicians-only Slack channel.

The physios' treatment-note template carries the question
"Patient Successfully Discharged?" (Yes / Not Yet). That answer is the ONLY
thing that counts as a successful discharge (Martin, 2026-11-22) — never infer
a discharge from cancellation reasons ("Feeling Better" is NOT a discharge),
appointment gaps or free text.

Every Friday at 09:00 this scans the treatment notes written in the previous
7 days (Fri 09:00 -> Fri 09:00) and posts every patient whose note answered Yes:
patient first + last name, the physio, the date. Full names are deliberate and
the channel must stay CLINICIANS ONLY (Martin, 2026-11-22). No clinical free text
leaves Cliniko — structured field only.

Two answer styles exist in the templates (seen Nov 2026):
  radiobuttons  selected option "Yes" / "Not Yet"
  paragraph     "Yes: [X] Not Yet: []"  (ticked box; spacing/case vary)
A note counts only if Yes is selected AND Not Yet is not. Both ticked = ambiguous,
not counted (reported as a count so it can be fixed in Cliniko).

A patient with several Yes notes in the week appears once (latest note), and a
patient who already had a Yes note before the window is never posted again.

Modes:
  python send_discharge_wins_weekly.py          preview — COUNTS ONLY, no names printed
  python send_discharge_wins_weekly.py --dm     full message to the CEO's DM (test)
  python send_discharge_wins_weekly.py --post   post to the wins channel
                                                (env DISCHARGE_WINS_CHANNEL_ID)

Cloud logs carry counts only, never patient names.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

import config
import phase2
import slack_notifier
from progress_scan import patient_name

LONDON = ZoneInfo("Europe/London")
QUESTION = "successfully discharged"
_BOX_RE = re.compile(r"(yes|not\s*yet|no)\s*:?\s*\[\s*([x ]?)\s*\]", re.I)


def previous_week_window(now=None):
    """(start_local, end_local): the 7 days ending at the most recent Friday 09:00.

    Fri 09:00 -> Fri 09:00 so the post (Fri 09:00, before team CPD) covers exactly
    the week since the last one, with no gap or overlap between runs. A run a few
    minutes after 09:00 still resolves to that same Friday 09:00.
    """
    if now is None:
        now = datetime.now(LONDON)
    end = now.replace(hour=9, minute=0, second=0, microsecond=0)
    end -= timedelta(days=(end.weekday() - 4) % 7)          # back to Friday
    if end > now:                                           # Fri before 09:00
        end -= timedelta(days=7)
    return end - timedelta(days=7), end


def _plain(html):
    text = re.sub(r"<br\s*/?>|</p>", " ", str(html or ""), flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("​", "").strip()


def discharge_answer(note):
    """'yes' / 'not_yet' / 'ambiguous' / None (question absent or unanswered)."""
    for sec in (note.get("content") or {}).get("sections") or []:
        for q in sec.get("questions") or []:
            if QUESTION not in (q.get("name") or "").lower():
                continue
            selected = [str(o.get("value", "")).strip().lower()
                        for o in (q.get("answers") or [])
                        if isinstance(o, dict) and o.get("selected")]
            if selected:
                yes = "yes" in selected
                no = any(s.replace(" ", "") in ("notyet", "no") for s in selected)
                return "ambiguous" if yes and no else "yes" if yes else "not_yet"
            if q.get("answer"):
                ticked = {m.group(1).lower().replace(" ", ""): bool(m.group(2).strip())
                          for m in _BOX_RE.finditer(_plain(q["answer"]))}
                yes = ticked.get("yes", False)
                no = ticked.get("notyet", False) or ticked.get("no", False)
                if yes and no:
                    return "ambiguous"
                if yes:
                    return "yes"
                if no:
                    return "not_yet"
    return None


def previously_celebrated(pid, start_local):
    """True if this patient already has a Yes note from BEFORE this week's window,
    i.e. they were celebrated in an earlier post. Stateless (Render has no disk):
    asks Cliniko for the patient's notes, so it also covers the first run after
    the question was introduced."""
    cutoff = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for n in phase2.fetch_all(f"/patients/{pid}/treatment_notes", []):
        if n.get("deleted_at") or n.get("archived_at"):
            continue
        if (n.get("created_at") or "") < cutoff and discharge_answer(n) == "yes":
            return True
    return False


def scan(start_local, end_local):
    """(rows, stats): one row per successfully discharged patient in the window."""
    s = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    e = end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_patient, stats = {}, {"notes": 0, "answered": 0, "ambiguous": 0, "repeat": 0}
    for note in phase2.fetch_all("/treatment_notes", [
            ("q[]", f"created_at:>={s}"), ("q[]", f"created_at:<{e}")]):
        if note.get("deleted_at") or note.get("archived_at"):
            continue
        stats["notes"] += 1
        ans = discharge_answer(note)
        if ans is None:
            continue
        stats["answered"] += 1
        if ans == "ambiguous":
            stats["ambiguous"] += 1
            continue
        if ans != "yes":
            continue
        pid = phase2.id_from_link(note.get("patient"))
        when = (note.get("created_at") or "")[:10]
        prev = by_patient.get(pid)
        if prev and when < prev["date"]:
            continue
        by_patient[pid] = {"patient": patient_name(note),
                           "physio": note.get("author_name", "?"), "date": when}
    for pid in list(by_patient):
        if previously_celebrated(pid, start_local):
            del by_patient[pid]
            stats["repeat"] += 1
    rows = sorted(by_patient.values(), key=lambda r: (r["date"], r["patient"]))
    return rows, stats


def build_text(rows, start_local, end_local):
    span = f"{start_local.strftime('%a %d %b')} – {end_local.strftime('%a %d %b')}"
    if not rows:
        return (f"🏆 *Successful discharges, {span}*\n\nNone ticked this week. "
                "If someone should be here, tick *Patient Successfully Discharged? → Yes* "
                "on their note.")
    n = len(rows)
    lines = [f"🏆 *{n} successful discharge{'s' if n != 1 else ''}, {span}*", ""]
    for r in rows:
        physio = config.PRACTITIONER_DISPLAY_NAME.get(r["physio"], r["physio"])
        date = datetime.strptime(r["date"], "%Y-%m-%d").strftime("%a %d %b")
        lines.append(f"• *{r['patient']}*  _(discharged by {physio}, {date})_")
    return "\n".join(lines)


def post_to_channel(text):
    from slack_sdk.errors import SlackApiError
    channel = os.environ.get("DISCHARGE_WINS_CHANNEL_ID", "").strip()
    if not channel:
        print("DISCHARGE_WINS_CHANNEL_ID not set — nothing posted.")
        return False
    if config.SLACK_SAFE_MODE:
        print("SAFE_MODE on — sending to the CEO's DM instead of the channel.")
        return slack_notifier._send_dm(config.CEO_SLACK_EMAIL, text,
                                       target_label="Discharge wins channel")
    try:
        slack_notifier._get_client().chat_postMessage(
            channel=channel, text=text, unfurl_links=False)
        return True
    except SlackApiError as e:
        print(f"WARN channel post failed: {e.response['error']}")
        return False


def main():
    start, end = previous_week_window()
    print(f"Scanning notes {start:%Y-%m-%d} -> {end:%Y-%m-%d} for successful discharges…",
          flush=True)
    rows, stats = scan(start, end)
    print(f"{len(rows)} successful discharge(s); {stats['notes']} notes scanned, "
          f"{stats['answered']} with the question answered, "
          f"{stats['ambiguous']} ambiguous (both ticked), "
          f"{stats['repeat']} skipped as already celebrated.")
    text = build_text(rows, start, end)
    if "--post" in sys.argv:
        ok = post_to_channel(text)
        print("Posted." if ok else "FAILED to post.")
        sys.exit(0 if ok else 1)
    if "--dm" in sys.argv:
        ok = slack_notifier._send_dm(config.CEO_SLACK_EMAIL, text,
                                     target_label="Discharge wins (test)")
        sys.exit(0 if ok else 1)
    print("(Preview: counts only. --dm sends the names to the CEO; --post goes to the channel.)")


if __name__ == "__main__":
    main()
