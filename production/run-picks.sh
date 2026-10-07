#!/bin/bash
# Publish newly qualifying plays to X, and log them to the public record.
#
# Runs every 20 minutes 09:00-23:00. Most runs post nothing: a play must clear
# 70, carry a trustworthy size signal, have a real bettable price, and not have
# been posted before. On a thin weekday that is zero all day, which is correct —
# an account that posts filler is worth less than one that stays quiet.
#
# picks.jsonl is the PUBLIC record and is the only thing this touches.
# bets.jsonl is Santhosh's own money and is never read here.
cd "$(dirname "$0")" || exit 1
LOG=picks.log

# At a 10-minute cadence a slow board (the sweep lock, a big slate) can still be
# running when the next slot fires. Two concurrent scans would double-post, so
# take a lock and skip rather than queue.
LOCK=/tmp/.nightly-picks.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
    echo "[$(date '+%F %T %Z')] previous scan still running — skipped" >> "$LOG"
    exit 0
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
BOARD=/tmp/.picks-board.json

# --pre: a pick is a pre-kickoff call. Without it live positions were being
# published in-game (Giants ML 6 min after kickoff on 2026-09-21).
/usr/bin/python3 bigboard.py --pre --min-score 65 --json "$BOARD" >/dev/null 2>>"$LOG" || {
  echo "[$(date '+%F %T %Z')] board failed — skipped" >> "$LOG"; exit 0; }
[ -s "$BOARD" ] || exit 0

# Post FIRST, log only what actually published. picks.jsonl is the public
# record; a pick sitting in it that X rejected (402 credits, 403 permissions)
# would be a call nobody could have seen, which is worse than no record at all.
NEW=$(/usr/bin/python3 picks.py --scan "$BOARD" --log 2>>"$LOG" | head -1)
echo "[$(date '+%F %T %Z')] $NEW" >> "$LOG"
case "$NEW" in 0\ new*|"") rm -f "$BOARD"; exit 0 ;; esac

/usr/bin/python3 - >> "$LOG" 2>&1 <<'PY'
import sys, json, os
sys.path.insert(0, "/Users/santhosh/.nightly-line")
import picks, xpost, xcard, discordsink as D
import units as _U
def U_fmt(p): return _U.fmt(p["units"])
# post only picks logged in the last few minutes and never posted to X
from datetime import datetime, timezone, timedelta
cut = datetime.now(timezone.utc) - timedelta(minutes=30)
rows = picks.load()
fresh = [p for p in rows
         if not p.get("x_posted")
         and datetime.fromisoformat(p["posted_at"]) >= cut]
sent = dsent = 0
for p in fresh:
    # pick card (xcard.py); a failed render just means a text-only post
    card = f"/tmp/.xpick-{os.getpid()}.png"
    try:
        cok, cwhy = xcard.render_pick(p, card)
    except Exception as e:
        cok, cwhy = False, str(e)
    if not cok:
        print(f"pick card skipped: {cwhy}")
    # with the card: play + best price only. Without it: the full text.
    ok, why, _img = xpost.post_with_image(picks.caption(p), card if cok else None,
                                          text_if_no_image=picks.post_text(p))
    if ok:
        p["x_posted"] = True
        sent += 1
    else:
        print(f"!! X post FAILED: {why}")
    # same play, same moment, richer format, routed by sport
    ch = D.channel_for("sharp", p.get("league") or "")
    dok, derr = D.send(ch, f"{p.get('league','')} · {U_fmt(p)} {p['pick']}",
                       picks.discord_text(p))
    if dok:
        p["d_posted"] = True
        dsent += 1
    else:
        print(f"!! Discord post FAILED ({ch}): {derr}")
# persist if EITHER destination succeeded — guarding on `sent` alone would
# lose the d_posted flag whenever X failed and Discord did not, and the next
# scan would then double-post to Discord.
if sent or dsent:
    with open(picks.PICKS, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
print(f"posted {sent}/{len(fresh)} to X, {dsent}/{len(fresh)} to Discord")
PY
rm -f "$BOARD"
exit 0
