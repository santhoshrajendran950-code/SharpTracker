#!/usr/bin/env python3
"""
"outliers" — books priced away from the market, right now.

    python3 outliers.py              # current gaps worth acting on
    python3 outliers.py --min 2.0    # only gaps of 2+ points of implied prob
    python3 outliers.py --all        # every line, not just the gaps

This is the mechanical half of the edge: no opinion about who wins, only about
who is mispriced relative to everyone else. myclv.py measured the placed bets at
-0.40 pts of closing-line value, beating the close 18% of the time — so the
weakness is price, and price is the part that can be fixed without predicting
anything.

Consensus is the MEDIAN implied probability across books, not the mean: one book
badly off would otherwise drag the benchmark toward itself and hide the very gap
being looked for. Kalshi and Polymarket are shown but never counted as best,
because they cannot be bet here — a table that names an unbettable venue as the
answer is worse than no table.

Reads the newest poll in lines/. Run linearchive.py first for a fresh view.
"""
import json, os, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import books

OUT = os.path.join(HERE, "lines")
REFS = books.REFS_LABELS


def implied(o):
    return (100.0 / (o + 100.0)) if o > 0 else (-o / (-o + 100.0))


def latest_rows():
    if not os.path.isdir(OUT):
        return []
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".jsonl"))
    if not files:
        return []
    rows = [json.loads(l) for l in open(os.path.join(OUT, files[-1])) if l.strip()]
    if not rows:
        return []
    newest = max(r["t"] for r in rows)
    return [r for r in rows if r["t"] == newest]


def main():
    a = sys.argv[1:]
    show_all = "--all" in a
    floor = float(a[a.index("--min") + 1]) if "--min" in a else 1.0

    rows = latest_rows()
    if not rows:
        print("Nothing archived yet — run linearchive.py first.")
        return 0
    when = datetime.fromtimestamp(rows[0]["t"], timezone.utc).astimezone()

    # group every book's price for one specific outcome
    g = defaultdict(list)
    for r in rows:
        key = (r["gid"], r["mkt"], r["name"], r.get("point"))
        g[key].append(r)

    found = []
    for key, obs in g.items():
        bettable = [o for o in obs if o["book"] not in REFS]
        if len(bettable) < 3:
            continue                       # too thin to call anything an outlier
        probs = [implied(o["price"]) for o in bettable]
        consensus = statistics.median(probs)
        best = min(bettable, key=lambda o: implied(o["price"]))
        gap = (consensus - implied(best["price"])) * 100
        ref = next((o for o in obs if o["book"] in REFS), None)
        if gap >= floor or show_all:
            found.append((gap, best, consensus, ref, obs))

    found.sort(key=lambda x: -x[0])
    print(f"OUTLIERS · {when:%a %-I:%M %p %Z} · {len(g)} line(s) across "
          f"{len({r['gid'] for r in rows})} game(s)")
    print(f"gap = consensus (median) minus the best bettable price, in points "
          f"of implied probability\n")
    if not found:
        print(f"No gaps of {floor:.1f}+ pts. The market is tight right now.")
        return 0

    print(f"{'GAP':>5}  {'BOOK':7} {'PRICE':>6}  {'PLAY':25} {'CONS':>6} {'REF':>10}  GAME")
    for gap, best, cons, ref, obs in found[:25]:
        name = best["name"]
        if best.get("point") is not None:
            name = f"{name} {best['point']:+g}"
        cons_odds = (round(100 * (1 - cons) / cons) if cons < 0.5
                     else -round(100 * cons / (1 - cons)))
        refs = f"{ref['book']} {ref['price']:+d}" if ref else "—"
        # NB: implicit string concatenation binds before subscription, so a
        # trailing [:34] on the last fragment truncates the WHOLE line. Slice
        # the game into its own variable instead.
        game = f"{best['away']} @ {best['home']}"[:32]
        print(f"{gap:>+5.1f}  {best['book']:7} {best['price']:>+6d}  "
              f"{name[:24]:25} {cons_odds:>+6d} {refs:>10}  {game}")
    if len(found) > 25:
        print(f"\n(+{len(found)-25} more below)")
    print(f"\n{len(found)} gap(s) of {floor:.1f}+ pts. A gap is free value only if "
          f"the line is\nreal — always confirm the price is still live before betting it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
