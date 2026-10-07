#!/bin/bash
# Freeze yesterday's settled ledger. Timing-insensitive by design — if the Mac
# was asleep at 10:00, launchd runs it on next wake and the result is identical.
cd "$(dirname "$0")" || exit 1
DAY=$(date -v-1d '+%Y-%m-%d')
/usr/bin/python3 dayledger.py --date "$DAY" >> dayledger.log 2>&1
echo "[$(date '+%F %T %Z')] froze $DAY (exit $?)" >> dayledger.log
