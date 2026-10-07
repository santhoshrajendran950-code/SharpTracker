#!/bin/bash
# Settle yesterday's bets and refresh that week's Drive sheet. Daily 07:00 local.
#
# Runs an hour before the Monday weeksheet job so the weekly sheet is always
# built from a fully settled ledger rather than one with open tickets in it.
#
# PRIVATE. Reads and writes bets.jsonl; the only destination off this machine is
# Santhosh's own Drive folder. No email, no channel.
cd "$(dirname "$0")" || exit 1
LOG=settle.log
echo "=== $(date '+%F %T %Z') ===" >> "$LOG"
/usr/bin/python3 settle.py --apply --sheet >> "$LOG" 2>&1
CODE=$?
[ $CODE -ne 0 ] && echo "[$(date '+%F %T %Z')] settle failed (exit $CODE)" >> "$LOG"
exit 0
