#!/usr/bin/env python3
"""
Closing-line value for every signal in the ledger.

CLV is the price the market settled at before kickoff, minus the price the
signal was logged at. Positive means the market moved TOWARD the position after
it was taken — the bet got better than the price paid.

Why this matters more than win rate: CLV converges on the truth far faster.
Win/loss needs hundreds of results to separate skill from variance; CLV gives a
read from dozens, because it measures whether the position anticipated the
market rather than whether one coin flip landed. If the conviction model has any
edge, positive CLV shows up long before the win column does.

Usage:  python3 clv.py [--verbose]
"""
import json, subprocess, sys
from collections import defaultdict
from datetime import datetime, timezone

LEDGER = "signals.jsonl"
HIST = "https://clob.polymarket.com/prices-history?market={t}&interval=max&fidelity=60"


def get(url):
    p = subprocess.run(["curl", "-s", "--max-time", "30", url],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def plist(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def token_for(slug, market_title, side, cache):
    """CLOB token id for the exact outcome this signal backed."""
    if slug not in cache:
        d = get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        cache[slug] = d[0] if isinstance(d, list) and d else None
    ev = cache[slug]
    if not ev:
        return None
    best = None
    for m in ev.get("markets", []):
        q = m.get("question") or ""
        if q == market_title or (market_title and (market_title in q or q in market_title)):
            best = m
            if q == market_title:
                break
    if not best:
        return None
    names = plist(best.get("outcomes"))
    toks = plist(best.get("clobTokenIds"))
    if len(names) != len(toks) or side not in names:
        return None
    return toks[names.index(side)]


def closing_price(token, start_iso, hcache):
    """Last traded price at or before kickoff — the closing line."""
    if token not in hcache:
        d = get(HIST.format(t=token))
        h = d.get("history") if isinstance(d, dict) else (d if isinstance(d, list) else None)
        hcache[token] = h or []
    hist = hcache[token]
    if not hist:
        return None
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    before = [p for p in hist if p.get("t", 0) <= start]
    if not before:
        return None
    return float(before[-1]["p"])


def main():
    verbose = "--verbose" in sys.argv
    try:
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        print(f"No {LEDGER} yet.")
        return 0

    seen, uniq = set(), []
    for r in rows:
        k = (r["slug"], r["market"], r["side"], r["user"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)

    ecache, hcache = {}, {}
    out, skipped = [], 0
    for r in uniq:
        entry = r.get("entry") or 0
        if not (0 < entry < 1) or not r.get("start"):
            skipped += 1
            continue
        tok = token_for(r["slug"], r["market"], r["side"], ecache)
        if not tok:
            skipped += 1
            continue
        close = closing_price(tok, r["start"], hcache)
        if close is None:
            skipped += 1
            continue
        r["close"] = close
        r["clv"] = close - entry
        out.append(r)

    if not out:
        print(f"No signals could be priced ({skipped} skipped).")
        return 0

    def block(label, rs):
        if not rs:
            print(f"  {label:16}      —")
            return
        avg = sum(x["clv"] for x in rs) / len(rs)
        beat = sum(1 for x in rs if x["clv"] > 0)
        print(f"  {label:16} {len(rs):>3} sigs   avg CLV {avg*100:>+6.2f} pts   "
              f"beat close {beat}/{len(rs)} ({beat/len(rs)*100:>3.0f}%)")

    print(f"CLOSING-LINE VALUE · {len(out)} signals priced, {skipped} unpriceable\n")
    print("BY TIER")
    for t in ("CONVICTION", "SIZE", "EDGE"):
        block(t, [x for x in out if x["why"] == t])
    print("\nBY TRAIT")
    block("stacked (x2+)", [x for x in out if x.get("stack", 1) >= 2])
    block("single market", [x for x in out if x.get("stack", 1) < 2])
    block("underdog", [x for x in out if x.get("dog")])
    block("favourite", [x for x in out if not x.get("dog")])
    block("hold >= 3%", [x for x in out if x.get("hold", 0) >= 3])
    block("hold < 3%", [x for x in out if x.get("hold", 0) < 3])
    print("-" * 74)
    block("ALL", out)

    avg = sum(x["clv"] for x in out) / len(out)
    print()
    if avg > 0.01:
        print(f"Positive CLV ({avg*100:+.2f} pts): the market moved toward these")
        print("positions after they were logged. That is the signature of a real edge,")
        print("and it shows up on far fewer samples than a win rate does.")
    elif avg < -0.01:
        print(f"Negative CLV ({avg*100:+.2f} pts): the market moved AWAY after logging.")
        print("Any profit so far is more likely variance than edge.")
    else:
        print(f"CLV is flat ({avg*100:+.2f} pts) — no evidence either way yet.")

    if verbose:
        print("\nDETAIL (worst to best)")
        for r in sorted(out, key=lambda x: x["clv"]):
            print(f"  {r['clv']*100:>+6.2f} pts  entry {r['entry']:.3f} -> close {r['close']:.3f}  "
                  f"[{r['why']:10}] {r['side'][:18]:20} {r['market'][:34]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
