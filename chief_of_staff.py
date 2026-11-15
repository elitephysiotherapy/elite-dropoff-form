#!/usr/bin/env python3
"""Chief of Staff — nightly health report on every bot and automation.

Answers one question each night at ~21:10: "did everything that should have
run in the last 24 hours actually run, and do its job?" It checks OUTCOMES,
not just whether a process is up — the 8-14 Nov 2026 speed-to-lead freeze had
a healthy server and no errors; it simply stopped doing anything.

Sources (all read-only):
  * Render — every cron firing in the window (ran? succeeded? overran?),
    web services' /health, the elite-bots worker's hourly heartbeat lines
    (runs / crashes / timeouts per bot), and Sheets-quota (429) log lines.
  * Outcomes — comms check ran today; speed-to-lead end-of-day report went;
    every Meta lead Meta counted actually reached the Online Leads sheet.
  * This Mac (only when run here) — the launchd jobs' last-success markers.
  * Claude scheduled tasks + claude.ai routines — passed in by the nightly
    Claude task as JSON (--extra), because only Claude's tools can see them.

Usage:
  python chief_of_staff.py                  print the report (no Slack)
  python chief_of_staff.py --post [--extra f.json]   post to Martin's Slack DM + mark posted
  python chief_of_staff.py --backup         Render cron 21:45: post a Render-only
                                            report ONLY if tonight's wasn't posted
  python chief_of_staff.py --json           raw findings as JSON
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc
RENDER = "https://api.render.com/v1"
MARKER_FILE = os.path.join(HERE, "data", "chief_of_staff", "last_posted")

# Runs longer than this (minutes) are flagged "slow" — a hang or a quota fight.
SLOW_MIN = {"elite-dropoff-daily": 110, "elite-ltv-monthly": 120,
            "elite-cfo-weekly": 60, "elite-reactivations-monthly": 35,
            "elite-marketing-poll": 20}
SLOW_DEFAULT = 20

# When a Render cron's schedule was last changed (UTC). Add a line here
# whenever you move a cron, or the first night will report false "skipped" runs.
SCHEDULE_CHANGED = {n: datetime(2026, 11, 15, 10, 45, tzinfo=timezone.utc) for n in (
    "elite-dropoff-daily", "elite-reactivations-monthly", "elite-meta-funnel",
    "elite-reactivations-weekly", "elite-packages-weekly", "elite-cfo-weekly")}

# Local (Mac) launchd jobs: (label, marker file, period, earliest London hour).
MAC_JOBS = [
    ("TeamUp → Cliniko Pilates bot", "~/teamup-cliniko/data/last_run_date", "day", 7),
    ("Vimeo exercise library", "~/vimeo-library/data/last_run_week", "day", 12),
    ("Content Results (Grok doc)", "~/cliniko-dropoffs/data/content_results/last_run_week", "isoweek", 6),
]


# --------------------------------------------------------------------------- helpers
def _h():
    return {"Authorization": f"Bearer {os.environ['RENDER_API_KEY']}"}


def _get(path, **params):
    r = requests.get(f"{RENDER}{path}", headers=_h(), params=params, timeout=30)
    r.raise_for_status()
    return json.loads(r.text, strict=False)


def _ts(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def _field_match(spec: str, val: int, lo: int, hi: int) -> bool:
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, st = part.split("/")
            step = int(st)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
        if a <= val <= b and (val - a) % step == 0:
            return True
    return False


def cron_firings(expr: str, start: datetime, end: datetime) -> list[datetime]:
    """UTC firings of a 5-field cron expression in [start, end)."""
    m, h, dom, mon, dow = expr.split()
    out, t = [], start.replace(second=0, microsecond=0)
    if t < start:
        t += timedelta(minutes=1)
    while t < end:
        if (_field_match(m, t.minute, 0, 59) and _field_match(h, t.hour, 0, 23)
                and _field_match(dom, t.day, 1, 31) and _field_match(mon, t.month, 1, 12)
                and _field_match(dow, (t.isoweekday() % 7), 0, 6)):
            out.append(t)
        t += timedelta(minutes=1)
    return out


def ldn(t: datetime) -> str:
    return t.astimezone(LONDON).strftime("%a %H:%M")


# --------------------------------------------------------------------------- Render
def services():
    out, cur = [], None
    while True:
        p = {"limit": 100}
        if cur:
            p["cursor"] = cur
        r = _get("/services", **p)
        if not r:
            break
        out += [x["service"] for x in r]
        cur = r[-1]["cursor"]
        if len(r) < 100:
            break
    return out


def cron_events(svc_id, since):
    evs, cur = [], None
    for _ in range(5):
        p = {"limit": 100, "startTime": since.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if cur:
            p["cursor"] = cur
        r = _get(f"/services/{svc_id}/events", **p)
        if not r:
            break
        evs += [x["event"] for x in r]
        cur = r[-1]["cursor"]
        if len(r) < 100:
            break
    return evs


def logs(owner, resources, since, until, text=None, limit=100, pages=5):
    out, start = [], since.strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(pages):
        p = {"ownerId": owner, "resource": resources, "startTime": start,
             "endTime": until.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": limit,
             "direction": "forward"}
        if text:
            p["text"] = text
        d = _get("/logs", **p)
        out += d.get("logs", [])
        if not d.get("hasMore"):
            break
        start = d["nextStartTime"]
    return out


def check_render(since, now, findings):
    svcs = [s for s in services() if s.get("suspended") == "not_suspended"]
    owner = svcs[0]["ownerId"]
    crons = [s for s in svcs if s["type"] == "cron_job"]
    ok_runs = 0
    for s in sorted(crons, key=lambda s: s["name"]):
        expr = s["serviceDetails"].get("schedule", "")
        # A schedule edited recently would make the old times look "skipped":
        # only expect firings of the CURRENT schedule since it changed. (Render's
        # updatedAt moves on every deploy, so changes are listed by hand.)
        changed = SCHEDULE_CHANGED.get(s["name"], since)
        due = cron_firings(expr, max(since, changed), now - timedelta(minutes=10)) if expr else []
        evs = cron_events(s["id"], since - timedelta(hours=2))
        started, ended = {}, {}
        for e in evs:
            rid = (e.get("details") or {}).get("cronJobRunId")
            if not rid:
                continue
            if e["type"] == "cron_job_run_started":
                started[rid] = _ts(e["timestamp"])
            elif e["type"] == "cron_job_run_ended":
                ended[rid] = (_ts(e["timestamp"]), e["details"].get("status"))
        missed = []
        for f in due:
            # the run Render started for this firing (within 10 min of it)
            rid = next((r for r, t in started.items() if timedelta(0) <= t - f < timedelta(minutes=10)), None)
            if rid is None:
                missed.append(f)
                continue
            if rid not in ended:
                run_for = (now - started[rid]).total_seconds() / 60
                if run_for > SLOW_MIN.get(s["name"], SLOW_DEFAULT):
                    findings["problems"].append(
                        f"*{s['name']}* started {ldn(started[rid])} and is still running "
                        f"after {run_for:.0f} min — likely stuck.")
                continue
            t_end, status = ended[rid]
            mins = (t_end - started[rid]).total_seconds() / 60
            if status != "successful":
                why = last_error(owner, s["id"], started[rid], t_end)
                findings["problems"].append(
                    f"*{s['name']}* failed at {ldn(started[rid])} ({mins:.0f} min)"
                    + (f": `{why}`" if why else "."))
            else:
                ok_runs += 1
                if mins > SLOW_MIN.get(s["name"], SLOW_DEFAULT):
                    findings["watch"].append(
                        f"{s['name']} took {mins:.0f} min ({ldn(started[rid])}) — slower than normal.")
        if missed:
            findings["problems"].append(
                f"*{s['name']}* skipped {len(missed)} scheduled run(s)"
                + (f" ({ldn(missed[0])}–{ldn(missed[-1])})" if len(missed) > 1 else f" ({ldn(missed[0])})")
                + " — Render didn't start them (usually a previous run still stuck).")
    findings["counts"]["render_runs_ok"] = ok_runs
    findings["counts"]["render_crons"] = len(crons)

    for s in svcs:
        if s["type"] == "web_service":
            url = s["serviceDetails"].get("url")
            try:
                r = requests.get(url + "/health", timeout=20)
                if r.status_code != 200:
                    r = requests.get(url, timeout=20)
                good = r.status_code < 500
            except requests.RequestException:
                good = False
            if not good:
                findings["problems"].append(f"*{s['name']}* (web) isn't answering.")

    worker = next((s for s in svcs if s["name"] == "elite-bots"), None)
    if worker is None:
        findings["problems"].append("*elite-bots* worker is missing or suspended — no bots are running.")
    else:
        check_worker(owner, worker, since, now, findings)

    # Sheets / API quota pressure across everything
    by_region = {}
    for s in svcs:      # the logs API takes several services only within one region
        by_region.setdefault(s["serviceDetails"].get("region", "?"), []).append(s["id"])
    chunks = [ids[i:i + 8] for ids in by_region.values() for i in range(0, len(ids), 8)]
    hits = {}
    for term, chunk in [(t, ch) for t in ("RESOURCE_EXHAUSTED", "Quota exceeded", "429")
                        for ch in chunks]:
        try:
            found = logs(owner, chunk, since, now, text=term, pages=3)
        except requests.RequestException:
            findings["info"].append("Quota-hit search timed out for some services (Render logs slow).")
            continue
        for l in found:
            if "/health" in l["message"] or '" 2' in l["message"]:
                continue
            name = next((s["name"] for s in svcs if s["id"] == (l.get("labels") and
                         next((x["value"] for x in l["labels"] if x["name"] == "resource"), ""))), "?")
            hits.setdefault(name, set()).add(l["timestamp"][:16])
    if hits:
        total = sum(len(v) for v in hits.values())
        top = ", ".join(f"{n} ({len(v)})" for n, v in sorted(hits.items(), key=lambda x: -len(x[1]))[:4])
        findings["watch"].append(f"Google/API 'too busy' (quota) hits: {total} minute(s) — {top}. "
                                 f"Retried automatically unless listed above.")


def last_error(owner, svc_id, t0, t1):
    try:
        lines = logs(owner, [svc_id], t0, t1 + timedelta(minutes=1), pages=3)
    except Exception:
        return ""
    bad = [l["message"].strip() for l in lines
           if re.search(r"Error|Exception|FAILED|failed|Timeout|timed out", l["message"])
           and not l["message"].lstrip().startswith("File ")]
    return bad[-1][:180] if bad else ""


def check_worker(owner, worker, since, now, findings):
    lines = logs(owner, [worker["id"]], since, now, text="[bot_worker]", pages=4)
    beats = [l for l in lines if "heartbeat:" in l["message"]]
    hours = (now - since).total_seconds() / 3600
    totals = {}
    for l in beats:
        for part in l["message"].split("heartbeat:", 1)[1].split("|"):
            m = re.match(r"\s*(\w+) ok=(\d+) crash=(\d+) timeout=(\d+)", part)
            if m:
                t = totals.setdefault(m.group(1), [0, 0, 0])
                for i in range(3):
                    t[i] += int(m.group(i + 2))
    try:
        dep = _get(f"/services/{worker['id']}/deploys", limit=1)[0]["deploy"]
        live_since = _ts(dep.get("finishedAt") or dep["createdAt"])
    except Exception:
        live_since = since
    hours = (now - max(since, live_since)).total_seconds() / 3600
    expected_beats = int(hours) - 1
    if expected_beats >= 1 and len(beats) < expected_beats:
        findings["problems"].append(
            f"*elite-bots* worker only reported {len(beats)} of ~{int(hours)} hourly check-ins — "
            f"it may have been down or restarting.")
    want = {"speed_to_lead": 60 * len(beats) * 0.8, "team_sheet": 1 * len(beats) * 0.8,
            "comms_check": 10 * len(beats) * 0.8}
    for job, (ok, crash, to) in totals.items():
        if crash or to:
            findings["problems" if (crash + to) >= 3 else "watch"].append(
                f"{job}: {crash} crashed and {to} timed-out run(s) today (each retried clean).")
        floor = want.get(job)
        if floor and ok < floor:
            findings["problems"].append(f"*{job}* only completed {ok} runs (expected ~{int(floor / 0.8)}).")
    findings["counts"]["bot_runs_ok"] = sum(v[0] for v in totals.values())
    skips = [l for l in lines if "held by another process" in l["message"]]
    if len(skips) > 5:
        findings["problems"].append(f"Bots were blocked by a held lock {len(skips)} times — the freeze pattern. Check now.")


# --------------------------------------------------------------------------- outcomes
def check_outcomes(now, findings):
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
    import config
    import speed_to_lead as stl
    nl = now.astimezone(LONDON)

    # Comms check: once a day from 07:40.
    try:
        import comms_check as cc
        last = cc._last_run(cc.check_ws(cc.marketing_sheet()))
        if (nl.hour, nl.minute) >= (8, 30) and last != nl.date().isoformat():
            findings["problems"].append(f"*Comms check* didn't run today (last run {last or 'never'}).")
    except Exception as exc:
        findings["watch"].append(f"Couldn't read the comms check marker: {exc}")

    # Speed-to-lead end-of-day report (desk close + 5 min, Mon-Fri).
    try:
        ws = stl.state_ws()
        state = stl.read_state(ws)
        marker = state.get(stl.REPORT_KEY)
        last = stl._parse(marker["Seen At"]) if marker else None
        # most recent report that should have gone by now (look back 4 days)
        due = None
        for back in range(0, 4):
            d = stl.report_due_at(nl - timedelta(days=back))
            if d:
                d = d.replace(year=(nl - timedelta(days=back)).year, month=(nl - timedelta(days=back)).month,
                              day=(nl - timedelta(days=back)).day)
                if d + timedelta(minutes=10) <= nl:
                    if nl - d <= timedelta(hours=24):
                        due = d
                    break
        if due and (not last or last < due):
            findings["problems"].append(
                f"*Speed-to-lead* end-of-day report due {due.strftime('%a %H:%M')} didn't go out.")
        # New leads in the last 24h, texted?
        since = nl - timedelta(hours=24)
        new = [r for k, r in state.items() if not k.startswith(("__", "test:"))
               and (stl._parse(r["Lead Created"]) or since) >= since]
        texted = [r for r in new if r["Text Status"].startswith("sent")]
        failed = [r for r in new if r["Text Status"].startswith("FAILED")]
        findings["counts"]["leads_24h"] = len(new)
        findings["counts"]["leads_texted_24h"] = len(texted)
        if failed:
            findings["problems"].append(f"*Speed-to-lead*: {len(failed)} lead text(s) FAILED to send — call them.")
    except Exception as exc:
        findings["watch"].append(f"Couldn't read the Speed to Lead tab: {exc}")

    # Every Meta lead reached the Online Leads sheet (the 14 Nov gap).
    try:
        tok = os.environ.get("META_ACCESS_TOKEN")
        if tok:
            d0 = (nl - timedelta(days=2)).date().isoformat()
            r = requests.get(f"https://graph.facebook.com/v21.0/act_{config.META_AD_ACCOUNT_ID if hasattr(config, 'META_AD_ACCOUNT_ID') else '164830011'}/insights",
                             params={"access_token": tok, "level": "account", "fields": "actions",
                                     "time_range": json.dumps({"since": d0, "until": nl.date().isoformat()}),
                                     "time_increment": 1}, timeout=30).json()
            meta = {}
            for row in r.get("data", []):
                a = {x["action_type"]: int(float(x["value"])) for x in row.get("actions", [])}
                meta[row["date_start"]] = a.get("lead", 0)
            sheet = {}
            for l in stl.read_leads():
                if l.get("dt"):
                    k = l["dt"].astimezone(LONDON).date().isoformat()
                    sheet[k] = sheet.get(k, 0) + 1
            # yesterday only (today's Meta numbers are still filling in)
            y = (nl - timedelta(days=1)).date().isoformat()
            if meta.get(y, 0) > sheet.get(y, 0):
                findings["problems"].append(
                    f"*Meta leads*: Meta counts {meta[y]} lead(s) yesterday but only {sheet.get(y, 0)} "
                    f"reached the Online Leads sheet — check Meta Leads Center; the missing one(s) "
                    f"won't be texted.")
            findings["counts"]["meta_leads_yesterday"] = meta.get(y, 0)
    except Exception as exc:
        findings["watch"].append(f"Couldn't compare Meta leads with the sheet: {exc}")


# --------------------------------------------------------------------------- Mac
def check_mac(now, findings):
    if not os.path.isdir(os.path.expanduser("~/teamup-cliniko")):
        findings["info"].append("Mac jobs not checked (this run is on Render — the Mac report didn't run).")
        return
    nl = now.astimezone(LONDON)
    for label, path, period, earliest in MAC_JOBS:
        p = os.path.expanduser(path)
        try:
            last = open(p).read().strip()
        except OSError:
            last = ""
        want = nl.date().isoformat() if period == "day" else \
            f"{nl.isocalendar()[0]}-W{nl.isocalendar()[1]:02d}"
        if nl.hour < earliest + 2:
            continue
        if last != want:
            findings["problems"].append(f"*{label}* (Mac) hasn't succeeded {'today' if period == 'day' else 'this week'} "
                                        f"(last: {last or 'never'}).")
        else:
            findings["counts"]["mac_ok"] = findings["counts"].get("mac_ok", 0) + 1


# --------------------------------------------------------------------------- report
def collect(extra: dict | None = None) -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    f = {"problems": [], "watch": [], "info": [], "counts": {},
         "generated": now.astimezone(LONDON).strftime("%a %d %b %H:%M")}
    for fn in (lambda: check_render(since, now, f), lambda: check_outcomes(now, f),
               lambda: check_mac(now, f)):
        try:
            fn()
        except Exception as exc:
            f["problems"].append(f"Chief of Staff couldn't finish a check: `{str(exc)[:150]}`")
    if extra:
        f["problems"] += extra.get("problems", [])
        f["watch"] += extra.get("watch", [])
        f["info"] += extra.get("info", [])
        for k, v in (extra.get("counts") or {}).items():
            f["counts"][k] = v
    return f


def render_text(f: dict) -> str:
    c = f["counts"]
    head = f":compass: *Chief of Staff — {f['generated']}* (last 24h)"
    lines = [head]
    if not f["problems"]:
        lines.append(":white_check_mark: *Everything ran and did its job.*")
    else:
        lines.append(f":warning: *{len(f['problems'])} thing(s) need you:*")
        lines += [f"• {p}" for p in f["problems"]]
    if f["watch"]:
        lines.append("\n:eyes: *Worth watching:*")
        lines += [f"• {w}" for w in f["watch"]]
    summary = []
    if "render_runs_ok" in c:
        summary.append(f"{c['render_runs_ok']} cloud job runs OK")
    if "bot_runs_ok" in c:
        summary.append(f"{c['bot_runs_ok']:,} bot runs OK")
    if "leads_24h" in c:
        summary.append(f"{c['leads_24h']} new lead(s), {c.get('leads_texted_24h', 0)} texted")
    if "claude_tasks_ok" in c:
        summary.append(f"{c['claude_tasks_ok']} Claude tasks/routines OK")
    if "mac_ok" in c:
        summary.append(f"{c['mac_ok']} Mac jobs OK")
    if summary:
        lines.append("\n:bar_chart: " + " · ".join(summary))
    lines += [f"_{i}_" for i in f["info"]]
    return "\n".join(lines)


def post(text: str) -> None:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
    import config
    import slack_notifier
    slack_notifier._send_dm(config.CEO_SLACK_EMAIL, text, target_label="chief of staff")


def marker_sheet():
    import speed_to_lead as stl
    return stl.state_ws()


def posted_today() -> bool:
    """Tonight's report already went? Marker row on the Speed to Lead tab
    (shared by the Mac task and the Render backup)."""
    import speed_to_lead as stl
    st = stl.read_state(stl.state_ws()).get("__chief_of_staff__")
    today = datetime.now(LONDON).date().isoformat()
    return bool(st and st["Seen At"].startswith(today))


def mark_posted() -> None:
    import speed_to_lead as stl
    ws = stl.state_ws()
    st = stl.read_state(ws).get("__chief_of_staff__")
    now = stl._iso(datetime.now(LONDON))
    if st:
        stl.update_cells(ws, st["_row"], {"Seen At": now})
    else:
        stl.append_row(ws, {"Lead Key": "__chief_of_staff__", "Seen At": now,
                            "Name": "(chief of staff marker)", "Closed": "meta"})


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
    extra = None
    if "--extra" in sys.argv:
        with open(sys.argv[sys.argv.index("--extra") + 1]) as fh:
            extra = json.load(fh)
    if "--backup" in sys.argv:
        if posted_today():
            print("chief of staff: tonight's report already posted — backup not needed")
            return
        f = collect()
        f["info"].append("Backup report from Render — the 21:10 Mac report didn't run "
                         "(Mac asleep/off or Claude app closed), so Claude tasks and Mac jobs aren't covered.")
        post(render_text(f))
        mark_posted()
        print("chief of staff: backup report posted")
        return
    f = collect(extra)
    if "--json" in sys.argv:
        print(json.dumps(f, indent=1))
        return
    text = render_text(f)
    print(text)
    if "--post" in sys.argv:
        post(text)
        mark_posted()
        print("\n(posted to Slack)")


if __name__ == "__main__":
    main()
