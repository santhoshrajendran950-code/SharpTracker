#!/bin/bash
# Paper-trade the pick ledger on Novig's play-money exchange.
#
#   run-place.sh          every 10 min 09:04-23:54: new picks.jsonl rows -> paper orders
#   run-place.sh settle   daily 06:50: grade filled orders from Novig's position history
#
# PAPER ONLY. NOVIG_HOST is pinned to api.paper.novig.com here; place.py itself
# refuses any other host unless --real and NOVIG_REAL=1 are both given, and
# neither is ever set in this file. Real money is a separate, deliberate step.
#
# orders.jsonl is PRIVATE (same footing as bets.jsonl): never synced, never sent.
cd "$(dirname "$0")" || exit 1
export NOVIG_HOST=https://api.paper.novig.com
LOG=place.log

if [ "${1:-}" = "settle" ]; then
  echo "=== $(date '+%F %T %Z') settle ===" >> "$LOG"
  /usr/bin/python3 place.py settle >> "$LOG" 2>&1
  /usr/bin/python3 place.py record >> "$LOG" 2>&1
  exit 0
fi

# Picks land in picks.jsonl at :00/:10/... and X-post within a minute or two;
# this runs at :04 and looks back 25 min, so a slow scan is still caught on
# the next pass and the orders ledger prevents a double send.
LOCK=/tmp/.nightly-place.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
    echo "[$(date '+%F %T %Z')] previous run still going — skipped" >> "$LOG"; exit 0
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

OUT=$(/usr/bin/python3 place.py from-picks --since-min 25 2>&1)
CODE=$?
# log only when something was considered, so the file stays readable
case "$OUT" in
  *"0 candidate pick(s)"*) ;;
  *) echo "=== $(date '+%F %T %Z') ===" >> "$LOG"; echo "$OUT" >> "$LOG" ;;
esac
[ $CODE -ne 0 ] && echo "[$(date '+%F %T %Z')] place failed (exit $CODE): $(echo "$OUT" | tail -1)" >> "$LOG"
exit 0
