#!/usr/bin/env python3
"""Knob sweep for the proposed X-send rule, with the CORRECTED sharp definition.

A sharp here is a row that passes the v4 gate (hold>=1.5%, prior P/L>=$250k,
stake>=$2,500, entry window, not thin/cold) AND clears the rule's own two bars.
The first run of this test applied only `hold > 3%`, which admitted wallets with
a median lifetime P/L of $82,410 -- 76% of them under the $250k bar. Those were
small winners, not sharps, and every number from that run is void.

48 cells are swept, so the best one is expected to look good by chance. The
decisive column is OOS: the grid is fitted on the earlier 65% of plays by time
and the winner is then scored on the later 35% it never saw.
"""
import sys, random
from collections import defaultdict
sys.path.insert(0, "/Users/santhosh/nl-research")
import xrule as X

HOLDS = [1.5, 3.0, 5.0, 10.0]
MULTS = [1.0, 2.0, 3.0, 5.0]
NEEDS = [2, 3, 4]

def pregame(r):
    return (not r.get("decided") and not r.get("two_sided")
            and (r.get("t_ts") or 0) < (r.get("kickoff_ts") or 0))

def fire(rows, hold_min, mult, need, symmetric=False):
    def q(r):
        return (pregame(r) and r.get("_rel") is not None and r.get("qualifies")
                and (r.get("hold") or -99) > hold_min and r["_rel"] > mult)
    by, mk, v4 = defaultdict(list), defaultdict(set), defaultdict(list)
    for r in rows:
        if not pregame(r): continue
        mk[(r["slug"], r.get("market"))].add(r.get("side"))
        if q(r): by[(r["slug"], r.get("market"), r.get("side"))].append(r)
        if r.get("qualifies"): v4[(r["slug"], r.get("market"), r.get("side"))].append(r)
    out = []
    for (slug, mkt, side), rs in by.items():
        if len(rs) < need: continue
        others = [s for s in mk[(slug, mkt)] if s != side]
        bad = (any(by.get((slug, mkt, s)) for s in others) if symmetric
               else any(v4.get((slug, mkt, s)) for s in others))
        if bad: continue
        rs.sort(key=lambda x: x.get("t_ts") or 0); lead = rs[0]
        f = X.flat(lead)
        if f is None: continue
        out.append({"slug": slug, "flat": f, "won": bool(lead.get("won")),
                    "clv_entry": lead.get("clv_entry"), "t_ts": lead.get("t_ts") or 0,
                    "league": lead.get("league"), "n": len(rs)})
    return out

def roi(ps):  return sum(p["flat"] for p in ps) / len(ps) * 100 if ps else float("nan")
def clv(ps):
    c = [p["clv_entry"] for p in ps if p["clv_entry"] is not None]
    return sum(c) / len(c) * 100 if c else float("nan")

def boot(ps, n=2000):
    if not ps: return (float("nan"),) * 2
    by = defaultdict(list)
    for p in ps: by[p["slug"]].append(p["flat"])
    ks = list(by); rng = random.Random(11); o = []
    for _ in range(n):
        s = []
        for _ in range(len(ks)): s += by[rng.choice(ks)]
        if s: o.append(sum(s) / len(s) * 100)
    o.sort()
    return (o[int(len(o) * .05)], o[int(len(o) * .95)])

if __name__ == "__main__":
    rows = X.add_career(X.load())
    print("SHARP = v4 gate (hold>=1.5%, prior P/L>=$250k, $2.5K+, not thin/cold)")
    print("        + the rule's own hold and career-median bars")
    print("opposition = EXP-027's veto (any v4-qualifying sharp on the other side)\n")
    print(f"{'hold':>6} {'mult':>6} {'need':>5} {'plays':>6} {'games':>6} "
          f"{'ROI':>8} {'90% CI':>18} {'CLV':>7}")
    print("-" * 68)
    cells = {}
    for h in HOLDS:
        for m in MULTS:
            for nd in NEEDS:
                ps = fire(rows, h, m, nd)
                cells[(h, m, nd)] = ps
                if len(ps) < 25:
                    print(f"{h:>6} {m:>6} {nd:>5} {len(ps):>6} "
                          f"{len(set(p['slug'] for p in ps)):>6}    (too few)")
                    continue
                lo, hi = boot(ps)
                print(f"{h:>6} {m:>6} {nd:>5} {len(ps):>6} "
                      f"{len(set(p['slug'] for p in ps)):>6} {roi(ps):>7.2f}% "
                      f"[{lo:>7.2f},{hi:>7.2f}] {clv(ps):>+6.2f}")

    # ---- the honest part: pick on early data, score on late data
    usable = {k: v for k, v in cells.items() if len(v) >= 40}
    allp = sorted({p["t_ts"] for v in usable.values() for p in v})
    cut = allp[int(len(allp) * .65)]
    print(f"\n{'='*68}\nOUT OF SAMPLE  ({len(usable)} cells with >=40 plays; "
          f"split at the 65th pct of play time)")
    best, bscore = None, -1e9
    for k, v in usable.items():
        tr = [p for p in v if p["t_ts"] < cut]
        if len(tr) < 25: continue
        s = roi(tr)
        if s > bscore: best, bscore = k, s
    print(f"  best cell IN SAMPLE: hold>{best[0]}%, >{best[1]}x, {best[2]} sharps "
          f"-> {bscore:+.2f}% on {len([p for p in usable[best] if p['t_ts']<cut])} plays")
    te = [p for p in usable[best] if p["t_ts"] >= cut]
    if te:
        lo, hi = boot(te)
        print(f"  same cell OUT OF SAMPLE: {roi(te):+.2f}% on {len(te)} plays / "
              f"{len(set(p['slug'] for p in te))} games  90% CI [{lo:.2f}, {hi:.2f}]  "
              f"CLV {clv(te):+.2f}")
    # how the proposal itself holds up OOS
    prop = cells[(3.0, 2.0, 2)]
    tr = [p for p in prop if p["t_ts"] < cut]; te = [p for p in prop if p["t_ts"] >= cut]
    print(f"\n  YOUR CELL (hold>3%, >2x, 2 sharps):")
    print(f"    early 65%: {roi(tr):+.2f}% on {len(tr)} plays")
    if te:
        lo, hi = boot(te)
        print(f"    later 35%: {roi(te):+.2f}% on {len(te)} plays  "
              f"90% CI [{lo:.2f}, {hi:.2f}]  CLV {clv(te):+.2f}")
