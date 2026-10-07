#!/bin/bash
# Move the *.bak-* copies that pile up beside the live code into
# ~/.nightly-line/_backups/.  Nothing in the live tree or the launchd plists
# references a .bak file (checked 2026-10-07), and with the repo as the code
# of record, git history is the backup.  Logs are NOT moved — the plists
# write to them by absolute path.
set -euo pipefail
LIVE="$HOME/.nightly-line"
DEST="$LIVE/_backups"
mkdir -p "$DEST"
n=0
for f in "$LIVE"/*.bak* "$LIVE"/*/*.bak*; do
  [ -f "$f" ] || continue
  case "$f" in "$DEST"/*) continue;; esac
  mv "$f" "$DEST/"; n=$((n+1))
done
echo "moved $n backup files to $DEST"
