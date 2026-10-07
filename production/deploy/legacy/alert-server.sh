#!/bin/bash
# Linux/server version of alert.sh.
#   - no osascript (email is the only channel on a server)
#   - key comes from the environment, not the macOS keychain
#   - email via mailer.py (Resend or SMTP) instead of `claude -p` + Gmail MCP
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
[ -f deploy/nightly-line.env ] && set -a && . deploy/nightly-line.env && set +a

THRESHOLD="${ALERT_THRESHOLD:-60}"
FROM_H="${ACTIVE_FROM:-8}"; TO_H="${ACTIVE_TO:-23}"
LOG=alert.log
H=$(date '+%-H')
if [ "$H" -lt "$FROM_H" ] || [ "$H" -ge "$TO_H" ]; then exit 0; fi

OUT=$(python3 check.py --alert "$THRESHOLD" 2>&1); CODE=$?
if [ $CODE -ne 0 ]; then
  echo "[$(date '+%F %T %Z')] scan failed (exit $CODE): $(echo "$OUT" | head -1)" >> "$LOG"
  printf 'Sharp scan FAILED on the server (exit %s)\n\n%s\n' "$CODE" "$OUT" \
    | python3 deploy/mailer.py "Sharp Alert — SCAN FAILED" >> "$LOG" 2>&1
  exit $CODE
fi
if [ -z "$OUT" ]; then
  echo "[$(date '+%F %T %Z')] nothing new >= $THRESHOLD" >> "$LOG"; exit 0
fi

printf '%s\n' "$OUT" > alert-body.txt
COUNT=$(head -1 alert-body.txt | awk '{print $1}')
{ echo ""; echo "======================================================================"
  echo "FULL BOARD"; echo "======================================================================"; } >> alert-body.txt
python3 bigboard.py --wide 2>/dev/null | python3 -c '
import sys
rows=[l.rstrip("\n") for l in sys.stdin if l.strip()]
head=[r for r in rows if not r.startswith("Conv|") and "|" not in r]
tab=[r.split("|") for r in rows if "|" in r]
for h in head: print(h)
if tab:
    w=[max(len(r[i]) if i<len(r) else 0 for r in tab) for i in range(max(len(r) for r in tab))]
    for i,r in enumerate(tab):
        print("  ".join((r[j] if j<len(r) else "").ljust(w[j]) for j in range(len(w))).rstrip())
        if i==0: print("-"*min(sum(w)+2*len(w),110))
' >> alert-body.txt

{ echo "===================================================================="
  echo "FIRED at $(date '+%F %T %Z')"; echo "$OUT"; } >> "$LOG"
python3 deploy/mailer.py "Sharp Alert — $COUNT new high-conviction play(s)" alert-body.txt >> "$LOG" 2>&1 \
  && echo "[$(date '+%F %T %Z')] emailed $COUNT play(s)" >> "$LOG" \
  || echo "[$(date '+%F %T %Z')] EMAIL FAILED" >> "$LOG"
exit 0
