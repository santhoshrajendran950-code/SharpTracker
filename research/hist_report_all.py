#!/usr/bin/env python3
"""
The final table: every completed window (any sport, any month), broken out
by sport x gate x market_type — hit rate, ROI (flat and s5-sized), avg CLV.

    python3 hist_report_all.py             # every window that has bt_scored.jsonl
    python3 hist_report_all.py mlb nfl      # just these sports (+ 'football' for
                                            # the legacy windows/<ym>/ NFL/CFB run)
"""
import glob, json, os, random, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import units as U
from hist_cuts import CUTS, roi, ci, VIG, avg_clv


def find_windows(want):
    out = []   # (sport_label, ym, dir)
    for ym_dir in sorted(glob.glob(os.path.join(HERE, "windows", "20*"))):
        if os.path.isdir(os.path.join(ym_dir, ".")) and os.path.exists(os.path.join(ym_dir, "bt_scored.jsonl")):
            ym = os.path.basename(ym_dir)
            if not want or "football" in want:
                out.append(("FOOTBALL", ym, ym_dir))
    for sport_dir in sorted(glob.glob(os.path.join(HERE, "windows", "*"))):
        sport = os.path.basename(sport_dir)
        if sport.startswith("20") or not os.path.isdir(sport_dir):
            continue
        if want and sport not in want:
            continue
        for ym_dir in sorted(glob.glob(os.path.join(sport_dir, "*"))):
            if os.path.exists(os.path.join(ym_dir, "bt_scored.jsonl")):
                out.append((sport.upper(), os.path.basename(ym_dir), ym_dir))
    return out


def load_window(d):
    rows = [json.loads(l) for l in open(os.path.join(d, "bt_scored.jsonl"))]
    prior = json.load(open(os.path.join(d, "prior.json")))
    meta = {}
    mp = os.path.join(d, "market_meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp))
    rows = [r for r in rows if r.get("won") is not None and 0 < (r.get("now") or 0) < 1
            and not r.get("two_sided") and not r.get("decided")]
    for r in rows:
        r["q"] = min(r["now"] + VIG, .985)
        m = meta.get(f"{r.get('slug')}|||{r.get('market')}")
        r["market_type"] = m["market_type"] if m else ("total" if "O/U" in (r.get("market") or "") else
                                                        "spread" if "Spread" in (r.get("market") or "") else "moneyline")
    return rows, prior


def main():
    want = set(sys.argv[1:])
    windows = find_windows(want)
    if not windows:
        print("no scored windows found yet")
        return 0
    print(f"{len(windows)} scored windows: " + ", ".join(f"{s}/{ym}" for s, ym, _ in windows))

    # pool every window's picks per (sport, cut, market_type)
    cells = defaultdict(list)   # (sport, cut_name, market_type) -> [(row, unit)]
    for sport, ym, d in windows:
        rows, prior = load_window(d)
        for name, f in CUTS.items():
            ws = {w for w, p in prior.items() if not p.get("partial") and f(p)}
            pk = [r for r in rows if r["wallet"] in ws and r["conv"] >= 70 and .55 <= r["now"] <= .643]
            for r in pk:
                u = U.recommend(dict(r, user=None))[0] or 1.0
                cells[(sport, name, r["market_type"])].append((r, u))
                cells[(sport, name, "ALL")].append((r, u))
                cells[("ALL SPORTS", name, "ALL")].append((r, u))

    print(f"\n{'sport':12} {'cut':48} {'type':10} {'n':>5} {'W-L':>8} {'hit%':>6} "
          f"{'flat ROI':>9} {'90% CI':>14} {'s5 ROI':>8} {'avg CLV':>8}")
    for (sport, cut, mt), bets in sorted(cells.items()):
        if mt != "ALL" and sport == "ALL SPORTS":
            continue
        rs = [r for r, _ in bets]
        if not rs:
            continue
        flat = [(r, 1.0) for r in rs]
        w = sum(bool(r["won"]) for r in rs)
        c = avg_clv(rs)
        print(f"{sport:12} {cut:48} {mt:10} {len(rs):5} {w:3}-{len(rs)-w:<4} {w/len(rs):5.0%} "
              f"{roi(flat):+8.1f}% {ci(flat):>14} {roi(bets):+7.1f}% "
              f"{(f'{c*100:+.2f}' if c is not None else '—'):>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
