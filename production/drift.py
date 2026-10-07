#!/usr/bin/env python3
"""
"what moved" — which of today's live signals the market has moved AGAINST.

    python3 drift.py                 # every un-started signal from today
    python3 drift.py --hours 48      # widen the horizon
    python3 drift.py --all           # show favourable moves too

The 2026-09-14 post-mortem found that 17 of 24 losses had the market drifting
away from the position before settlement — "bad read", not variance. That makes
pre-game drift the most promising filter in the system, and possibly worth more
than any trait weight. This is the live version of that: it prices every open
signal now, against the price it was logged at, and says which ones the market
has since disagreed with.

Drift is not the same as CLV. CLV is settled history, measured after kickoff and
useful for grading a model. Drift is measured while the game is still bettable,
which makes it the only one of the two you can act on.

Reads signals.jsonl. Never reads bets.jsonl.
"""
import json, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import check as chk
import clv as C

LEDGER = "signals.jsonl"
ADVERSE = 0.03          # pts of implied probability before it is worth saying


def now_price(token, hcache):
    """Last traded price available right now."""
    if token not in hcache:
        d = C.get(f"https://clob.polymarket.com/prices-history?market={token}"
                  f"&interval=1d&fidelity=1")
        h = d.get("history") if isinstance(d, dict) else (d if isinstance(d, list) else None)
        hcache[token] = h or []
    hist = hcache[token]
    return float(hist[-1]["p"]) if hist else None


def main():
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 24.0
    show_all = "--all" in sys.argv
    now = datetime.now(timezone.utc)

    try:
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        print("No signals.jsonl yet.")
        return 0

    # latest logged state per position, upcoming games only
    live = {}
    for r in rows:
        try:
            start = datetime.fromisoformat(r["start"])
        except Exception:
            continue
        mins = (start - now).total_seconds() / 60
        if mins <= 0 or mins / 60 > hours:
            continue
        k = (r.get("slug"), r.get("market"), r.get("side"), r.get("user"))
        if k not in live or r["logged"] > live[k]["logged"]:
            live[k] = r

    if not live:
        print(f"No logged signals on games starting in the next {hours:.0f}h.")
        return 0

    ccache, hcache, out, unpriced = {}, {}, [], 0
    for r in live.values():
        tok = C.token_for(r["slug"], r["market"], r["side"], ccache)
        px = now_price(tok, hcache) if tok else None
        if px is None:
            unpriced += 1
            continue
        entry = r.get("entry") or 0
        if not (0 < entry < 1):
            unpriced += 1
            continue
        r["px"] = px
        r["move"] = px - entry          # + = market agrees, - = market disagrees
        out.append(r)

    if not out:
        print(f"Nothing priceable ({unpriced} unpriced).")
        return 0

    out.sort(key=lambda x: x["move"])
    against = [x for x in out if x["move"] <= -ADVERSE]
    toward = [x for x in out if x["move"] >= ADVERSE]

    print(f"DRIFT · {now.astimezone():%a %-I:%M %p %Z} · {len(out)} live signals priced"
          + (f" · {unpriced} unpriceable" if unpriced else ""))
    print(f"{len(against)} moved against  ·  {len(toward)} moved toward  ·  "
          f"{len(out)-len(against)-len(toward)} flat\n")

    def row(x, tag):
        start = datetime.fromisoformat(x["start"])
        mins = (start - now).total_seconds() / 60
        print(f"  {tag} {x['move']*100:>+6.2f} pts  {chk.am(x['entry']):>7} -> "
              f"{chk.am(x['px']):>7}  [{x.get('conv',0):>4.1f}] {chk.m0(x['stake']):>9}  "
              f"{x['side'][:20]:22} {x.get('league',''):6} {x['game'][:28]:30} "
              f"{int(mins//60)}h{int(mins%60):02d}m")

    if against:
        print("MOVED AGAINST — the market disagrees since this was logged:")
        for x in against:
            row(x, "!!")
        print()
    if toward and (show_all or not against):
        print("MOVED TOWARD — the market has come to these:")
        for x in toward:
            row(x, "  ")
        print()
    if show_all:
        flat = [x for x in out if abs(x["move"]) < ADVERSE]
        if flat:
            print("FLAT:")
            for x in flat:
                row(x, "  ")
            print()

    if against:
        worst = against[0]
        print(f"Biggest disagreement: {worst['side'][:24]} at {worst['move']*100:+.1f} pts.")
    print("Drift is a warning, not a verdict — a moving line can mean news, or it "
          "can mean\nthe rest of the market catching up to a sharp who was early.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
