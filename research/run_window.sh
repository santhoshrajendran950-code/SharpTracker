#!/bin/bash
# Full pipeline for one month: ./run_window.sh 2025-10-01 2025-10-31
# Stages are resumable; rerun the script and finished stages are skipped/cheap.
cd "$(dirname "$0")" || exit 1
START="$1"; END="$2"; YM="${START:0:7}"; D="windows/$YM"; mkdir -p "$D"; LOG="$D/pipeline.log"
{
echo "=== $YM start $(date)"
[ -s "$D/slate.json" ]   || /usr/bin/python3 hist_slate.py "$START" "$END" || { echo "!! slate FAILED"; exit 1; }
[ -s "$D/wallets.json" ] || /usr/bin/python3 hist_discover.py "$YM"       || { echo "!! discover FAILED"; exit 1; }
echo "=== discover done $(date)"
/usr/bin/python3 hist_prior.py "$YM"                                       || { echo "!! prior FAILED"; exit 1; }
echo "=== prior done $(date)"
/usr/bin/python3 hist_adapt.py "$YM"                                       || { echo "!! adapt FAILED"; exit 1; }
[ -s "$D/bt_positions.jsonl" ] || (cd "$D" && /usr/bin/python3 bt_rebuild.py) || { echo "!! rebuild FAILED"; exit 1; }
echo "=== rebuild done $(date)"
(cd "$D" && /usr/bin/python3 bt_score.py)                                   || { echo "!! score FAILED"; exit 1; }
echo "=== score done $(date)"
/usr/bin/python3 lib/bt_strategies.py "$(pwd)/$D" > "$D/strategies.txt" 2>&1 && echo "=== report written $(date)"; /usr/bin/python3 hist_cuts.py "$YM" > "$D/cuts.txt" 2>&1
echo "=== $YM complete $(date)"
} >> "$LOG" 2>&1
