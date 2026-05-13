#!/bin/bash
# Daily wrapper for Elite drop-off automation.
# Called by launchd at 7am every day. Logs to ~/cliniko-dropoffs/logs/YYYY-MM-DD.log.
# Retries once on failure (covers transient wifi/network blips at startup).

LOG_DIR="/Users/martinloughran/cliniko-dropoffs/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

cd /Users/martinloughran/cliniko-dropoffs || exit 1

run_once() {
    echo "" >> "$LOG_FILE"
    echo "------------------------------------------" >> "$LOG_FILE"
    echo "Attempt at $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
    echo "------------------------------------------" >> "$LOG_FILE"
    ./venv/bin/python phase1_fetch.py --write >> "$LOG_FILE" 2>&1
}

{
    echo ""
    echo "=========================================="
    echo "Daily run started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "=========================================="
} >> "$LOG_FILE"

run_once
RC=$?

# Retry once after a 2-minute wait — transient network failures (wifi just woken
# from sleep, DNS hiccup, Cliniko rate-limit overflow) almost always clear quickly.
if [ $RC -ne 0 ]; then
    echo "" >> "$LOG_FILE"
    echo ">>> First attempt failed (exit $RC). Waiting 120s then retrying once. <<<" >> "$LOG_FILE"
    sleep 120
    run_once
    RC=$?
fi

{
    echo ""
    echo "=========================================="
    echo "Daily run finished: $(date '+%Y-%m-%d %H:%M:%S %Z')  (final exit: $RC)"
    echo "=========================================="
} >> "$LOG_FILE"

exit $RC
