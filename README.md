# Elite Physiotherapy — Patient Drop-off Automation

A fully automated system that captures patient drop-offs (cancellations, no-shows, no-rebooks) from Cliniko each morning, enriches them with AI body-area categorisation, populates a central Google Sheet, sends Slack notifications to physios + reception + ops manager, and lets physios classify each drop-off via interactive Slack buttons.

**Replaces** Sinead's 2–3 hour Monday task of manually pulling drop-offs from Cliniko, plus same-day reactivation calls instead of the following week.

## Architecture

```
                ┌─────────────────┐
                │  macOS launchd  │  ← daily cron at 07:00
                │  (run_daily.sh) │
                └────────┬────────┘
                         │
                         ▼
                ┌────────────────────────────────┐
                │  phase1_fetch.py --write       │
                │                                │
                │  1. Cliniko API → drop-offs    │
                │     (Phase 1 logic)            │
                │  2. AI body-area + session #   │
                │     (Phase 2, via Claude API)  │
                │  3. Write rows to Google Sheet │
                │  4. Refresh summary tabs       │
                │     (Phase 2.5)                │
                │  5. Slack DMs to team          │
                │     (Phase 3 → slack_notifier) │
                └────────┬───────────────────────┘
                         │
                         ▼
                ┌────────────────────────────────────────┐
                │  Google Sheet                          │
                │  "Elite Physio — Patient Drop-Off"     │
                │                                        │
                │  • W/C YYYY-MM-DD tabs (one per week)  │
                │  • IA Rebook Rate                      │
                │  • Monthly Summary                     │
                │  • Weekly Snapshot                     │
                │  • Performance Dashboard               │
                └────────────────────────────────────────┘

         ┌──── Physio receives DM with [Clinical] [Non-clinical] buttons
         │
         ▼
   ┌────────────────────────────────────────┐
   │  Render-hosted Flask app (server.py)   │  ← Phase 4
   │  elite-dropoff-form.onrender.com       │
   │                                        │
   │  Receives button click → updates       │
   │  Sheet → DMs reception if non-clinical │
   │  → posts updated message back to Slack │
   └────────────────────────────────────────┘
```

## File map

### Production scripts (the daily cron)

| File | Role |
|------|------|
| `phase1_fetch.py` | Main entry point. CLI: `--write` (commit to Sheet), `--no-phase2` (skip AI step), `--date YYYY-MM-DD` (override "yesterday"). Drives the daily pipeline. |
| `phase2.py` | Cliniko API client, episode detection, IA Rebook Rate, weekly stats, monthly per-physio stats, Claude API body-area categorisation. |
| `slack_notifier.py` | Composes and sends three message types (physio DMs with interactive buttons, reception/Sinéad list, ops manager summary). |
| `backfill.py` | One-off tool — pulls historical drop-offs for a date range and writes them into the Sheet. Usage: `python backfill.py YYYY-MM-DD YYYY-MM-DD`. |
| `run_daily.sh` | Wrapper script that launchd runs at 07:00. Retries once on failure. Logs to `logs/YYYY-MM-DD.log`. |

### Interactive button server (deployed to Render)

| File | Role |
|------|------|
| `server.py` | Flask app at `/slack/interactive` that receives Slack button clicks, verifies signatures, updates the Sheet, notifies reception when a drop-off is marked non-clinical. Hosted on Render Hobby. |
| `requirements.txt` | Python deps for Render build. |
| `Procfile` | Render start command (gunicorn). |
| `runtime.txt` | Python version pin (3.11.9). |

### Configuration

| File | Role |
|------|------|
| `config.py` | All editable settings — IA appointment-type IDs, classes to exclude, per-physio hours, Slack email mappings, gold-standard KPI thresholds, `SLACK_SAFE_MODE`. **Edit this file when team or clinic config changes.** Restart not required — next cron picks it up. |
| `.env` | Secrets — Cliniko API key, Anthropic API key, Slack Bot Token, Slack Signing Secret. Gitignored. |
| `service_account.json` | Google service account credentials (Sheets API). Gitignored. |

### Reference

| File | Role |
|------|------|
| `Elite_DropOff_Brief.docx` / `.txt` | Original briefing document — describes the full vision in 13 sections. |

## Daily operations

### Pause/resume the cron

```bash
# Pause:
launchctl unload ~/Library/LaunchAgents/com.elitephysio.dropoff.daily.plist

# Resume:
launchctl load ~/Library/LaunchAgents/com.elitephysio.dropoff.daily.plist

# Check status:
launchctl list | grep elitephysio

# Trigger manually right now:
launchctl start com.elitephysio.dropoff.daily
```

### View logs

```bash
# Today's log:
cat ~/cliniko-dropoffs/logs/$(date +%Y-%m-%d).log

# All recent logs:
ls -la ~/cliniko-dropoffs/logs/
```

### Manual run for a specific date

```bash
cd ~/cliniko-dropoffs
./venv/bin/python phase1_fetch.py --date 2026-05-15 --write
```

### Backfill a date range

```bash
./venv/bin/python backfill.py 2026-04-01 2026-04-30
```

### Pause Slack notifications without pausing the cron

Edit `config.py` → set `SLACK_SAFE_MODE = True` → next cron run, all DMs go to Marty's inbox only (prefixed with `[TEST]`).

## Adding/removing a physio

Edit `config.py`:

1. **`PRACTITIONER_DISPLAY_NAME`** — add the new Cliniko full name → display name mapping (and any `X CS` variant).
2. **`PRACTITIONER_DISPLAY_ORDER`** — add display name in the position you want them to appear on the Performance Dashboard.
3. **`PHYSIO_MONTHLY_HOURS`** — their monthly available hours.
4. **`PHYSIO_SLACK_EMAIL`** — their clinic email for Slack DM lookup.
5. If they should be excluded from the "w/o M&J" team-average row, add to **`EXCLUDE_FROM_MAIN_TEAM`**.

That's it. Next cron picks them up.

## Changing KPI thresholds

Edit `config.py` → `STANDARDS` dict. The Performance Dashboard's conditional formatting uses these to colour cells green/yellow/red.

## Render — interactive button server

Hosted at `https://elite-dropoff-form.onrender.com`. Auto-deploys from this repo's `main` branch.

Required environment variables (set in Render dashboard → Environment):
- `SLACK_BOT_TOKEN` — `xoxb-...` from Slack app's OAuth page
- `SLACK_SIGNING_SECRET` — from Slack app's Basic Information page
- `SPREADSHEET_ID` — the Google Sheet ID (without `/edit` etc.)
- `SERVICE_ACCOUNT_JSON` — full JSON content of the service account credentials
- `RECEPTION_NOTIFY_EMAILS` (optional) — comma-separated emails to notify on non-clinical clicks. Defaults to reception@ + sinead@.

## Tabs in the Google Sheet

| Tab | Purpose | Refreshed |
|-----|---------|-----------|
| `W/C DD Mon YYYY` | One per week. Patient-level detail of all drop-off events that week. New rows append each morning. | Daily (new rows) |
| `Weekly Snapshot` | Clinic-wide weekly KPIs (IAs, Rebook %, CNAs, DNAs, etc.) — last 4 completed weeks. | Daily |
| `Monthly Summary` | Per-physio drop-off counts by type for each month with data. | Daily |
| `IA Rebook Rate` | MTD + last 3 settled months, per physio + clinic-wide. | Daily |
| `Performance Dashboard` | Full per-physio monthly tracker matching Marty's manual sheet — Utilization, NPs, DNA%, CNA%, PVA, etc. Colour-coded vs. gold standards. | Daily |

## Cost

| Service | Monthly cost |
|---------|-------------:|
| Render Hobby (Flask server) | $7 |
| Anthropic Claude API (body-area AI) | <$1 |
| Cliniko, Google Sheets, Slack | $0 |
| **Total** | **~$8/month** |

## Built phases

1. **Phase 1** — Daily Cliniko fetch + Google Sheet writing
2. **Phase 2** — Session count, body-area AI categorisation, IA Rebook Rate
3. **Phase 2.5** — Weekly Snapshot + monthly Performance Dashboard
4. **Phase 3** — Slack notifications (physio DMs, reception, ops manager summary)
5. **Phase 4** — Interactive Slack buttons via Flask server on Render
6. **Phase 5** — macOS launchd daily 7am cron

## GDPR notes

- Patient data does NOT pass through any AI model during routine operation, **except** for body-area categorisation in Phase 2.
- Phase 2 sends *only* SOAP-style clinical notes (de-identified — no patient name, DOB, address, phone) to Claude. Body area output goes into the Sheet.
- All other phases are pure data movement (Cliniko ↔ Sheet ↔ Slack), no AI.
- All data stays within Elite Physiotherapy's existing tools (Cliniko, Google Workspace, Slack).
