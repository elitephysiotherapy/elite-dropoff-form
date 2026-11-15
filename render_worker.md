# Render setup: `elite-bots` Background Worker (2026-11-15)

Speed-to-lead, the team-sheet sync, the daily comms check and the campaign
sender now run in their own Background Worker (`bot_worker.py`). They no
longer run inside the web service `elite-dropoff-form`.

Why: gunicorn forked its web worker from the master process that was running
the bot threads. The forked worker inherited their `/tmp/*.lock` files and
held the locks forever, so every bot tick silently skipped from 8 to 14 Nov
2026. In the new worker each bot run is a fresh process with a hard time
limit, and a bot that can't get its lock now logs that it skipped.

## 1. Create the worker

- **Type:** Background Worker (Starter plan)
- **Repo/branch:** the same repo as `elite-dropoff-form`, branch `main`
- **Region:** Frankfurt
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python -u bot_worker.py`
- Python comes from `.python-version` (3.14.3, what Render already ran). Render ignores `runtime.txt`.

## 2. Env vars

Copy these from `elite-dropoff-form`. Names are listed here, values are not:

`CAMPAIGN_MODE`, `CLINIKO_API_KEY`, `CLINIKO_SHARD`, `CLINIKO_USER_AGENT`,
`SERVICE_ACCOUNT_JSON`, `SLACK_BOT_TOKEN`, `SPREADSHEET_ID`, `STL_CHANNEL`,
`STL_LIVE`, `STL_START_AT`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`SLACK_SIGNING_SECRET`

⚠️ **`SLACK_SIGNING_SECRET` is required.** Campaign unsubscribe links are
signed with it. The worker writes the links and the web service checks them,
so both services must have the same value. Without it, unsubscribe links in
campaign texts and emails stop working.

The web service doesn't currently have `RESEND_API_KEY` or
`CAMPAIGN_PILATES_LINK`. If campaign emails or vouchers are meant to send,
set them on the worker.

Optional:
- `BOT_STATE_DIR` (default `/tmp/elite-bots`)
- `BOT_WORKER_STARTUP_DELAY` (default 90 s)
- The per-bot switches `STL_ENABLED`, `TEAM_SHEET_SYNC`, `COMMS_CHECK_ENABLED`

## 3. Order of switch-over (avoid double texts)

1. Push the code. The web service redeploys with **no** bot threads and
   logs "bot threads are not run in the web server".
2. Wait until that deploy is live and the old instance has stopped (~5 min).
3. Create the worker (steps 1–2). It waits 90 s before its first run. The
   campaign also waits its usual 15-min grace from the worker start.
4. Check the worker logs. You should see
   `[bot_worker] started: speed_to_lead/60s(limit 50s), …`, and later
   `comms check: …` at 07:15.

The web service keeps every web route: Slack buttons (including the
speed-to-lead **Called** button), Twilio replies and calls, and campaign
unsubscribe.

There's no "threads in the web server" fallback any more, because it would
bring the bug back. If the worker is ever down, restart or redeploy the
worker itself.
