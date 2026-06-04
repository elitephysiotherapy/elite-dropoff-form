#!/bin/bash
# End-of-day stats report — posts the stats table to the #eod-claude Slack
# channel 15 minutes before each admin shift ends. Scheduled by launchd
# (com.elitephysio.eod.plist): 12:45 / 16:15 / 20:45 Mon-Thu, 15:45 Fri.

LOG_DIR="/Users/martinloughran/cliniko-dropoffs/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/eod-$(date +%Y-%m-%d).log"

cd /Users/martinloughran/cliniko-dropoffs || exit 1

{
    echo ""
    echo "--- eod stats $(date '+%Y-%m-%d %H:%M:%S %Z') ---"

    # If the Mac was asleep at the scheduled time, launchd runs this job the
    # instant it wakes — often before WiFi/DNS has reconnected. Retry with
    # backoff (30/60/90/120s) so a not-yet-ready network self-heals. The
    # script only posts to Slack as its final step, so a retry never
    # double-posts.
    attempt=1
    max=5
    while [ "$attempt" -le "$max" ]; do
        if ./venv/bin/python eod_stats.py --post; then
            break
        fi
        if [ "$attempt" -lt "$max" ]; then
            wait=$((attempt * 30))
            echo "  attempt $attempt failed — retrying in ${wait}s…"
            sleep "$wait"
        else
            echo "  all $max attempts failed — giving up until the next run."
        fi
        attempt=$((attempt + 1))
    done
} >> "$LOG_FILE" 2>&1
