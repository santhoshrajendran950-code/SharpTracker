#!/bin/bash
# Pre-slate (T-60 before the day's first game) and closing (T-15 per game)
# alerts — to Santhosh ONLY, no BCC. Separate from the shared sharp alerts.
#
# windows.py does a cheap gamma-only check first and exits unless a window is
# open, so most runs cost almost nothing. It queues any emails to
# windows-outbox.json; this script just sends what's in the queue.
cd "$(dirname "$0")" || exit 1
EMAIL_TO="YOUR_EMAIL@example.com"     # deliberately no BCC
. "$(dirname "$0")/sendmail.sh"
LOG=windows.log
OUTBOX=windows-outbox.json

rm -f "$OUTBOX"
OUT=$(/usr/bin/python3 windows.py 2>&1); CODE=$?
if [ $CODE -ne 0 ]; then
  echo "[$(date '+%F %T %Z')] windows.py failed (exit $CODE): $(echo "$OUT" | head -1)" >> "$LOG"
  exit $CODE
fi
[ -s "$OUTBOX" ] || exit 0
N=$(/usr/bin/python3 -c "import json;print(len(json.load(open('$OUTBOX'))))")
[ "$N" -gt 0 ] || exit 0

for i in $(seq 0 $((N-1))); do
  SUBJ=$(/usr/bin/python3 -c "import json;print(json.load(open('$OUTBOX'))[$i]['subject'])")
  /usr/bin/python3 -c "import json;open('/tmp/.windows-body.txt','w').write(json.load(open('$OUTBOX'))[$i]['body'])"
  send_mail "$SUBJ" /tmp/.windows-body.txt "$EMAIL_TO"   # deliberately no BCC
  echo "[$(date '+%F %T %Z')] sent '$SUBJ'" >> "$LOG"
done
# Deliberately NOT posted to Discord. windows.py queues a CLOSING LOOK fifteen
# minutes before EVERY game plus change alerts until first pitch — on a 15-game
# MLB night that is 15+ messages into #big-board, which is not a board, it is a
# firehose. #big-board carries only the scheduled boards from run-slateboard.sh
# (weekdays 5:30pm, weekend football waves). These window alerts stay email-only,
# to Santhosh alone, exactly as they were before Discord existed.

exit 0
