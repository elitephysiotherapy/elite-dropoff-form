#!/bin/bash
# One-off scheduled run of an Omagh campaign wave, fired by launchd.
#   omagh_wave_run.sh <wave_size> <launchd_label> <wave_name>
#
# Unattended, so it does three things a manual run does not need:
#   - refuses to send outside 09:00-17:00, because launchd runs a MISSED job
#     when the Mac next WAKES; without this a laptop opened at 2am would text
#     patients at 2am
#   - posts the outcome to Slack, so a silent failure is not invisible. This
#     earned its keep on 25 Aug: the job ran on time and sent nothing because
#     ~/Downloads is TCC-protected from launchd, and only the alert revealed it
#   - removes its own launchd job, so it can never fire twice
set -uo pipefail
SIZE="${1:-100}"
LABEL="${2:-com.elitephysio.omagh-wave}"
NAME="${3:-wave}"

cd /Users/martinloughran/cliniko-dropoffs
mkdir -p logs
LOG="logs/omagh_${NAME}_$(date +%Y%m%d_%H%M%S).log"

HOUR=$(date +%H)
if [ "$HOUR" -lt 9 ] || [ "$HOUR" -ge 17 ]; then
  ./venv/bin/python omagh_notify.py skipped "$(date '+%a %d %b %H:%M')" "$NAME" | tee -a "$LOG"
else
  MARKETING_SAFE_MODE=false ./venv/bin/python omagh_send.py --wave "$SIZE" --send 2>&1 | tee -a "$LOG"
  ./venv/bin/python omagh_notify.py sent "$LOG" "$NAME" 2>&1 | tee -a "$LOG"
fi

# One-off: retire the job whatever happened, so it cannot fire again.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "launchd job $LABEL removed" >> "$LOG"
