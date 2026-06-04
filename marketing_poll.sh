#!/bin/bash
# Marketing / NPS poller — runs every 10 minutes via launchd.
# One cycle per invocation. Logs to ~/cliniko-dropoffs/logs/marketing-YYYY-MM-DD.log.
#
# A failed run needs no retry: the next run is only 10 minutes away and the
# dedup ledger means nothing is ever sent twice.

LOG_DIR="/Users/martinloughran/cliniko-dropoffs/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/marketing-$(date +%Y-%m-%d).log"

cd /Users/martinloughran/cliniko-dropoffs || exit 1

{
    echo ""
    echo "--- poll $(date '+%Y-%m-%d %H:%M:%S %Z') ---"
    ./venv/bin/python -m marketing.poller
} >> "$LOG_FILE" 2>&1
