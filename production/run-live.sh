#!/bin/bash
# Live pre-game watch — Tiafoe/Shelton + Missouri/Kansas, 2026-09-11.
# Fires at 5:30pm, 6:00pm and 6:30pm local via com.nightlyline.livewatch.
#
# These two games finish tonight, so this job retires itself: after the last
# slot (or on any later date) it unloads its own launchd agent. Without that it
# would keep emailing about settled games every evening forever.

cd "$(dirname "$0")" || exit 1

EMAIL_TO="YOUR_EMAIL@example.com"
. "$(dirname "$0")/sendmail.sh"
WATCH_DATE="2026-09-11"       # the night these events are played
LAST_SLOT="1830"              # HHMM of the final scheduled run
AGENT="com.nightlyline.livewatch"
LOG="live.log"

TODAY=$(date '+%Y-%m-%d')
NOWHM=$(date '+%H%M')

retire() {
  echo "[$(date '+%F %T %Z')] retiring $AGENT — $1" >> "$LOG"
  /bin/launchctl bootout "gui/$(id -u)/$AGENT" 2>/dev/null
  /bin/rm -f "$HOME/Library/LaunchAgents/$AGENT.plist"
}

# Past the watch night entirely — retire without running or emailing.
if [ "$TODAY" != "$WATCH_DATE" ]; then
  retire "date is $TODAY, watch night was $WATCH_DATE"
  exit 0
fi

OUT=$(/usr/bin/python3 live-watch.py 2>&1)
CODE=$?

{
  echo "===================================================================="
  echo "run at $(date '+%F %T %Z')  (exit $CODE)"
  echo "$OUT"
} >> "$LOG"

if [ $CODE -ne 0 ]; then
  /usr/bin/osascript -e "display notification \"live watch failed (exit $CODE)\" with title \"Live Watch\"" 2>/dev/null
  # still email, because a silent failure before a game is worse than a noisy one
  printf '%s\n' "$OUT" > live-body.txt
fi

if [ -s live-body.txt ]; then
  SUBJ="Live Watch $(date '+%-I:%M %p') — Tiafoe/Shelton + Mizzou/Kansas"
  send_mail "$SUBJ" live-body.txt "$EMAIL_TO"
  echo "[email] sent '$SUBJ' to $EMAIL_TO" >> "$LOG"
fi

# Last scheduled slot of the night — clean up after ourselves.
if [ "$NOWHM" -ge "$LAST_SLOT" ]; then
  retire "final slot $LAST_SLOT reached"
fi

exit 0
