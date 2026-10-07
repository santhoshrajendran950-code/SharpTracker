#!/bin/bash
# Monday 3:00 PM local — publish the week that just closed, or email if it is
# not complete. Never posts a partial week: a public record that silently omits
# a day is worse than a post that arrives late.
cd "$(dirname "$0")" || exit 1
LOG=weekpost.log
echo "=== $(date '+%F %T %Z') ===" >> "$LOG"
/usr/bin/python3 weekpost.py --send >> "$LOG" 2>&1
tail -1 "$LOG" | grep -q "FAILED" && \
  /usr/bin/osascript -e 'display notification "Weekly post failed" with title "Nightly Line" sound name "Basso"' 2>/dev/null
exit 0
