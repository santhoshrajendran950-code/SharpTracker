#!/bin/bash
# Linux/server version of run-scan.sh — daily broad scan + ledger append.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
[ -f deploy/nightly-line.env ] && set -a && . deploy/nightly-line.env && set +a
LOG=scan.log
OUT=$(python3 scan.py 2>&1); CODE=$?
{ echo "===================================================================="
  echo "run at $(date '+%F %T %Z')  (exit $CODE)"; echo "$OUT"; } >> "$LOG"
if [ $CODE -ne 0 ]; then
  printf 'Daily scan FAILED (exit %s)\n\n%s\n' "$CODE" "$OUT" \
    | python3 deploy/mailer.py "Sharp Scan — FAILED" >> "$LOG" 2>&1
  exit $CODE
fi
# email only when something cleared the bar
python3 - <<'PY' > daily-body.txt
import json
d=json.load(open("scan-latest.json"))
f=d.get("flagged",[])
if not f: raise SystemExit
print(f"{len(f)} flagged across {d['games']} games · {d['pool']-d['unresolved']}/{d['pool']} wallets")
if d.get("unresolved"): print(f"!! {d['unresolved']} lookups failed — PARTIAL\n")
for h in sorted(f,key=lambda x:-x['stake'])[:20]:
    print(f"\n${h['stake']:,.0f} on {h['side']}  [{h['why']}]")
    print(f"  {h['market']}")
    print(f"  {h['league']} · starts {h['start'][:16].replace('T',' ')} UTC")
    print(f"  {h['user']} — #{h['rank']}, {h['hold']:.1f}% hold")
print("\nInformational only — what anonymous traders hold. Not advice.")
PY
if [ -s daily-body.txt ]; then
  N=$(head -1 daily-body.txt | awk '{print $1}')
  python3 deploy/mailer.py "Sharp Scan — $N flagged" daily-body.txt >> "$LOG" 2>&1
fi
exit 0
