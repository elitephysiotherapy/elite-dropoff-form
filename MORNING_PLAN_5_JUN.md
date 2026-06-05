# Morning of 5 Jun 2026 — autonomous task for Claude

If the 07:23 cron didn't fire (Claude session ended overnight), just open
Claude in this project and paste:

> Do the shortener morning plan from the 4 Jun conversation.

Full instructions are in the CronCreate prompt from last night and the
prior conversation transcript. Summary: rebuild the SMS URL shortener as
a standalone Render web service (NOT in elite-dropoff-form), starting
fresh from commit a17f8cb. Should take 30-60 min, no input from you.

Expected outcome by lunchtime: next NPS SMS goes out with a short URL
instead of the 400-char Tally URL. Twilio cost per NPS message drops
from ~£0.13 (3-4 segments) to ~£0.08 (2 segments) or lower. ~£190+/yr
saving begins immediately.

Reference: this file is safe to delete once the shortener is live.
