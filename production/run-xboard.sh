#!/bin/bash
# Post the board to X at fixed pre-kickoff times for NFL and CFB only.
#
# Separate from run-slateboard.sh, which keeps posting to Discord on its own
# schedule, and from run-picks.sh, which publishes individual picks. This is a
# roundup: a headline of the top plays at a wave, not a recommendation, and it
# never writes to picks.jsonl.
#
# Usage: run-xboard.sh <nfl|cfb|all> <label>
cd "$(dirname "$0")" || exit 1
LOG=xboard.log
LEAGUE="$1"; LABEL="$2"
BOARD=/tmp/.xboard-$LEAGUE.json

# `all` = every sport, games still to be played only. A Monday 8pm board that
# is NFL-only is one game; the MLB late slate and overnight tennis are on the
# same board and were being left off it. --pre drops anything already live,
# because a board pushed at people should only carry what can still be bet.
if [ "$LEAGUE" = "all" ]; then
  ARGS="--pre"
else
  ARGS="$LEAGUE"
fi
# The picks scan runs every ~10 minutes and holds the bigboard lock for about a
# minute, so a fixed 8:00 slot lands on it regularly. Without a retry that was
# a silently missing board, which looks exactly like a quiet slate.
TRY=0
until /usr/bin/python3 bigboard.py $ARGS --min-score 65 --json "$BOARD" >/dev/null 2>>"$LOG"; do
  TRY=$((TRY+1))
  if [ $TRY -ge 4 ]; then
    echo "[$(date '+%F %T %Z')] !! board failed ($LEAGUE) after $TRY tries — NOT posted" >> "$LOG"; exit 0
  fi
  echo "[$(date '+%F %T %Z')] board busy/failed ($LEAGUE) — retry $TRY in 45s" >> "$LOG"
  sleep 45
done
[ -s "$BOARD" ] || { echo "[$(date '+%F %T %Z')] no board json ($LEAGUE)" >> "$LOG"; exit 0; }

# --image attaches the rendered card (xcard.py). If the render or the upload
# fails, xpost falls back to the text headline rather than skipping the slot.
OUT=$(/usr/bin/python3 xpost.py --board "$BOARD" --label "$LABEL" --image --send 2>&1)
echo "[$(date '+%F %T %Z')] $LEAGUE | $LABEL | $(printf '%s' "$OUT" | tail -1)" >> "$LOG"

# the same wave board to Discord, same moment, full table rather than the
# 280-character headline
/usr/bin/python3 - "$BOARD" "$LABEL" >> "$LOG" 2>&1 <<'PY'
import sys, json
sys.path.insert(0, "/Users/santhosh/.nightly-line")
import postplays as P, discordsink as D
from oddskey import get_key
rows = [r for r in json.load(open(sys.argv[1])) if (r.get("conv") or 0) >= 60]
if rows:
    try: key = get_key()
    except Exception: key = None
    body = [sys.argv[2], "",
            f"{'CV':>3} {'SZ':<4} {'WAGER':>6} {'PLAY':17} {'ODDS':>6}  WHERE"]
    body += P.render(rows, key)
    body += ["", "Informational only — what anonymous traders hold. Not advice.",
             "Unit sizing is still in testing."]
    ok, err = D.send("board", sys.argv[2], "\n".join(body))
    print(f"board -> Discord: {'ok' if ok else 'FAILED ' + str(err)}")
PY
rm -f "$BOARD"
exit 0
