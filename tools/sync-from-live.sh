#!/bin/bash
# Pull the live code into this repo, redacted, so the repo is the code of record.
#
#   ~/.nightly-line/*.py *.sh deploy/ PORTING-*.md     -> production/
#   ~/Library/LaunchAgents/com.nightlyline.*.plist     -> production/launchd/
#   ~/nl-research/*.py *.sh README.md lib/*.py         -> research/
#   ~/.nightly-line/backtest/*.py                      -> research/backtest-v1/
#   ~/nl-research/windows/<ym>/{cuts,strategies}.txt   -> results/football/<ym>/
#   ~/nl-research/windows/<sport>/<ym>/cuts.txt        -> results/sports/<sport>/<ym>.cuts.txt
#
# Never copied (docs/exclusions.md): data (*.json, *.jsonl, lines/, ledgers/,
# windows/**/*.json*), logs, *.bak*, secrets, trusted.json, discovered.json,
# aliases.json, nhl_sharps.* (named wallets). Three strings are redacted in the
# copies and the script REFUSES to finish if any survive. The originals on the
# host are never touched.
#
# Usage: tools/sync-from-live.sh [--check]   (--check = report drift, copy nothing)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LIVE="$HOME/.nightly-line"
RES="$HOME/nl-research"
PLISTS="$HOME/Library/LaunchAgents"
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1

# string -> placeholder, one "from|to" per line, in a PRIVATE file outside the
# repo so the secrets never appear here.  Keep docs/exclusions.md in step.
MAP="$LIVE/.redact"
[ -f "$MAP" ] || { echo "missing $MAP (from|to lines)"; exit 1; }
REDACT=(); while IFS= read -r line; do [ -n "$line" ] && REDACT+=("$line"); done < "$MAP"

redact() {  # redact <file>
  local f="$1" pair from to
  for pair in "${REDACT[@]}"; do
    from="${pair%%|*}"; to="${pair##*|}"
    LC_ALL=C sed -i '' "s|${from}|${to}|g" "$f"
  done
}

copy() {  # copy <src> <dst>  — redacted copy, prints the file if it changed
  local src="$1" dst="$2" tmp
  [ -f "$src" ] || return 0
  tmp="$(mktemp)"; cp "$src" "$tmp"; redact "$tmp"
  if [ ! -f "$dst" ] || ! cmp -s "$tmp" "$dst"; then
    echo "  $(basename "$src")  ->  ${dst#$REPO/}"
    if [ $CHECK -eq 0 ]; then mkdir -p "$(dirname "$dst")"; cp "$tmp" "$dst"; fi
  fi
  rm -f "$tmp"
}

echo "== production/ =="
for f in "$LIVE"/*.py "$LIVE"/*.sh "$LIVE"/PORTING-TO-OTHER-MARKETS.md; do
  case "$f" in *.bak*) continue;; esac
  copy "$f" "$REPO/production/$(basename "$f")"
done
for f in "$LIVE"/deploy/*; do
  case "$(basename "$f")" in legacy|systemd) ;; *.env|.env*) continue;; esac
  [ -f "$f" ] && copy "$f" "$REPO/production/deploy/$(basename "$f")"
done
for d in systemd legacy; do
  for f in "$LIVE"/deploy/$d/*; do [ -f "$f" ] && copy "$f" "$REPO/production/deploy/$d/$(basename "$f")"; done
done
for f in "$PLISTS"/com.nightlyline.*.plist; do copy "$f" "$REPO/production/launchd/$(basename "$f")"; done

echo "== research/ =="
for f in "$RES"/*.py "$RES"/*.sh "$RES"/README.md "$RES"/keep_list.txt; do
  case "$(basename "$f")" in nhl_sharps*) continue;; esac
  copy "$f" "$REPO/research/$(basename "$f")"
done
for f in "$RES"/lib/*.py; do copy "$f" "$REPO/research/lib/$(basename "$f")"; done
for f in "$LIVE"/backtest/*.py; do copy "$f" "$REPO/research/backtest-v1/$(basename "$f")"; done

echo "== results/ =="
for w in "$RES"/windows/*/; do
  name="$(basename "$w")"
  if [[ "$name" =~ ^20[0-9]{2}-[0-9]{2}$ ]]; then          # football season windows
    copy "$w/cuts.txt"       "$REPO/results/football/$name/cuts.txt"
    copy "$w/strategies.txt" "$REPO/results/football/$name/strategies.txt"
  else                                                      # <sport>/<ym>/
    for m in "$w"/*/; do
      ym="$(basename "$m")"
      copy "$m/cuts.txt"       "$REPO/results/sports/$name/$ym.cuts.txt"
      copy "$m/strategies.txt" "$REPO/results/sports/$name/$ym.strategies.txt"
    done
    [ -f "$w/aggregate_13mo.txt" ] && copy "$w/aggregate_13mo.txt" "$REPO/results/sports/$name/aggregate_13mo.txt"
  fi
done
copy "$RES/probe_results.json" "$REPO/results/sports/probe_results.json"

[ $CHECK -eq 1 ] && { echo "(check only — nothing copied)"; exit 0; }

# Refuse to leave a secret behind.
bad=0
for pair in "${REDACT[@]}"; do
  from="${pair%%|*}"
  if hits="$(grep -rl --exclude-dir=.git -- "$from" "$REPO" 2>/dev/null)"; then
    echo "REDACTION FAILED — '$from' still present in:"; echo "$hits"; bad=1
  fi
done
if grep -rlE --exclude-dir=.git -- "0x[0-9a-fA-F]{40}" "$REPO/results" "$REPO/docs" 2>/dev/null | grep -q .; then
  echo "WALLET ADDRESS found under results/ or docs/ — see docs/exclusions.md"; bad=1
fi
[ $bad -eq 1 ] && exit 1
echo "synced $(date '+%F %T')"
