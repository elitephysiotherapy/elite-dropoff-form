#!/bin/bash
# New Patient Bookings trawl — runs 6x/day via launchd (06:00, 09:00, 12:00,
# 15:00, 18:00, 20:45). One trawl per invocation.
# A failed run needs no retry: the next trawl is hours away and the dedup on
# appointment_id means nothing is ever logged twice.

LOG_DIR="/Users/martinloughran/cliniko-dropoffs/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/bookings-$(date +%Y-%m-%d).log"

cd /Users/martinloughran/cliniko-dropoffs || exit 1

{
    echo ""
    echo "--- bookings trawl $(date '+%Y-%m-%d %H:%M:%S %Z') ---"
    ./venv/bin/python bookings_fetch.py --write
} >> "$LOG_FILE" 2>&1
