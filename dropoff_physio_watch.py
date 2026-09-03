#!/usr/bin/env python3
"""Read the physio-trace logging out of Render and say whether it caught the bug.

Background: drop-off rows keep recording the BOOKED-with physio instead of the
responsible one (~3/week). Replaying the same day locally computes the correct
physio every time, so commit 5cf08c4 added trace logging to the 07:00 cron to
capture what that run actually saw. This reads it back.

For each row the cron attributed, the trace carries the branch that decided it
and `tail` — the last attended appointments present in the history the run was
handed. This script re-derives the answer from Cliniko now and splits any
disagreement into the two possible diagnoses:

  HISTORY SHORT  — the deciding appointment is absent from the run's tail.
                   The patient-filtered fetch came back incomplete at 06:00 UTC.
  LOGIC          — the deciding appointment WAS in the tail and the run still
                   fell back. The bug is in responsible_physio_id.

Silent when nothing is caught. DMs Martin on Slack when something is.

Usage:
  ./venv/bin/python dropoff_physio_watch.py            # last 7 days, print only
  ./venv/bin/python dropoff_physio_watch.py --days 3
  ./venv/bin/python dropoff_physio_watch.py --dm       # DM only if it caught something
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

import phase1_fetch as p1
import phase2 as p2
import config

RENDER_API = "https://api.render.com/v1/logs"
OWNER_ID = "tea-d82a96hkh4rs73c229ig"
DROPOFF_CRON = "crn-d8a4j9ek1jcs73fk6230"   # elite-dropoff-daily

# "  [FALLBACK] Brenda Rushe   appt=2000105824860509509 cancelled branch=B:no-ia-anchor ..."
HEAD_RE = re.compile(
    r"\[(?P<flag>FALLBACK|moved|same)\s*\]\s+(?P<patient>.+?)\s+appt=(?P<appt>\d+)\s+"
    r"(?P<kind>\S+)\s+branch=(?P<branch>\S+)\s+hist=(?P<hist>\d+)")
TAIL_RE = re.compile(r"booked=(?P<booked>.+?)\s+->\s+responsible=(?P<resp>.+?)\s+tail=(?P<tail>\[.*\])")


def fetch_log_lines(days):
    """Render caps each response, so page backwards until the window is covered."""
    key = os.environ.get("RENDER_API_KEY")
    if not key:
        sys.exit("RENDER_API_KEY not set — cannot read the cron log.")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    lines, guard = [], 0
    while guard < 40:
        guard += 1
        r = requests.get(RENDER_API, headers=headers, timeout=60, params={
            "ownerId": OWNER_ID, "resource": DROPOFF_CRON, "limit": 100,
            "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        if r.status_code != 200:
            sys.exit(f"Render logs API {r.status_code}: {r.text[:200]}")
        data = r.json()
        batch = data.get("logs") or []
        lines.extend((e.get("timestamp", ""), str(e.get("message", ""))) for e in batch)
        if not data.get("hasMore") or not data.get("nextEndTime"):
            break
        end = datetime.strptime(data["nextEndTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    lines.sort()
    return lines


def parse_traces(lines):
    """Each trace is two consecutive log lines — a header and a booked/tail line."""
    traces, pending = [], None
    for ts, msg in lines:
        head = HEAD_RE.search(msg)
        if head:
            pending = {"ts": ts[:19], **head.groupdict()}
            continue
        if pending:
            tail = TAIL_RE.search(msg)
            if tail:
                pending.update(tail.groupdict())
                traces.append(pending)
            pending = None
    return traces


def verdicts(traces):
    """Re-derive each traced row from Cliniko now and classify any disagreement."""
    pracs = {k: f'{v.get("first_name")} {v.get("last_name")}'.strip()
             for k, v in p1.all_practitioners().items()}
    cache, out = {}, []
    for t in traces:
        # A cancelled appointment 404s on a direct GET, so resolve it through the
        # patient's own history — the route the tracker uses everywhere else.
        pid = resolve_patient_id(t["appt"], t["patient"], cache)
        if not pid:
            continue
        if pid not in cache:
            cache[pid] = p2.fetch_patient_full_history(pid)
        hist = cache[pid]
        appt = next((a for a in hist if str(a["id"]) == t["appt"]), None)
        if not appt:
            continue
        now_id = p1.responsible_physio_id(appt, hist)
        now_name = pracs.get(str(now_id), "?")
        if now_name == t["resp"].strip():
            continue                      # run agreed with today — nothing to see
        # Disagreement. Was the deciding appointment visible to the run?
        seen = t["tail"]
        deciding = deciding_appt(appt, hist)
        stamp = (deciding.get("starts_at") or "")[:16] if deciding else ""
        out.append({
            **t,
            "recomputed": now_name,
            "deciding": f'{stamp} {pracs.get(str(p1.id_from_link(deciding.get("practitioner"))), "?")}'
                        if deciding else "(none)",
            "diagnosis": "LOGIC" if (stamp and stamp in seen) else "HISTORY SHORT",
        })
    return out


def resolve_patient_id(appt_id, patient_name, cache):
    """Patient ids aren't in the log line, so find the row in the sheet."""
    for pid, hist in cache.items():
        if any(str(a["id"]) == appt_id for a in hist):
            return pid
    surname = patient_name.strip().split()[-1].strip("()")
    for p in p1.fetch_all("/patients", [("q[]", f"last_name:={surname}")]):
        pid = str(p["id"])
        if pid in cache:
            continue
        try:
            hist = p2.fetch_patient_full_history(pid)
        except Exception:
            continue
        cache[pid] = hist
        if any(str(a["id"]) == appt_id for a in hist):
            return pid
    return None


def deciding_appt(appt, hist):
    """The appointment that SHOULD decide attribution: most recent attended in
    the episode. Mirrors responsible_physio_id step 2 rather than re-deriving
    the rule, so the two can't drift apart."""
    strict4 = {str(x) for x in config.PHASE1_DROPOFF_IA_TYPE_IDS}
    excl = ({str(x) for x in config.EXCLUDED_FROM_TOTAL_APPTS}
            | {str(x) for x in config.EXCLUDED_FROM_DROPOFF_STATS})
    astart = appt.get("starts_at") or ""
    attended = [h for h in hist if (h.get("starts_at") or "") < astart
                and not h.get("cancelled_at") and not h.get("did_not_arrive")]
    ias = [h for h in attended
           if str(p1.id_from_link(h.get("appointment_type"))) in strict4]
    if not ias:
        return None
    ia_start = max(h["starts_at"] for h in ias)
    in_ep = [h for h in attended if (h.get("starts_at") or "") >= ia_start
             and str(p1.id_from_link(h.get("appointment_type"))) not in excl]
    return max(in_ep, key=lambda x: x["starts_at"]) if in_ep else None


def main():
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    lines = fetch_log_lines(days)
    traces = parse_traces(lines)
    print(f"Render log: {len(lines)} lines over {days}d, {len(traces)} physio-trace row(s) parsed")
    if not traces:
        print("No trace data yet — has the 07:00 cron run since 5cf08c4 deployed?")
        return
    caught = verdicts(traces)
    branches = {}
    for t in traces:
        branches[t["branch"]] = branches.get(t["branch"], 0) + 1
    print("branches: " + ", ".join(f"{k}={v}" for k, v in sorted(branches.items())))
    if not caught:
        print("Nothing caught — every traced row still recomputes to what the run recorded.")
        return
    lines_out = [f"*Drop-off physio trace — {len(caught)} caught over {days} days*", ""]
    for c in caught:
        lines_out.append(
            f'• *{c["patient"]}* ({c["ts"]}, {c["kind"]}) — run said *{c["resp"].strip()}*, '
            f'should be *{c["recomputed"]}*\n'
            f'    branch `{c["branch"]}`, history {c["hist"]} appts, '
            f'deciding appt `{c["deciding"]}`\n'
            f'    → *{c["diagnosis"]}*')
    msg = "\n".join(lines_out)
    print("\n" + msg)
    if "--dm" in sys.argv:
        import slack_notifier
        slack_notifier._send_dm(config.CEO_SLACK_EMAIL, msg, target_label="Martin")
        print("\nDM sent.")


if __name__ == "__main__":
    main()
