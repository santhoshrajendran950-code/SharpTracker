#!/bin/bash
# Midnight: settle every published pick whose game has finished, then post the
# day's scoreboard. Posts on losing days exactly as on winning ones — a record
# with gaps is worth nothing to someone who might pay for it.
cd "$(dirname "$0")" || exit 1
LOG=picks.log
DAY=$(date '+%Y-%m-%d')
echo "=== $(date '+%F %T %Z') results ===" >> "$LOG"
/usr/bin/python3 picks.py --settle >> "$LOG" 2>&1
# closing-line value on the picks just settled — the KPI (pickclv.py)
/usr/bin/python3 pickclv.py >> "$LOG" 2>&1
OUT=$(/usr/bin/python3 picks.py --report --day "$DAY" 2>>"$LOG")
echo "$OUT" >> "$LOG"
case "$OUT" in
  *"No plays settled"*) echo "[$(date '+%F %T')] nothing to post" >> "$LOG"; exit 0 ;;
  *"cannot be valued"*) echo "[$(date '+%F %T')] UNPRICED picks — NOT posting" >> "$LOG"
                        /usr/bin/osascript -e 'display notification "Results post blocked: unpriced picks" with title "Nightly Line" sound name "Basso"' 2>/dev/null
                        exit 0 ;;
esac
/usr/bin/python3 - "$OUT" "$DAY" >> "$LOG" 2>&1 <<'PY'
import sys, os
sys.path.insert(0, "/Users/santhosh/.nightly-line")
import xpost, xcard, picks, discordsink as D
text, day = sys.argv[1], sys.argv[2]
# results card: every settled pick W/L with its price and units. Valued by
# picks.pl, the same function the text report uses, so the two cannot disagree.
card = f"/tmp/.xresults-{os.getpid()}.png"
try:
    allrows = picks.load()
    cok, cwhy = xcard.render_results([p for p in allrows if p.get("date") == day],
                                     allrows, day, picks.pl, card)
except Exception as e:
    cok, cwhy = False, str(e)
if not cok:
    print(f"results card skipped: {cwhy}")
ok, why, img = xpost.post_with_image(text, card if cok else None)
print(("results posted to X" + (" with image" if img else "")) if ok else f"!! X results FAILED: {why}")
# the nightly record goes to both, at the same moment
dok, derr = D.send("board", "Daily results", text)
print("results posted to Discord" if dok else f"!! Discord results FAILED: {derr}")
PY
exit 0
