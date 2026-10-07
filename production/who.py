#!/usr/bin/env python3
"""
"who is X" — one trader's dossier.

    python3 who.py kch123
    python3 who.py 0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee
    python3 who.py swiss --positions      # also list every open position

bettors.py lists who is on today's slate; this answers the question you actually
ask when the same name keeps appearing on the board — who is this, are they any
good, and are they still good.

The last part is the point. Lifetime hold is a single number covering years, and
it cannot tell a wallet compounding an edge apart from one that made its money in
2024 and has been giving it back since. Splitting lifetime into
`prior = ALL - MONTH` shows both halves separately, so current form is visible
next to the career record instead of averaged into it.

Hold is always taken from the LEADERBOARD's own volume, never recomputed from
summed trades: plenty of high-volume wallets never sell — they buy both sides and
MERGE — so a self-computed hold reads up to ~2x lower and would rank them wrongly.
"""
import json, os, sys, time
from collections import defaultdict

import check as chk

LB = "https://data-api.polymarket.com/v1/leaderboard?category=SPORTS"
LEDGER = "signals.jsonl"


def wait_lock():
    """Yield to any running sweep — never take the lock for a single lookup."""
    t0 = time.time()
    while os.path.exists(chk.SWEEP_LOCK) and time.time() - t0 < 120:
        time.sleep(2)


def find(q):
    """Match a wallet by address, or by case-insensitive name fragment."""
    wait_lock()
    if q.lower().startswith("0x"):
        r = chk.get(f"{LB}&timePeriod=ALL&orderBy=PNL&limit=1&user={q}")
        return [r[0]] if r else []
    hits, seen = [], set()
    for period in ("ALL", "MONTH"):
        for off in range(0, 1000, 50):
            got = chk.get(f"{LB}&timePeriod={period}&orderBy=PNL&limit=50&offset={off}")
            if not got:
                break
            for t in got:
                n = (t.get("userName") or "").lower()
                w = t.get("proxyWallet")
                if q.lower() in n and w not in seen:
                    seen.add(w)
                    hits.append(t)
            if len(got) < 50:
                break
    return hits


def period_row(w, period):
    wait_lock()
    r = chk.get(f"{LB}&timePeriod={period}&orderBy=PNL&limit=1&user={w}")
    if not r:
        return {"pnl": 0.0, "vol": 0.0, "rank": None}
    t = r[0]
    return {"pnl": t.get("pnl") or 0.0, "vol": t.get("vol") or 0.0,
            "rank": t.get("rank"), "name": t.get("userName")}


def hold(p):
    return (p["pnl"] / p["vol"] * 100) if p["vol"] else None


def fmt_hold(h):
    return f"{h:>6.2f}%" if h is not None else "     —"


def ledger_record(wallet, name):
    """What this trader's signals did in OUR ledger."""
    try:
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        return []
    seen, out = set(), []
    for r in rows:
        if r.get("user") not in (name, wallet) and not (
                name and r.get("user", "").startswith(name[:10])):
            continue
        k = (r.get("slug"), r.get("market"), r.get("side"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip().split("\n\n")[1])
        return 1
    q = args[0]
    show_pos = "--positions" in sys.argv

    hits = find(q)
    if not hits:
        print(f"No sports-leaderboard trader matches {q!r}.")
        print("Try the wallet address, or a shorter fragment of the name.")
        return 1
    if len(hits) > 1:
        print(f"{len(hits)} matches for {q!r}:")
        for t in hits[:10]:
            print(f"  {t.get('userName') or '—':24} {t['proxyWallet']}  "
                  f"{chk.m0(t.get('pnl') or 0):>9} lifetime")
        print("\nRe-run with the full name or the wallet address.")
        return 0

    t = hits[0]
    w = t["proxyWallet"]
    name = t.get("userName") or w[:10] + "…"
    allp = period_row(w, "ALL")
    mon = period_row(w, "MONTH")
    prior = {"pnl": allp["pnl"] - mon["pnl"], "vol": allp["vol"] - mon["vol"]}

    print(f"{name}   {w}")
    print(f"rank #{allp['rank']} all-time sports P/L\n")
    print(f"{'':10} {'P/L':>12} {'VOLUME':>14} {'HOLD':>8}")
    print("-" * 48)
    print(f"{'lifetime':10} {chk.m0(allp['pnl']):>12} {chk.m0(allp['vol']):>14} "
          f"{fmt_hold(hold(allp))}")
    print(f"{'last 30d':10} {chk.m0(mon['pnl']):>12} {chk.m0(mon['vol']):>14} "
          f"{fmt_hold(hold(mon))}")
    print(f"{'prior':10} {chk.m0(prior['pnl']):>12} {chk.m0(prior['vol']):>14} "
          f"{fmt_hold(hold(prior))}")

    ph, mh = hold(prior), hold(mon)
    print()
    if ph is None or mh is None or not mon["vol"]:
        print("No meaningful volume in the last 30 days — this is a career record "
              "with no current form attached.")
    else:
        d = mh - ph
        verdict = ("holding form" if abs(d) < 1 else
                   "BETTER than career" if d > 0 else "WORSE than career")
        print(f"Current form is {verdict} ({mh:+.2f}% vs {ph:+.2f}% prior, "
              f"{d:+.2f} pts).")
        if mon["vol"] < allp["vol"] * 0.02:
            print("Caveat: the last 30 days is a thin slice of their volume — that "
                  "hold is noisy.")

    bars = []
    if hold(allp) is not None and hold(allp) >= chk.MIN_HOLD:
        bars.append(f"hold >= {chk.MIN_HOLD}%")
    if allp["pnl"] >= chk.MIN_PNL:
        bars.append(f"P/L >= {chk.m0(chk.MIN_PNL)}")
    print(f"\nClears {len(bars)}/2 quality bars" + (f" ({', '.join(bars)})" if bars else ""))

    wait_lock()
    ps = chk.get(f"https://data-api.polymarket.com/positions?user={w}"
                 f"&limit=100&sortBy=CURRENT&sortDirection=DESC")
    if isinstance(ps, list):
        open_ps = [p for p in ps if (p.get("currentValue") or 0) > 0]
        sizes = sorted((p.get("initialValue") or 0) for p in open_ps
                       if (p.get("initialValue") or 0) > 0)
        med = sizes[len(sizes) // 2] if sizes else 0
        print(f"\n{len(open_ps)} open positions · median size {chk.m0(med)} "
              f"· largest {chk.m0(sizes[-1]) if sizes else '—'}")
        if show_pos and open_ps:
            print()
            for p in sorted(open_ps, key=lambda x: -(x.get("initialValue") or 0))[:20]:
                print(f"  {chk.m0(p.get('initialValue') or 0):>10} "
                      f"{chk.am(p.get('avgPrice') or 0):>7}  "
                      f"{(p.get('outcome') or '')[:20]:22} {(p.get('title') or '')[:44]}")
    else:
        print("\n!! position lookup FAILED — open positions unknown, not zero")

    sigs = ledger_record(w, t.get("userName"))
    if sigs:
        print(f"\nIn our ledger: {len(sigs)} distinct signals across "
              f"{len({s['slug'] for s in sigs})} games")
        for s in sorted(sigs, key=lambda x: -(x.get("conv") or 0))[:5]:
            print(f"  [{s.get('conv',0):>4.1f}] {chk.m0(s['stake']):>9} "
                  f"{s['side'][:20]:22} {s.get('league',''):6} {s['game'][:30]}")
    else:
        print("\nNot in our signal ledger yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
