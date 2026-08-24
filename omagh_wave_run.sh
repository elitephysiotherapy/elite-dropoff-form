#!/bin/bash
# One-off scheduled run of an Omagh campaign wave, fired by launchd.
#
# Unattended, so it does three things a manual run does not need:
#   - refuses to send outside 09:00-17:00, because launchd runs a missed job
#     when the Mac next WAKES; without this a laptop opened at 2am would text
#     100 patients at 2am
#   - posts the outcome to Slack, so a silent failure is not invisible
#   - removes its own launchd job, so it can never fire twice
set -uo pipefail
cd /Users/martinloughran/cliniko-dropoffs
mkdir -p logs
LOG="logs/omagh_wave_$(date +%Y%m%d_%H%M%S).log"
LABEL="com.elitephysio.omagh-wave2"

HOUR=$(date +%H)
if [ "$HOUR" -lt 9 ] || [ "$HOUR" -ge 17 ]; then
  ./venv/bin/python omagh_notify.py skipped "$(date '+%a %d %b %H:%M')" | tee -a "$LOG"
else
  MARKETING_SAFE_MODE=false ./venv/bin/python omagh_send.py --wave 100 --send 2>&1 | tee -a "$LOG"
  ./venv/bin/python omagh_notify.py sent "$LOG" 2>&1 | tee -a "$LOG"
fi

# One-off: retire the job whatever happened, so it cannot fire again.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "launchd job removed" >> "$LOG"
