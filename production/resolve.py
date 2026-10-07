#!/usr/bin/env python3
"""
Score past signals against what actually happened.

scan.py appends every flagged position to signals.jsonl. This resolves the ones
whose games have finished and reports hit rate by tier and by trait, so "the
conviction shape works" can become a measured claim instead of a memory of two
good nights.

Usage:  python3 resolve.py [--verbose]
"""
import json, subprocess, sys
from collections import defaultdict
from datetime import datetime, timezone

LEDGER = "signals.jsonl"
CACHE = "resolved.json"
EVENT = "https://gamma-api.polymarket.com/events?slug={s}"


def get(url):
    p = subprocess.run(["curl", "-s", "--max-time", "30", url],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def parse_list(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def outcome_for(slug, market_title, side, cache):
    """Did `side` win `market_title` in event `slug`? True / False / None."""
    if slug not in cache:
        data = get(EVENT.format(s=slug))
        ev = data[0] if isinstance(data, list) and data else None
        if not ev:
            cache[slug] = {"closed": False, "markets": {}}
        else:
            m = {}
            for mk in ev.get("markets", []):
                names = parse_list(mk.get("outcomes"))
                prices = [float(x) for x in parse_list(mk.get("outcomePrices")) or []]
                if len(names) == len(prices) and prices:
                    m[mk.get("question") or ""] = {
                        n: p for n, p in zip(names, prices)}
            cache[slug] = {"closed": bool(ev.get("closed")), "markets": m}

    entry = cache[slug]
    if not entry["closed"]:
        return None
    prices = entry["markets"].get(market_title)
    if prices is None:
        # position titles and market questions don't always match exactly
        for q, pr in entry["markets"].items():
            if market_title and (market_title in q or q in market_title):
                prices = pr
                break
    if not prices or side not in prices:
        return None
    # a settled market prices the winner at ~1 and everything else at ~0
    if max(prices.values()) < 0.9:
        return None
    return prices[side] >= 0.9


def main():
    verbose = "--verbose" in sys.argv
    try:
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        print(f"No {LEDGER} yet — run scan.py at least once.")
        return 0

    # de-duplicate: the same position gets logged every morning it stays open
    seen, uniq = set(), []
    for r in rows:
        k = (r["slug"], r["market"], r["side"], r["user"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)

    cache = {}
    scored, pending = [], 0
    for r in uniq:
        won = outcome_for(r["slug"], r["market"], r["side"], cache)
        if won is None:
            pending += 1
            continue
        r["won"] = won
        scored.append(r)

    print(f"LEDGER: {len(uniq)} distinct signals · {len(scored)} settled · {pending} pending\n")
    if not scored:
        print("Nothing settled yet. Re-run after some games finish.")
        return 0

    # Flat-stake ROI at the sharp's own entry price. Win rate alone is
    # meaningless without the odds: 79% at heavy chalk still loses money.
    FLAT = 100.0

    def roi(rs):
        rs = [x for x in rs if 0 < (x.get("entry") or 0) < 1]
        if not rs:
            return "      —       "
        staked = FLAT * len(rs)
        ret = sum(FLAT / x["entry"] for x in rs if x["won"])
        return f"{ret-staked:>+8,.0f} on {staked:>6,.0f}  {(ret-staked)/staked*100:>+6.1f}%"

    def rate(rs):
        if not rs:
            return "     —      "
        w = sum(1 for x in rs if x["won"])
        return f"{w}/{len(rs)}  {w/len(rs)*100:5.1f}%"

    print(f"{'BY TIER':12} {'RECORD':>14}   {'FLAT-$100 P/L':>28}   INDEP")
    for tier in ("CONVICTION", "SIZE", "EDGE"):
        rs = [x for x in scored if x["why"] == tier]
        # legs of one trader's stack on one game share an outcome — they are
        # ONE event, not several. Counting them separately overstates the record.
        indep = len({(x["user"], x["slug"]) for x in rs})
        print(f"  {tier:10} {rate(rs)}   {roi(rs)}   {indep} game(s)")

    print("\nBY TRAIT")
    traits = [
        ("stacked (x2+)", lambda x: x.get("stack", 1) >= 2),
        ("single market", lambda x: x.get("stack", 1) < 2),
        ("underdog entry", lambda x: x.get("dog")),
        ("favourite entry", lambda x: not x.get("dog")),
        ("fresh entry", lambda x: x.get("fresh")),
        ("hold >= 3%", lambda x: x.get("hold", 0) >= 3),
    ]
    for label, fn in traits:
        print(f"  {label:16} {rate([x for x in scored if fn(x)])}")

    indep = len({(x["user"], x["slug"]) for x in scored})
    print("-" * 78)
    print(f"  {'OVERALL':10} {rate(scored)}   {roi(scored)}   {indep} game(s)")
    print(f"\n{len(scored)} settled legs represent only {indep} independent outcomes.")
    n = len(scored)
    if n < 30:
        print(f"\n{n} settled signals is far too few to conclude anything. "
              f"Treat every rate above as noise until this is in the hundreds.")

    if verbose:
        print("\nSETTLED DETAIL")
        for r in sorted(scored, key=lambda x: -x["stake"]):
            print(f"  {'WON ' if r['won'] else 'LOST'} [{r['why']:10}] "
                  f"${r['stake']:>9,.0f} {r['side'][:18]:20} {r['market'][:44]}")

    with open(CACHE, "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(),
                   "settled": len(scored), "pending": pending}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
