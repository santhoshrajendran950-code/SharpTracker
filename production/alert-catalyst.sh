#!/bin/bash
# Catalyst watcher: emails when Economics specialists put NEW money into a US
# economic-release market (Fed, CPI, jobs, PCE, PPI, GDP, ISM) since the last run.
#
# catalyst.py --alert prints nothing unless a view fired, so "output is non-empty"
# is the trigger. State lives in catalyst-alert-snapshot.json, separate from manual
# `catalyst.py` board runs, so looking by hand never swallows an alert.

cd "$(dirname "$0")" || exit 1

EMAIL_TO="YOUR_EMAIL@example.com"
ACTIVE_FROM=7          # local hour; releases print 8:30am ET, FOMC 2pm ET
ACTIVE_TO=23
. "$(dirname "$0")/sendmail.sh"
LOG="alert-catalyst.log"
FAIL_STAMP=".catalyst-fail-emailed"
FAIL_EMAIL_EVERY=10800 # at most one failure email per 3h

HOUR=$(date '+%-H')
if [ "$HOUR" -lt "$ACTIVE_FROM" ] || [ "$HOUR" -ge "$ACTIVE_TO" ]; then
  exit 0
fi

# The sports alert (check.py) sweeps the same positions API on its own 15-minute
# timer without a lock. Two sweeps at once trip the throttle — wait it out.
for _ in $(seq 1 30); do
  pgrep -f "check.py --alert|bigboard.py|macro.py" >/dev/null || break
  sleep 5
done

send_email() {   # $1 subject, $2 body file
  send_mail "$1" "$2" "$EMAIL_TO"
}

OUT=$(/usr/bin/python3 catalyst.py --alert 2>catalyst-alert.err)
CODE=$?

if [ $CODE -eq 4 ]; then
  echo "[$(date '+%F %T %Z')] another sweep holds the lock — skipped" >> "$LOG"
  exit 0
fi

if [ $CODE -ne 0 ]; then
  ERR=$(head -1 catalyst-alert.err)
  echo "[$(date '+%F %T %Z')] catalyst failed (exit $CODE): $ERR" >> "$LOG"
  /usr/bin/osascript -e "display notification \"catalyst scan failed (exit $CODE)\" with title \"Catalyst Alert\"" 2>/dev/null
  # silence must mean "ran clean" — so failures email, throttled
  LAST=$(cat "$FAIL_STAMP" 2>/dev/null || echo 0)
  if [ $(( $(date +%s) - LAST )) -ge $FAIL_EMAIL_EVERY ]; then
    { echo "The catalyst scan FAILED at $(date '+%F %T %Z') (exit $CODE)."
      echo "Until this clears, no email does NOT mean no new money."
      echo; cat catalyst-alert.err; } > catalyst-fail-body.txt
    send_email "Catalyst Alert — scan FAILED" catalyst-fail-body.txt
    date +%s > "$FAIL_STAMP"
  fi
  exit $CODE
fi
rm -f "$FAIL_STAMP"

if [ -z "$OUT" ]; then
  echo "[$(date '+%F %T %Z')] nothing new" >> "$LOG"
  exit 0
fi

printf '%s\n' "$OUT" > catalyst-alert-body.txt
COUNT=$(head -1 catalyst-alert-body.txt | awk '{print $1}')
TOP=$(grep -m1 '^== ' catalyst-alert-body.txt | sed 's/^== //; s/  ·.*//')
ADD=$(grep -m1 '^   +\$' catalyst-alert-body.txt | sed 's/^ *//')

{
  echo "===================================================================="
  echo "FIRED at $(date '+%F %T %Z')"
  echo "$OUT"
} >> "$LOG"

/usr/bin/osascript -e "display notification \"$ADD\" with title \"Catalyst · $TOP\" sound name \"Glass\"" 2>/dev/null
send_email "Catalyst Alert — $TOP: $ADD" catalyst-alert-body.txt
echo "[$(date '+%F %T %Z')] emailed $COUNT view(s) to $EMAIL_TO" >> "$LOG"
exit 0
