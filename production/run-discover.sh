#!/bin/bash
# Daily wallet discovery: market-first sweep of today's slate, every wallet
# holding >= $10K, records looked up, gate-clearing unknowns added to
# discovered.json for check.build_pool. ~250 throttled calls; runs once at
# 11:30am so the pool is wider before the afternoon boards.
cd "$(dirname "$0")" || exit 1
LOG=discover.log
echo "=== $(date '+%F %T %Z')" >> "$LOG"
/usr/bin/python3 holders.py mlb tennis nfl cfb --min 10000 --discover 2>&1 | grep -v "^  markets: \|^  wallets: " >> "$LOG"
