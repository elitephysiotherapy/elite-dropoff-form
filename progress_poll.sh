#!/bin/bash
# Weekly off-track / progress review — runs every Monday morning via launchd.
# Scans the previous completed Mon-Sun week of follow-up notes and refreshes the
# "Off-Track Review" tab of the drop-off master sheet.

LOG_DIR="/Users/martinloughran/cliniko-dropoffs/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/progress-$(date +%Y-%m-%d).log"

cd /Users/martinloughran/cliniko-dropoffs || exit 1

{
    echo ""
    echo "--- off-track scan $(date '+%Y-%m-%d %H:%M:%S %Z') ---"
    ./venv/bin/python progress_scan.py
} >> "$LOG_FILE" 2>&1
