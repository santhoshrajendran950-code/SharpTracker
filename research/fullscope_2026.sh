#!/bin/bash
# Re-run the 2026 football windows after the hist_slate discovery fix.
#
# WHY: gamma's start_date_min/max filters on the market's OPEN date, not
# kickoff. hist_slate walked open-dates across the window +/- 3 days, so every
# game whose market was listed earlier than that was never fetched — NFL Week 1
# (13 games, Sun 2026-09-13) and the Sep 3-5 CFB slates were missing from the
# windows entirely. hist_slate now sweeps start_time_min/max (kickoff) and
# unions the old open-date sweep, and records any unreadable slice under
# "holes" in slate.json instead of returning short in silence.
#
# Both slates are re-discovered here, then every downstream stage for both
# windows. Positions MUST be rebuilt from scratch (the slate is wider), so
# bt_positions.jsonl is removed first. Stages above that are resumable:
# hist_discover skips markets already in trades.jsonl, hist_prior skips wallets
# already in prior.json, so only the newly-added games cost anything.
# btlib never takes production's sweep lock, only waits on it.
# Partial-scope windows preserved in windows/_partialscope-*.
cd "$(dirname "$0")" || exit 1
LOG=fullscope.log
run() { echo "=== $(date '+%F %T %Z') $*" >> "$LOG"; "$@" >> "$LOG" 2>&1 || { echo "!! FAILED: $*" >> "$LOG"; exit 1; }; }

run /usr/bin/python3 hist_slate.py 2026-09-01 2026-09-30
run /usr/bin/python3 hist_slate.py 2026-10-01 2026-10-18

for spec in "2026-09 2026-09-01 2026-09-30" "2026-10 2026-10-01 2026-10-18"; do
  set -- $spec; YM=$1
  echo "######## $YM $(date)" >> "$LOG"
  run /usr/bin/python3 hist_discover.py "$YM"
  run /usr/bin/python3 hist_prior.py "$YM"
  run /usr/bin/python3 hist_adapt.py "$YM"
  rm -f "windows/$YM/bt_positions.jsonl"
  ( cd "windows/$YM" && /usr/bin/python3 bt_rebuild.py ) >> "$LOG" 2>&1 || { echo "!! rebuild FAILED $YM" >> "$LOG"; exit 1; }
  ( cd "windows/$YM" && /usr/bin/python3 bt_score.py ) >> "$LOG" 2>&1 || { echo "!! score FAILED $YM" >> "$LOG"; exit 1; }
  echo "######## $YM DONE $(date)" >> "$LOG"
done
echo "######## FULLSCOPE ALL DONE $(date)" >> "$LOG"
