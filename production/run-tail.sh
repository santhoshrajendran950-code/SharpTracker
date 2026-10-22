#!/bin/bash
# Kch-Temp football tail (tail.py, rules in tail.json): every 10 min 09:07-23:57.
# New or upsized recs -> Novig PAPER order + Mac notification + email to Santhosh only;
# 1u recs (x_min_units) also go to X under the codename with a blurred stake.
# PAPER ONLY: tail.py --paper pins NOVIG_HOST to the paper exchange.
# tails.jsonl is PRIVATE (same footing as bets.jsonl): never synced, never sent.
cd "$(dirname "$0")" || exit 1
EMAIL_TO="YOUR_EMAIL@example.com"
. "$(dirname "$0")/sendmail.sh"
LOG=tail.log
FAIL_STAMP=".tail-fail-emailed"
HOUR=$(date '+%-H')
[ "$HOUR" -lt 9 ] && exit 0      # launchd fires every 10 min; active 09:00-23:59

send_email() {   # $1 subject, $2 body file
  send_mail "$1" "$2" "$EMAIL_TO"
}

OUT=$(/usr/bin/python3 tail.py --quiet --paper --x 2>tail.err)
CODE=$?
if [ $CODE -ne 0 ]; then
  echo "[$(date '+%F %T %Z')] tail failed (exit $CODE): $(tail -1 tail.err)" >> "$LOG"
  LAST=$(cat "$FAIL_STAMP" 2>/dev/null || echo 0)
  if [ $(( $(date +%s) - LAST )) -ge 10800 ]; then   # at most one failure email per 3h
    { echo "The Kch-Temp tail FAILED at $(date '+%F %T %Z') (exit $CODE)."
      echo "Until this clears, no email does NOT mean no new play."; echo; cat tail.err; } > tail-fail-body.txt
    send_email "Tail — scan FAILED" tail-fail-body.txt
    date +%s > "$FAIL_STAMP"
  fi
  exit 0
fi
rm -f "$FAIL_STAMP"
[ -z "$OUT" ] && exit 0

printf '%s\n' "$OUT" > tail-body.txt
{ echo "=== FIRED $(date '+%F %T %Z') ==="; echo "$OUT"; } >> "$LOG"
TOP=$(grep -m1 '^NEW ' tail-body.txt | sed 's/^NEW //' | cut -d'|' -f1-2 | sed 's/ *$//')
/usr/bin/osascript -e "display notification \"$TOP\" with title \"Kch-Temp tail\" sound name \"Glass\"" 2>/dev/null
send_email "Kch-Temp tail — $TOP" tail-body.txt
echo "[$(date '+%F %T %Z')] emailed $EMAIL_TO" >> "$LOG"
exit 0
