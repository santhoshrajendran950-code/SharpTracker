#!/usr/bin/env python3
"""
Post-hoc gates on a scored window: how many alerts / picks, and how they did,
under several definitions of "a sharp wallet".

    python3 hist_cuts.py 2025-10 [2025-11 ...]

Every wallet with a prior was scored (hist_adapt). Here each CUT re-selects
wallets from prior.json, then counts, per month:

  alerts  pre-game rows from gate wallets with frozen-v4 conv >= 65
  picks   ... conv >= 70 inside the -122..-180 window (what would have published)

and grades both at the T-60 price + 1.5pt vig, flat and s5-sized.
"""
import json, os, random, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import units as U

VIG = 0.015
CUTS = {
    "A  hold>=1.5%, 90d P/L>=$50K, vol>=$500K":      lambda p: p["hold"] >= 1.5 and p["pnl"] >= 50e3 and p["vol"] >= 500e3,
    "B  hold>=1.5%, 90d P/L>=$100K":                  lambda p: p["hold"] >= 1.5 and p["pnl"] >= 100e3,
    "C  hold>=3%,   90d P/L>=$50K, >=30 settled":     lambda p: p["hold"] >= 3 and p["pnl"] >= 50e3 and p.get("n_settled", 0) >= 30,
    "D  hold>=3%,   90d P/L>=$100K, >=50 settled":    lambda p: p["hold"] >= 3 and p["pnl"] >= 100e3 and p.get("n_settled", 0) >= 50,
    "E  hold>=5%,   90d P/L>=$100K, >=50 settled":    lambda p: p["hold"] >= 5 and p["pnl"] >= 100e3 and p.get("n_settled", 0) >= 50,
}


def roi(bets):
    risk = sum(u for _, u in bets)
    return (sum((u * (1 / r["q"] - 1) if r["won"] else -u) for r, u in bets) / risk * 100) if risk else float("nan")


def ci(bets, B=1000):
    g = defaultdict(list)
    for r, u in bets:
        g[r["slug"]].append((r, u))
    ks = list(g)
    if len(ks) < 8:
        return ""
    random.seed(1)
    o = sorted(roi([x for k in random.choices(ks, k=len(ks)) for x in g[k]]) for _ in range(B))
    return f"[{o[int(.05*B)]:+.0f}, {o[int(.95*B)]:+.0f}]"


def avg_clv(rs):
    c = [r["clv"] for r in rs if r.get("clv") is not None]
    return (sum(c) / len(c)) if c else None


def main():
    for spec in sys.argv[1:]:
        d = os.path.join(HERE, "windows", *spec.split("/"))
        try:
            rows = [json.loads(l) for l in open(os.path.join(d, "bt_scored.jsonl"))]
            prior = json.load(open(os.path.join(d, "prior.json")))
        except FileNotFoundError:
            print(f"{spec}: not scored yet")
            continue
        meta = {}
        mpath = os.path.join(d, "market_meta.json")
        if os.path.exists(mpath):
            meta = json.load(open(mpath))
        rows = [r for r in rows if r.get("won") is not None and 0 < (r.get("now") or 0) < 1 and not r.get("two_sided") and not r.get("decided")]
        for r in rows:
            r["q"] = min(r["now"] + VIG, .985)
            m = meta.get(f"{r.get('slug')}|||{r.get('market')}")
            r["market_type"] = m["market_type"] if m else ("total" if "O/U" in (r.get("market") or "") else
                                                            "spread" if "Spread" in (r.get("market") or "") else "moneyline")
        print(f"\n{spec} · {len(rows)} scored pre-game positions · {len({r['slug'] for r in rows})} games · "
              f"{len({r['wallet'] for r in rows})} wallets")
        print(f"  {'cut':48} {'wallets':>7} {'alerts':>6} {'picks':>5} {'W-L':>7} {'flat':>7} {'90% CI':>12} "
              f"{'s5':>7} {'risked':>6} {'CLV':>7}")
        for name, f in CUTS.items():
            ws = {w for w, p in prior.items() if not p.get("partial") and f(p)}
            al = [r for r in rows if r["wallet"] in ws and r["conv"] >= 65]
            pk = [r for r in al if r["conv"] >= 70 and .55 <= r["now"] <= .643]
            if not pk:
                print(f"  {name:48} {len(ws):7} {len(al):6} {len(pk):5}     —")
                continue
            flat = [(r, 1.0) for r in pk]
            sized = [(r, U.recommend(dict(r, user=None))[0] or 1.0) for r in pk]
            w = sum(bool(r["won"]) for r in pk)
            clv = avg_clv(pk)
            print(f"  {name:48} {len(ws):7} {len(al):6} {len(pk):5} {w:3}-{len(pk)-w:<3} "
                  f"{roi(flat):+6.1f}% {ci(flat):>12} {roi(sized):+6.1f}% {sum(u for _, u in sized):6.0f}u "
                  f"{(f'{clv*100:+.2f}' if clv is not None else '—'):>7}")
            by_mt = defaultdict(list)
            for r in pk:
                by_mt[r["market_type"]].append(r)
            if len(by_mt) > 1:
                for mt, rs in sorted(by_mt.items()):
                    ww = sum(bool(r["won"]) for r in rs)
                    c = avg_clv(rs)
                    print(f"      {mt:15} {len(rs):5} {ww:3}-{len(rs)-ww:<3} win {ww/len(rs):4.0%}  "
                          f"ROI {roi([(r, 1.0) for r in rs]):+6.1f}%  CLV {(f'{c*100:+.2f}' if c is not None else '—'):>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
