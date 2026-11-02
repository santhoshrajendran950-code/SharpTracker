#!/bin/bash
# NCAAB 2025-26, every month with games (Nov-Apr), one month at a time so it
# never competes with itself for the throttled data-api. Resumable.
cd "$(dirname "$0")" || exit 1
LOG=ncaab_season.log
for R in "2025-11-01 2025-11-30" "2025-12-01 2025-12-31" "2026-01-01 2026-01-31" \
         "2026-02-01 2026-02-28" "2026-03-01 2026-03-31" "2026-04-01 2026-04-30"; do
  echo "=== $(date) starting $R" >> "$LOG"
  ./run_sport_window.sh ncaa-basketball $R
  echo "=== $(date) finished $R" >> "$LOG"
done
echo "=== $(date) NCAAB SEASON DONE" >> "$LOG"
