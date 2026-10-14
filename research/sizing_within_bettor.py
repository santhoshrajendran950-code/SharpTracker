"""Within-bettor sizing test on win3 (Sep 15 -> Oct 13), the 7 Polycopy-listed sports wallets.
Question (Santhosh, 2026-10-13): does a bettor's stake RELATIVE TO HIS OWN NORM predict his result?
Graded flat 1u at the sharp's entry price + VIG. Game-clustered bootstrap CIs."""
import json, random, collections, math, sys
VIG = 0.015
rows = [json.loads(l) for l in open("bt_scored.jsonl")]
rows = [r for r in rows if r.get("won") is not None and not r.get("decided") and r.get("vs_typical")]
def price(r):
    p = r.get("now")
    p = r["entry"] if p is None else p
    return p + VIG
def pl(r):
    p = price(r)
    if not (0 < p < 1): return None
    return (1 - p) / p if r["won"] else -1.0
def bucket(v): return "<0.5x" if v < .5 else "0.5-1x" if v < 1 else "1-2x" if v < 2 else "2-5x" if v < 5 else "5-15x" if v < 15 else "15x+"
ORDER = ["<0.5x", "0.5-1x", "1-2x", "2-5x", "5-15x", "15x+"]
def roi(rs):
    x = [v for v in map(pl, rs) if v is not None]
    return sum(x) / len(x) * 100 if x else float("nan"), len(x)
def ci(rs, B=2000):
    games = collections.defaultdict(list)
    for r in rs: games[r.get("game") or r.get("slug")].append(r)
    keys = list(games); out = []
    random.seed(7)
    for _ in range(B):
        samp = [g for g in random.choices(keys, k=len(keys))]
        x = [v for g in samp for v in map(pl, games[g]) if v is not None]
        if x: out.append(sum(x) / len(x) * 100)
    out.sort()
    return out[int(len(out) * .025)], out[int(len(out) * .975)]
by = collections.defaultdict(list)
for r in rows: by[r["user"]].append(r)
print(f"win3 rows {len(rows)} · graded at entry + {VIG*100:.1f}pt vig · flat 1u\n")
for u, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
    med = sorted(r["stake"] for r in rs)[len(rs) // 2]
    games = len({r.get("game") for r in rs})
    tot, n = roi(rs)
    print(f"=== {u}: {n} positions · {games} games · median stake ${med:,.0f} · overall {tot:+.1f}%")
    print("  bucket | n | W-L | flat ROI | avg stake")
    bk = collections.defaultdict(list)
    for r in rs: bk[bucket(r["vs_typical"])].append(r)
    for b in ORDER:
        g = bk.get(b)
        if not g: continue
        w = sum(1 for r in g if r["won"]); R, _ = roi(g)
        print(f"  {b:7s}| {len(g):4d} | {w}-{len(g)-w} | {R:+6.1f}% | ${sum(r['stake'] for r in g)/len(g):>9,.0f}")
    big = [r for r in rs if r["vs_typical"] >= 2]; small = [r for r in rs if r["vs_typical"] < 2]
    if len(big) >= 5 and len(small) >= 5:
        rb, nb = roi(big); rsml, ns = roi(small); lo, hi = ci(big)
        print(f"  >=2x own median: {rb:+.1f}% on {nb} [{lo:+.0f}, {hi:+.0f}]  vs  <2x: {rsml:+.1f}% on {ns}")
    print()
# pooled within-bettor rank correlation: does bigger-than-usual go with better P/L?
pairs = [(math.log(r["vs_typical"]), pl(r)) for r in rows if pl(r) is not None]
def spearman(p):
    def rank(xs):
        s = sorted(range(len(xs)), key=lambda i: xs[i]); rk = [0] * len(xs)
        for i, j in enumerate(s): rk[j] = i
        return rk
    a = rank([x for x, _ in p]); b = rank([y for _, y in p]); n = len(p)
    ma = sum(a) / n; mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else 0
print(f"POOLED Spearman(log vs_typical, flat P/L) = {spearman(pairs):+.3f} on {len(pairs)} positions")
for u, rs in by.items():
    p = [(math.log(r["vs_typical"]), pl(r)) for r in rs if pl(r) is not None]
    if len(p) >= 20: print(f"  {u:22s} rho={spearman(p):+.3f} n={len(p)}")
