#!/bin/bash
# Is the public pipeline alive? Every 15 min, 09:00-24:00.
#   * picks.log must have a line newer than 30 min (the pick job runs every 10)
#   * the Mac must be on AC power (it hibernated at 1% battery on 2026-09-30 and
#     everything was dead for 13 hours)
# On a problem: macOS banner + ONE email per problem per 3 hours (not a storm).
# Dead-man's switch: this script cannot report its own host dying, so every run
# pings an outside monitor (healthchecks.io or similar; URL from env
# HEALTHCHECK_URL or Keychain "nightly-line-healthcheck"). A clean run pings the
# URL, a problem run pings URL/fail, and the monitor emails when pings stop.
cd "$(dirname "$0")" || exit 1
LOG=watchdog.log
H=$(date +%H); [ "$H" -ge 9 ] || exit 0   # monitor schedule should allow the overnight gap
EMAIL_TO="YOUR_EMAIL@example.com"
. "$(dirname "$0")/sendmail.sh"
STAMP=/tmp/.nightly-watchdog-last-mail
PROBLEMS=""
HC_URL="${HEALTHCHECK_URL:-$(security find-generic-password -s nightly-line-healthcheck -w 2>/dev/null)}"
ping_hc() { [ -n "$HC_URL" ] && curl -fsS -m 10 --retry 2 "$HC_URL$1" >/dev/null 2>&1; }

# 1. pick job heartbeat
LAST=$(grep -o '^\[2026-[0-9-]* [0-9:]*' picks.log | tail -1 | tr -d '[')
if [ -n "$LAST" ]; then
  AGE=$(( $(date +%s) - $(date -j -f '%Y-%m-%d %H:%M:%S' "$LAST" +%s) ))
  [ $AGE -gt 1800 ] && PROBLEMS="$PROBLEMS\n- pick job silent for $((AGE/60)) min (last: $LAST)"
fi
launchctl list | grep -q com.nightlyline.picks || PROBLEMS="$PROBLEMS\n- com.nightlyline.picks is NOT loaded in launchd"

# 2. mail: any send that failed after retries in the last 3 hours (deploy/mailer.py)
if [ -f mail-failed.jsonl ] && [ $(( $(date +%s) - $(stat -f %m mail-failed.jsonl) )) -lt 10800 ]; then
  PROBLEMS="$PROBLEMS\n- email send FAILED: $(tail -1 mail-failed.jsonl | cut -c1-200)"
fi

# 3. power
pmset -g batt | grep -q "AC Power" || PROBLEMS="$PROBLEMS\n- running on BATTERY ($(pmset -g batt | grep -o '[0-9]*%')) — plug the Mac in"

[ -z "$PROBLEMS" ] && { ping_hc ""; exit 0; }
ping_hc "/fail"
MSG=$(printf '%b' "$PROBLEMS" | sed '/^$/d' | tr '\n' ' ')
echo "[$(date '+%F %T %Z')] $MSG" >> "$LOG"
/usr/bin/osascript -e "display notification \"$MSG\" with title \"Nightly Line WATCHDOG\" sound name \"Basso\"" 2>/dev/null

# rate-limit the email to one per 3 hours
if [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -f %m "$STAMP") )) -lt 10800 ]; then exit 0; fi
{ echo "Nightly Line watchdog — $(date '+%a %b %d %-I:%M %p')"; echo; echo "Problems:"; printf '%b\n' "$PROBLEMS"; echo; echo "Last 5 picks.log lines:"; tail -5 picks.log; } > watchdog-body.txt
send_mail "Nightly Line WATCHDOG — pipeline problem" watchdog-body.txt "$EMAIL_TO"
touch "$STAMP"
exit 0
