#!/bin/bash
# Extend the 2026 football windows to cover the season through the last
# completed week (Oct 18). Stages are resumable: hist_discover skips markets
# already in trades.jsonl, hist_prior skips wallets already in prior.json, so
# only the newly-closed games cost anything. btlib NEVER takes the sweep lock
# (it waits on production's), so this cannot starve the picks job.
# Originals preserved in windows/_prebackfill-*.
cd "$(dirname "$0")" || exit 1
LOG=backfill.log
run() { echo "=== $(date '+%F %T %Z') $*" >> "$LOG"; "$@" >> "$LOG" 2>&1 || { echo "!! FAILED: $*" >> "$LOG"; exit 1; }; }

for spec in "2026-09 2026-09-01 2026-09-30" "2026-10 2026-10-01 2026-10-18"; do
  set -- $spec; YM=$1; LO=$2; HI=$3
  echo "######## $YM $LO..$HI $(date)" >> "$LOG"
  run /usr/bin/python3 hist_slate.py "$LO" "$HI"
  run /usr/bin/python3 hist_discover.py "$YM"
  run /usr/bin/python3 hist_prior.py "$YM"
  run /usr/bin/python3 hist_adapt.py "$YM"
  rm -f "windows/$YM/bt_positions.jsonl"        # force a full rebuild on the wider slate
  ( cd "windows/$YM" && /usr/bin/python3 bt_rebuild.py ) >> "$LOG" 2>&1 || { echo "!! rebuild FAILED $YM" >> "$LOG"; exit 1; }
  ( cd "windows/$YM" && /usr/bin/python3 bt_score.py ) >> "$LOG" 2>&1 || { echo "!! score FAILED $YM" >> "$LOG"; exit 1; }
  echo "######## $YM DONE $(date)" >> "$LOG"
done
echo "######## BACKFILL ALL DONE $(date)" >> "$LOG"
