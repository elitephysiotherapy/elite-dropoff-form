#!/bin/bash
# ONE-SHOT EOD handover post — scheduled for 20:15 tonight on Martin's request
# (2026-05-19). Reuses the normal poll wrapper (with its retry/backoff), then
# removes its own launchd job so it never fires again.

bash /Users/martinloughran/cliniko-dropoffs/eod_poll.sh

launchctl unload ~/Library/LaunchAgents/com.elitephysio.eod.oneshot.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.elitephysio.eod.oneshot.plist
