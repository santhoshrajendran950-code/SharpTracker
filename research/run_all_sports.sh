#!/bin/bash
# Waits for the in-flight football season (run_season.sh) to finish, then runs
# the full discover/prior/rebuild/score pipeline for every sport-month that
# hist_probe_months.py found had >= 8 games. Sequential — one sport-month at a
# time — so it never competes with itself for the throttled data-api.
cd "$(dirname "$0")" || exit 1
LOG=all_sports.log
echo "=== $(date) waiting for football (season.log) to complete" >> "$LOG"
while ! grep -q "ALL DONE" season.log 2>/dev/null; do
  pgrep -f run_season.sh > /dev/null || { echo "!! $(date) run_season.sh is not running and never finished — proceeding anyway" >> "$LOG"; break; }
  sleep 120
done
echo "=== $(date) football done (or absent) — starting multi-sport pipeline" >> "$LOG"

if [ ! -s probe_results.json ]; then
  echo "!! probe_results.json missing — run hist_probe_months.py first" >> "$LOG"
  exit 1
fi

/usr/bin/python3 - >> "$LOG" 2>&1 <<'PY'
import json
d = json.load(open("probe_results.json"))
keep = sorted(k for k, v in d.items() if v["games"] >= 8)
with open("keep_list.txt", "w") as f:
    for k in keep:
        f.write(k + "\n")
print(f"{len(keep)} sport-months to run")
PY

while read -r line; do
  SPORT="${line%%/*}"; YM="${line##*/}"
  Y="${YM%-*}"; M="${YM#*-}"
  case "$M" in
    01|03|05|07|08|10|12) LASTDAY=31 ;;
    04|06|09|11) LASTDAY=30 ;;
    02) LASTDAY=28 ;;
  esac
  echo "=== $(date) starting $SPORT $YM" >> "$LOG"
  ./run_sport_window.sh "$SPORT" "$YM-01" "$YM-$LASTDAY"
  echo "=== $(date) finished $SPORT $YM" >> "$LOG"
done < keep_list.txt

echo "=== $(date) ALL SPORTS DONE" >> "$LOG"
