#!/usr/bin/env python3
"""Santhosh's proposed X-send rule, measured on the research corpus.

    Post a side when >=2 bettors with hold > HOLD_MIN each have more than
    MULT x their own career median on it, AND the opposite side of the same
    market has no bettor who ALSO clears both bars.

The last clause is the new idea: EXP-027 today kills a play if any qualifying
sharp opposes it, with no size or hold test on the opponent. This makes the
opposition test symmetric.

Career median is an EXPANDING median of that wallet's strictly EARLIER wagers,
so nothing looks ahead. One play per (slug, market, side) however many sharps
are on it -- you would post it once.
"""
import glob, json, math, os, random, sys
from collections import defaultdict

VIG        = 0.015
HOLD_MIN   = 3.0      # percent
MULT       = 2.0      # x career median
NEED       = 2        # sharps on the side
MIN_HIST   = 10       # wagers before a median is trusted
MIN_MEDIAN = 250.0    # dust wallets inflate every ratio
PREGAME    = True

def median(xs):
    s = sorted(xs); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2.0

def load():
    fs = sorted(glob.glob(os.path.expanduser("~/nl-research/windows/*/*/bt_scored.jsonl")))
    fs += sorted(glob.glob(os.path.expanduser("~/nl-research/windows/20*/bt_scored.jsonl")))
    rows, seen = [], set()
    for f in fs:
        for line in open(f):
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except ValueError: continue
            k = (r.get("wallet"), r.get("slug"), r.get("asset"), r.get("t_ts"))
            if k in seen: continue
            seen.add(k)
            rows.append(r)
    return rows

def add_career(rows):
    """stake / expanding median of that wallet's EARLIER wagers."""
    by_w = defaultdict(list)
    for r in rows: by_w[r["wallet"]].append(r)
    for rs in by_w.values():
        rs.sort(key=lambda x: x.get("t_ts") or 0)
        hist = []
        for r in rs:
            if len(hist) >= MIN_HIST:
                med = median(hist)
                if med >= MIN_MEDIAN:
                    r["_med"] = med
                    r["_rel"] = (r.get("stake") or 0) / med
            hist.append(r.get("stake") or 0)
    return rows

def flat(r):
    e = r.get("entry") or 0
    if e <= 0: return None
    return (1.0 / (e + VIG) - 1.0) if r.get("won") else -1.0

def qualifies(r, hold_min, mult):
    return (r.get("_rel") is not None
            and (r.get("hold") or -99) > hold_min
            and r["_rel"] > mult
            and not r.get("decided")
            and not r.get("two_sided")
            and (not PREGAME or (r.get("t_ts") or 0) < (r.get("kickoff_ts") or 0)))

def fire(rows, hold_min=HOLD_MIN, mult=MULT, need=NEED, symmetric=True):
    """-> list of fired plays, one per (slug, market, side)."""
    by_side = defaultdict(list)
    for r in rows:
        if qualifies(r, hold_min, mult):
            by_side[(r["slug"], r.get("market"), r.get("side"))].append(r)
    sides_in_market = defaultdict(set)
    for (slug, mkt, side) in by_side:
        sides_in_market[(slug, mkt)].add(side)
    # any bettor at all on a side (for the asymmetric EXP-027 comparison)
    any_side = defaultdict(list)
    for r in rows:
        if (not r.get("decided") and not r.get("two_sided")
                and (not PREGAME or (r.get("t_ts") or 0) < (r.get("kickoff_ts") or 0))):
            any_side[(r["slug"], r.get("market"), r.get("side"))].append(r)

    out = []
    for (slug, mkt, side), rs in by_side.items():
        if len(rs) < need: continue
        others = [s for s in sides_in_market[(slug, mkt)] if s != side]
        if symmetric:
            opposed = any(others)                       # opponent cleared both bars
        else:
            opposed = any(any_side.get((slug, mkt, s))  # EXP-027: anyone at all
                          for s in {k[2] for k in any_side if k[0] == slug and k[1] == mkt}
                          if s != side)
        if opposed: continue
        rs.sort(key=lambda x: x.get("t_ts") or 0)
        lead = rs[0]
        f = flat(lead)
        if f is None: continue
        out.append({"slug": slug, "market": mkt, "side": side, "n": len(rs),
                    "league": lead.get("league"), "entry": lead["entry"],
                    "flat": f, "clv_entry": lead.get("clv_entry"),
                    "won": bool(lead.get("won")), "rel": lead["_rel"],
                    "hold": lead["hold"]})
    return out

def boot(plays, n=2000, lo=5, hi=95):
    if not plays: return (None, None)
    by = defaultdict(list)
    for p in plays: by[p["slug"]].append(p["flat"])
    keys = list(by); rng = random.Random(7); outs = []
    for _ in range(n):
        s = []
        for _ in range(len(keys)): s += by[rng.choice(keys)]
        if s: outs.append(sum(s)/len(s)*100)
    outs.sort()
    return (outs[int(len(outs)*lo/100)], outs[int(len(outs)*hi/100)])

def report(name, plays):
    if not plays:
        print(f"{name:44} 0 plays"); return
    roi = sum(p["flat"] for p in plays)/len(plays)*100
    w = sum(1 for p in plays if p["won"])
    lo, hi = boot(plays)
    cl = [p["clv_entry"] for p in plays if p["clv_entry"] is not None]
    clv = sum(cl)/len(cl)*100 if cl else float("nan")
    print(f"{name:44} {len(plays):>5} plays {len(set(p['slug'] for p in plays)):>5} games "
          f"{w}-{len(plays)-w:<9} ROI {roi:>7.2f}%  90% CI [{lo:>6.2f}, {hi:>6.2f}]  "
          f"signal CLV {clv:>+6.2f}")

if __name__ == "__main__":
    rows = add_career(load())
    print(f"corpus: {len(rows):,} rows · "
          f"{sum(1 for r in rows if r.get('_rel') is not None):,} with a trusted career median\n")

    base = [r for r in rows if r.get("_rel") is not None and flat(r) is not None
            and not r.get("decided") and not r.get("two_sided")
            and (r.get("t_ts") or 0) < (r.get("kickoff_ts") or 0)]
    broi = sum(flat(r) for r in base)/len(base)*100
    print(f"{'BASELINE every pregame row with a median':44} {len(base):>5} rows "
          f"{'':>5}       {'':>14} ROI {broi:>7.2f}%\n")

    print("THE RULE AS PROPOSED (symmetric opposition test)")
    report(f"  hold>3%, >2x median, 2 sharps, no opp", fire(rows))
    print("\nsame, but EXP-027's asymmetric test (any opponent kills it)")
    report(f"  hold>3%, >2x median, 2 sharps, EXP-027", fire(rows, symmetric=False))

    print("\nONE KNOB AT A TIME")
    for h in (1.5, 3.0, 5.0, 10.0):
        report(f"  hold>{h}%  >2x  2 sharps", fire(rows, hold_min=h))
    print()
    for m in (1.0, 2.0, 3.0, 5.0):
        report(f"  hold>3%   >{m}x  2 sharps", fire(rows, mult=m))
    print()
    for nd in (1, 2, 3):
        report(f"  hold>3%   >2x  {nd} sharp(s)", fire(rows, need=nd))
