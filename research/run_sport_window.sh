#!/bin/bash
# Full pipeline for one sport-month: ./run_sport_window.sh mlb 2025-10-01 2025-10-31
# Mirrors run_window.sh (football) but writes to windows/<sport>/<ym>/ and uses
# hist_slate2.py (sport-aware) for stage 1. Resumable; skips finished stages.
cd "$(dirname "$0")" || exit 1
SPORT="$1"; START="$2"; END="$3"; YM="${START:0:7}"; D="windows/$SPORT/$YM"; mkdir -p "$D"; LOG="$D/pipeline.log"
{
echo "=== $SPORT $YM start $(date)"
[ -s "$D/slate.json" ]   || /usr/bin/python3 hist_slate2.py "$SPORT" "$START" "$END" || { echo "!! slate FAILED"; exit 1; }
N=$(/usr/bin/python3 -c "import json; print(len(json.load(open('$D/slate.json')).get('games',[])))" 2>/dev/null || echo 0)
if [ "$N" -lt 8 ]; then
  echo "=== $SPORT $YM: only $N games — skipping discover/prior/rebuild, confirmed not assumed"
  exit 0
fi
[ -s "$D/wallets.json" ] || /usr/bin/python3 hist_discover.py "$SPORT/$YM"       || { echo "!! discover FAILED"; exit 1; }
echo "=== discover done $(date)"
/usr/bin/python3 hist_prior.py "$SPORT/$YM"                                       || { echo "!! prior FAILED"; exit 1; }
echo "=== prior done $(date)"
/usr/bin/python3 hist_adapt.py "$SPORT/$YM"                                       || { echo "!! adapt FAILED"; exit 1; }
[ -s "$D/bt_positions.jsonl" ] || (cd "$D" && /usr/bin/python3 bt_rebuild.py) || { echo "!! rebuild FAILED"; exit 1; }
echo "=== rebuild done $(date)"
(cd "$D" && /usr/bin/python3 bt_score.py)                                   || { echo "!! score FAILED"; exit 1; }
echo "=== score done $(date)"
/usr/bin/python3 hist_cuts.py "$SPORT/$YM" > "$D/cuts.txt" 2>&1 && echo "=== report written $(date)"
echo "=== $SPORT $YM complete $(date)"
} >> "$LOG" 2>&1
