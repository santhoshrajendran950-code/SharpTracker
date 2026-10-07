#!/bin/bash
# Every football month of the past year, in sequence. Resumable per stage.
cd "$(dirname "$0")" || exit 1
for W in "2025-10-01 2025-10-31" "2025-09-01 2025-09-30" "2025-11-01 2025-11-30" "2025-12-01 2025-12-31" "2026-01-01 2026-01-31" "2026-08-01 2026-08-31" "2026-09-01 2026-09-22"; do
  set -- $W
  ./run_window.sh "$1" "$2"
  echo "=== season: finished $1 at $(date)" >> season.log
done
echo "=== season: ALL DONE $(date)" >> season.log
