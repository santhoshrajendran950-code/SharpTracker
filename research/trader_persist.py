#!/usr/bin/env python3
"""
Do per-trader edges persist? The question behind every trusted.json override.

Pools every scored position in the research windows (99 sport-months, T-60
price, outcome, close) plus the live signal ledger, keyed by trader name.
Then, for every trader with enough history, splits their record
chronologically in half and asks whether the first half predicts the second:
  - rank correlation of flat ROI across traders, half 1 vs half 2
  - the same inside each price band (the override question)
  - how the traders who would have been "licensed" on half 1 did in half 2
  - shrinkage: how much a trader's own record should move us off the pool rate
Prints the named traders' band-by-band, half-by-half records at the end.

    python3 research/trader_persist.py [--min 60] [--names a,b,c]
Read-only.
"""
import glob, json, os, sys, random
from collections import defaultdict
from datetime import datetime

RES = os.path.expanduser("~/nl-research/windows")
LIVE = os.path.expanduser("~/.nightly-line")
VIG = 0.015
MIN = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 60
NAMES = (sys.argv[sys.argv.index("--names") + 1].split(",") if "--names" in sys.argv
         else ["Kch-Temp", "justaluckydude", "CandleHammerDrums", "BrotherObama", "Soarin22", "primm", "betdge", "Talvez10"])


def band(p):
    return ("dog" if p < .50 else "pickem" if p < .55 else "window" if p <= .643 else "short" if p <= .75 else "vshort")


def grade(price, won):
    q = min(.99, price + VIG)
    return (1 - q) / q if won else -1.0


def load():
    rows = []
    for f in glob.glob(os.path.join(RES, "**", "bt_scored.jsonl"), recursive=True):
        for l in open(f):
            r = json.loads(l)
            if r.get("won") is None or not r.get("user"): continue
            px = r.get("now") or r.get("entry")
            if not px or not (0 < px < 1): continue
            rows.append(dict(user=r["user"], t=r.get("t_ts") or r.get("kickoff_ts") or 0, px=px,
                             won=bool(r["won"]), clv=((r["close"] - r["entry"]) * 100 if r.get("close") and r.get("entry") else None),
                             league=r.get("league"), src="research"))
    seen = {}
    for f in sorted(glob.glob(os.path.join(LIVE, "ledgers", "*.json"))):
        for s in json.load(open(f)).get("signals", []):
            k = (s["slug"], s["market"], s["side"], s["user"])
            if k in seen or s.get("won") is None: continue
            seen[k] = 1
            try: t = int(datetime.fromisoformat(s["start"]).timestamp())
            except Exception: t = 0
            rows.append(dict(user=s["user"], t=t, px=s["entry"], won=bool(s["won"]),
                             clv=(s["clv"] * 100 if s.get("clv") is not None else None), league=s.get("league"), src="live"))
    for r in rows: r["pl"] = grade(r["px"], r["won"]); r["band"] = band(r["px"])
    return rows


def roi(rs): return 100 * sum(r["pl"] for r in rs) / len(rs) if rs else None
def clv(rs):
    c = [r["clv"] for r in rs if r["clv"] is not None]; return sum(c) / len(c) if c else None


def spearman(a, b):
    n = len(a)
    if n < 5: return None
    def rk(x):
        s = sorted(range(n), key=lambda i: x[i]); r = [0] * n
        for pos, i in enumerate(s): r[i] = pos
        return r
    ra, rb = rk(a), rk(b)
    return 1 - 6 * sum((ra[i] - rb[i]) ** 2 for i in range(n)) / (n * (n * n - 1))


def halves(rs):
    rs = sorted(rs, key=lambda r: r["t"]); m = len(rs) // 2
    return rs[:m], rs[m:]


def main():
    rows = load()
    by = defaultdict(list)
    for r in rows: by[r["user"]].append(r)
    print(f"{len(rows)} settled positions, {len(by)} traders; research {sum(1 for r in rows if r['src']=='research')}, live {sum(1 for r in rows if r['src']=='live')}")
    print(f"pool flat ROI {roi(rows):+.1f}%, CLV {clv(rows):+.2f}\n")

    # 1. persistence overall
    eligible = {u: rs for u, rs in by.items() if len(rs) >= MIN}
    a, b = [], []
    for u, rs in eligible.items():
        h1, h2 = halves(rs); a.append(roi(h1)); b.append(roi(h2))
    print(f"## 1. Does a trader's first-half ROI predict the second? ({len(eligible)} traders with {MIN}+ settled)")
    print(f"Spearman rank correlation, half 1 vs half 2: {spearman(a, b):+.2f}")
    top = sorted(eligible, key=lambda u: -roi(halves(eligible[u])[0]))
    q = max(1, len(top) // 4)
    for label, grp in (("top quartile by half-1 ROI", top[:q]), ("bottom quartile by half-1 ROI", top[-q:])):
        h1 = [r for u in grp for r in halves(eligible[u])[0]]; h2 = [r for u in grp for r in halves(eligible[u])[1]]
        print(f"  {label:32} half1 {roi(h1):+6.1f}% (n={len(h1)})  ->  half2 {roi(h2):+6.1f}% (n={len(h2)})  CLV2 {clv(h2):+.2f}")
    print()

    # 2. persistence inside each band — the override question
    print(f"## 2. Inside a price band (trader x band with {MIN//2}+ in the band)")
    for bd in ("dog", "pickem", "window", "short", "vshort"):
        a, b, lic = [], [], []
        for u, rs in by.items():
            inb = [r for r in rs if r["band"] == bd]
            if len(inb) < MIN // 2: continue
            h1, h2 = halves(inb); a.append(roi(h1)); b.append(roi(h2))
            if roi(h1) > 10 and clv(h1) is not None: lic.append((u, h1, h2))
        pool = [r for r in rows if r["band"] == bd]
        sp = spearman(a, b)
        l2 = [r for _, _, h2 in lic for r in h2]
        print(f"  {bd:7} pool {roi(pool):+6.1f}% (n={len(pool):5}) · {len(a):3} traders · rank corr {('%+.2f' % sp) if sp is not None else '  —  '}"
              f" · 'licensed' on half1 (ROI>+10%): {len(lic):3} traders -> half2 {('%+.1f%%' % roi(l2)) if l2 else '—'} (n={len(l2)})")
    print()

    # 3. shrinkage: how far should n games move us off the pool rate?
    print("## 3. Shrinkage — a trader's record vs the pool, by sample size")
    print("Empirical-Bayes on win rate inside the window band: variance of true trader skill vs sampling noise.")
    inb = defaultdict(list)
    for r in rows:
        if r["band"] == "window": inb[r["user"]].append(r["won"])
    ws = [(sum(v) / len(v), len(v)) for v in inb.values() if len(v) >= 20]
    if ws:
        m = sum(w * n for w, n in ws) / sum(n for _, n in ws)
        obs_var = sum((w - m) ** 2 for w, _ in ws) / len(ws)
        noise = sum(m * (1 - m) / n for _, n in ws) / len(ws)
        tau2 = max(0.0, obs_var - noise)
        k = (m * (1 - m) / tau2) if tau2 > 0 else float("inf")
        print(f"  pool win rate {m:.1%} over {len(ws)} traders; observed spread {obs_var**.5:.3f}, expected noise {noise**.5:.3f}")
        print(f"  implied true-skill spread {tau2**.5:.3f} -> prior strength k = {k:.0f} games"
              + ("  (no detectable skill spread: shrink fully to the pool)" if tau2 == 0 else
                 f"; a trader with n games keeps n/(n+k) of their own deviation: n=30 -> {30/(30+k):.0%}, n=100 -> {100/(100+k):.0%}, n=300 -> {300/(300+k):.0%}"))
    print()

    # 4. the named traders
    print("## 4. Named traders — band x half (all history, chronological)")
    print("| trader | band | n | half1 W-L ROI CLV | half2 W-L ROI CLV | live last 30 |")
    print("|---|---|---:|---|---|---|")
    for u in NAMES:
        rs = by.get(u, [])
        if not rs: print(f"| {u} | — | 0 | no history under this name | | |"); continue
        for bd in ("dog", "pickem", "window", "short", "vshort"):
            inb = [r for r in rs if r["band"] == bd]
            if len(inb) < 10: continue
            h1, h2 = halves(inb)
            def fm(h):
                w = sum(1 for r in h if r["won"]); c = clv(h)
                return f"{w}-{len(h)-w} {roi(h):+.0f}% {('%+.1f' % c) if c is not None else '—'}"
            live = sorted([r for r in inb if r["src"] == "live"], key=lambda r: -r["t"])[:30]
            print(f"| {u} | {bd} | {len(inb)} | {fm(h1)} | {fm(h2)} | {fm(live) if live else '—'} |")


if __name__ == "__main__":
    main()
