#!/bin/bash
# Weekend football slate boards — 30 and 15 minutes before each kickoff wave.
#
#   Saturday CFB   12:00, 3:30, 6:00, 8:30 pm  -> boards at :30 and :15 before
#   Sunday   NFL    1:00, 4:00, 8:00 pm        -> boards at :30 and :15 before
#   Weekdays       one full board at 5:30 pm, ahead of the evening slate
#
# This is now the ONLY place the whole board is pushed. alert.sh used to append
# it under every alert, which buried the one new play it was reporting and cost
# a second ~55s sweep each time it fired.
#
# College and pro football kick off in waves rather than trickling out like the
# baseball slate, so a board an hour out is stale and one at first pitch is too
# late. Two reads per wave catches money arriving in the last half hour, which
# is when the Phillies and Cardinals positions moved on 2026-09-15.
#
# The board is filtered to the league that is actually kicking off — bigboard.py
# takes league arguments, and dropping from 8 tags to 1 is most of the runtime.
#
# Posts to Discord #big-board. Silent no-op if no webhook is configured.
cd "$(dirname "$0")" || exit 1
LOG=slateboard.log
DOW=$(date '+%u')                      # 1-5 = Mon-Fri, 6 = Sat, 7 = Sun
case "$DOW" in
  6) LEAGUE=cfb;  LABEL="CFB"  ;;
  7) LEAGUE=nfl;  LABEL="NFL"  ;;
  *) LEAGUE="";   LABEL="BOARD" ;;     # weekday: every league, no filter
esac

# Which wave is this run in front of, and how far out?
read -r WAVE MINS <<EOF
$(/usr/bin/python3 - "$LEAGUE" <<'PY'
import sys
from datetime import datetime, timedelta
# Weekday runs pass an empty league and have no wave structure — .get(), not
# [], so the weekday path does not raise before the board is even built.
waves = {"cfb": ["12:00", "15:30", "18:00", "20:30"],
         "nfl": ["13:00", "16:00", "20:00"]}.get(sys.argv[1] if len(sys.argv) > 1 else "", [])
now = datetime.now()
best, mins = "", 999
for w in waves:
    h, m = map(int, w.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    d = (t - now).total_seconds() / 60
    if -5 <= d < mins:
        best, mins = t.strftime("%-I:%M %p"), d
print(best or "evening", max(int(round(mins)), 0) if best else 0)
PY
)
EOF

# 60+ only. The terminal board is deliberately permissive — you can scan a
# sub-40 row and ignore it — but a channel post is pushed at people, and a 37
# sitting under a 100 reads as though the system endorses both.
MIN_SCORE=65
if [ -n "$LEAGUE" ]; then
  OUT=$(/usr/bin/python3 bigboard.py "$LEAGUE" --min-score "$MIN_SCORE" 2>&1); CODE=$?
else
  OUT=$(/usr/bin/python3 bigboard.py --min-score "$MIN_SCORE" 2>&1); CODE=$?
fi
if [ $CODE -ne 0 ]; then
  # Most common cause is the sweep lock: weekend boards are 15 minutes apart and
  # a long run can still hold it. Wait it out once rather than dropping the slot
  # silently — a missed board looks identical to a quiet slate.
  echo "[$(date '+%F %T %Z')] bigboard $LEAGUE exit $CODE: $(printf '%s' "$OUT" | head -1) — retrying in 90s" >> "$LOG"
  sleep 90
  if [ -n "$LEAGUE" ]; then
    OUT=$(/usr/bin/python3 bigboard.py "$LEAGUE" --min-score "$MIN_SCORE" 2>&1); CODE=$?
  else
    OUT=$(/usr/bin/python3 bigboard.py --min-score "$MIN_SCORE" 2>&1); CODE=$?
  fi
fi
if [ $CODE -ne 0 ]; then
  echo "[$(date '+%F %T %Z')] !! bigboard $LEAGUE FAILED after retry (exit $CODE) — no board posted" >> "$LOG"
  exit 0
fi
case "$OUT" in
  "BIG BOARD"*) ;;
  *) echo "[$(date '+%F %T %Z')] output did not look like a board — NOT posted: $(printf '%s' "$OUT" | head -1)" >> "$LOG"
     exit 0 ;;
esac

printf '%s\n' "$OUT" > /tmp/.slateboard.txt
if [ "$LABEL" = "BOARD" ]; then
  TITLE="Big Board — evening slate (${MIN_SCORE}+)"
else
  TITLE="$LABEL board ${MIN_SCORE}+ — T-${MINS}m to the $WAVE wave"
fi
if ! /usr/bin/python3 discordsink.py --channel board --if-configured \
     --title "$TITLE" /tmp/.slateboard.txt >> "$LOG" 2>&1; then
  echo "[$(date '+%F %T %Z')] !! DISCORD SEND FAILED for #big-board" >> "$LOG"
  /usr/bin/osascript -e 'display notification "Discord #big-board send FAILED" with title "Nightly Line" sound name "Basso"' 2>/dev/null
fi
if [ "$LABEL" = "BOARD" ]; then
  echo "[$(date '+%F %T %Z')] weekday board posted" >> "$LOG"
else
  echo "[$(date '+%F %T %Z')] $LABEL board posted (T-${MINS}m, $WAVE wave)" >> "$LOG"
fi
rm -f /tmp/.slateboard.txt
exit 0
