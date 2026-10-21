#!/bin/bash
# Nightly rebuild of the career wager-history store (wallethist.json).
#
# The store is what units.py and bigboard.py compare a live wager against, so a
# stale one quietly mis-sizes: before it existed, `typical` was the median of a
# wallet's CONCURRENTLY OPEN positions, which read Feromont's routine $10,000 as
# 416.7x normal. The store answers with its real $9,031 career median instead.
#
# --build is a full idempotent rebuild from every scored research window, and
# save() writes through a temp file plus os.replace(), so a board sweeping at
# the same moment either sees the old store or the new one, never a half file.
# Measured 2026-10-21: 445,190 rows across 101 window files in 7 seconds.
#
# Note what this does and does not fix. The store grows only as nl-research
# produces new scored windows; wallets that barely appear in that corpus stay
# thin however often this runs (Jokerxing7 was n=4 on the 2026-10-21 board).
# The "!" the board prints on a thin wallet is the honest answer there, not a
# symptom of a stale build.
cd "$(dirname "$0")" || exit 1
LOG=wallethist.log

LOCK=/tmp/.wallethist.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
    echo "[$(date '+%F %T %Z')] previous build still running — skipped" >> "$LOG"
    exit 0
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

T0=$(date +%s)
OUT=$(/usr/bin/python3 wallethist.py --build 2>&1)
CODE=$?
DUR=$(( $(date +%s) - T0 ))

if [ $CODE -ne 0 ]; then
  echo "[$(date '+%F %T %Z')] BUILD FAILED (exit $CODE, ${DUR}s): $(echo "$OUT" | tail -3 | tr '\n' ' ')" >> "$LOG"
  /usr/bin/osascript -e "display notification \"wallethist build failed (exit $CODE)\" with title \"Nightly Line\"" 2>/dev/null
  exit $CODE
fi

echo "[$(date '+%F %T %Z')] ${DUR}s · $(echo "$OUT" | grep -m1 '^built' | sed 's|^built [^ ]* — ||')" >> "$LOG"
exit 0
