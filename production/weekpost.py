#!/usr/bin/env python3
"""
Weekly public summary — composed Monday, for the week that just closed.

    python3 weekpost.py              # dry run: print the post, or say why not
    python3 weekpost.py --send       # post to X and Discord
    python3 weekpost.py --week 2026-09-14

Refuses to publish an incomplete week. Two checks, both of which have already
bitten this ledger:

  SATURDAY AND SUNDAY MUST HAVE BETS. Sunday 2026-09-20 sat empty for a day
  because the tickets were never sent over; a summary posted in that state would
  have silently omitted 41 wagers and reported a week that never happened.

  NOTHING IN THE WEEK MAY BE UNSETTLED, futures excepted. An open ticket means
  the record is not final — Friday's soccer sat unsettled for days waiting on
  scores. Bets flagged `future` (Heisman, division winners) are skipped, since
  they resolve months later and would otherwise hold the post indefinitely.

When either check fails it emails Santhosh — him only, no BCC — and posts
nothing. Silence plus a nudge beats a wrong number on a public record.

UNITS ONLY. UNIT_USD converts the ledger's dollars into units for reporting;
no dollar figure is ever rendered.
"""
import json, os, subprocess, sys
from collections import defaultdict
from datetime import datetime, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anon, units as U

BETS = os.path.join(HERE, "bets.jsonl")
EPOCH = date(2026, 9, 14)        # week 1 Monday
EMAIL_TO = "YOUR_EMAIL@example.com"
CLAUDE = "/Users/santhosh/.local/bin/claude"


def week_bounds(ref=None):
    ref = ref or date.today()
    monday = ref - timedelta(days=ref.weekday())
    start = monday - timedelta(days=7)
    return start, start + timedelta(days=6)


def load():
    try:
        return [json.loads(l) for l in open(BETS) if l.strip()]
    except FileNotFoundError:
        return []


def ret(b):
    return b["to_win"] if b["result"] == "won" else -b["risk"]


def check(rows, lo, hi):
    """(ok, reason). Reason is the email body when not ok."""
    wk = [b for b in rows if lo.isoformat() <= b.get("date", "") <= hi.isoformat()]
    sat = (hi - timedelta(days=1)).isoformat()
    sun = hi.isoformat()
    missing = [d for d in (sat, sun) if not any(b.get("date") == d for b in wk)]
    if missing:
        return False, ("No bets logged for " + " and ".join(missing) +
                       ".\n\nThe weekly post is held until they are in. Send the "
                       "tickets and re-run:\n  python3 ~/.nightly-line/weekpost.py --send")
    # Long-dated futures never settle inside the week they were placed — a
    # Heisman bet would otherwise hold the weekly post for months. They are
    # already excluded from the numbers (compose() only counts settled rows),
    # so they must not gate publication either.
    open_b = [b for b in wk if not b.get("result") and not b.get("future")]
    if open_b:
        lines = "\n".join(f"  {b['date']}  {b['book']}  {b['pick'][:40]}  ({b['game']})"
                          for b in open_b)
        return False, (f"{len(open_b)} ticket(s) in {lo}–{hi} are still unsettled, so "
                       f"the week is not final:\n\n{lines}\n\nSettle them, then:\n"
                       f"  python3 ~/.nightly-line/weekpost.py --send")
    if not wk:
        return False, f"No bets at all in {lo}–{hi}."
    return True, ""


def compose(rows, lo, hi):
    """Positions, not tickets: the same play at two books is one call."""
    wk = [b for b in rows
          if lo.isoformat() <= b.get("date", "") <= hi.isoformat() and b.get("result")]
    pos = defaultdict(lambda: [0.0, 0.0])
    for b in wk:
        k = (b["date"], b.get("game") or "?", b.get("pick") or "?")
        pos[k][0] += b["risk"]
        pos[k][1] += ret(b)
    won = sum(1 for v in pos.values() if v[1] > 0)
    lost = len(pos) - won
    risk = sum(v[0] for v in pos.values())
    net = sum(v[1] for v in pos.values())
    n = (lo - EPOCH).days // 7 + 1
    return (f"WEEK {n} · {lo:%b %-d}-{hi:%-d}\n\n"
            f"{won}-{lost}  ·  {won/len(pos)*100:.0f}% win rate\n"
            f"{risk/U.UNIT_USD:.0f}u risked  ·  {net/U.UNIT_USD:+.1f}u\n"
            f"ROI {anon.roi(net/risk*100)}")


def email(subject, body):
    subprocess.run(
        [CLAUDE, "-p",
         f"Send one email using mcp__claude_ai_Gmail__send_message.\n"
         f"to: [\"{EMAIL_TO}\"]\nsubject: {subject}\nbody:\n{body}\n\n"
         f"Send to that ONE recipient only — no cc, no bcc. Reply with only "
         f"DONE or FAILED:<reason>.",
         "--allowedTools", "mcp__claude_ai_Gmail__send_message",
         "--permission-prompts", "none", "--no-session-persistence",
         "--model", "claude-sonnet-5"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)


def main():
    a = sys.argv[1:]
    send = "--send" in a
    if "--week" in a:
        lo = datetime.strptime(a[a.index("--week") + 1], "%Y-%m-%d").date()
        lo -= timedelta(days=lo.weekday())
        hi = lo + timedelta(days=6)
    else:
        lo, hi = week_bounds()

    rows = load()
    ok, why = check(rows, lo, hi)
    if not ok:
        print(f"HELD · {lo} – {hi}\n{why}")
        if send:
            email(f"Weekly post held — {lo} to {hi}", why)
            print("\n(emailed Santhosh; nothing posted)")
        return 0

    text = compose(rows, lo, hi)
    print("-" * 46); print(text); print("-" * 46); print(f"{len(text)} chars")
    if not send:
        print("\nDRY RUN. Add --send to publish.")
        return 0
    import xpost, discordsink as D
    xok, xwhy = xpost.post(text)
    print("X: posted" if xok else f"X: FAILED — {xwhy}")
    dok, derr = D.send("board", f"Week {(lo - EPOCH).days // 7 + 1} summary", text)
    print("Discord: posted" if dok else f"Discord: FAILED — {derr}")
    return 0 if (xok and dok) else 1


if __name__ == "__main__":
    sys.exit(main())
