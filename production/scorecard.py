#!/usr/bin/env python3
"""
"how are we doing" — one rolling health read on the whole signal ledger.

    python3 scorecard.py               # everything logged
    python3 scorecard.py --days 7      # last 7 days of signals
    python3 scorecard.py --model v2    # only signals scored by one model
    python3 scorecard.py --verbose     # per-signal detail

resolve.py answers "did they win", clv.py answers "did the market agree", and
dayledger.py freezes a single day. Nothing answered the question actually worth
asking — *is this working yet* — which needs all three at once plus an honest
statement of how little the sample supports.

Two things this prints that the others do not:

  INDEPENDENT OUTCOMES. Legs of one wallet's stack on one game share a single
  final score. 47 signals over 20 games with seven losses from one scoreline is
  20 outcomes, not 47, and every rate computed on 47 is overconfident. Every
  block here prints both counts.

  A CLUSTERED CONFIDENCE INTERVAL. ROI is bootstrapped by resampling GAMES, not
  signals, so correlated legs travel together the way they actually do. When the
  interval straddles zero the header says so in words, because "+12% ROI" with a
  [-31%, +58%] interval is not a result and should never read like one.

Reads signals.jsonl only. Never reads or prints bets.jsonl — your own stakes stay
out of this, so the output is safe to paste anywhere.
"""
import json, random, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import check as chk
import resolve as R
import clv as C

LEDGER = "signals.jsonl"
FLAT = 100.0
BOOT = 2000


def load(days=None, model=None):
    try:
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        return []
    seen, uniq = set(), []
    for r in rows:
        k = (r.get("slug"), r.get("market"), r.get("side"), r.get("user"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    if model:
        uniq = [r for r in uniq if (r.get("model") or "v1") == model]
    if days:
        cut = datetime.now(timezone.utc) - timedelta(days=days)
        out = []
        for r in uniq:
            try:
                if datetime.fromisoformat(r["logged"]) >= cut:
                    out.append(r)
            except Exception:
                pass
        uniq = out
    return uniq


def boot_roi(rs, n=BOOT):
    """Bootstrap flat-stake ROI, resampling GAMES so correlated legs move as one."""
    by_game = defaultdict(list)
    for r in rs:
        by_game[r["slug"]].append(r)
    games = list(by_game)
    if len(games) < 3:
        return None
    out = []
    for _ in range(n):
        pick = [by_game[random.choice(games)] for _ in games]
        legs = [x for g in pick for x in g]
        staked = FLAT * len(legs)
        if not staked:
            continue
        ret = sum(FLAT / x["entry"] for x in legs if x["won"])
        out.append((ret - staked) / staked * 100)
    if not out:
        return None
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


def roi(rs):
    rs = [x for x in rs if 0 < (x.get("entry") or 0) < 1]
    if not rs:
        return None
    staked = FLAT * len(rs)
    ret = sum(FLAT / x["entry"] for x in rs if x["won"])
    return (ret - staked) / staked * 100


def block(label, rs, show_clv=True):
    if not rs:
        print(f"  {label:18}        —")
        return
    w = sum(1 for x in rs if x["won"])
    games = len({x["slug"] for x in rs})
    indep = len({(x["user"], x["slug"]) for x in rs})
    r = roi(rs)
    cl = [x["clv"] for x in rs if x.get("clv") is not None]
    line = (f"  {label:18} {w:>3}/{len(rs):<3} {w/len(rs)*100:>5.1f}%   "
            f"ROI {r:>+7.1f}%   {indep:>3} indep / {games:>3} games")
    if show_clv and cl:
        line += f"   CLV {sum(cl)/len(cl)*100:>+6.2f}"
    print(line)


def main():
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else None
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    verbose = "--verbose" in sys.argv

    sigs = load(days, model)
    if not sigs:
        print("No signals in the ledger for that filter.")
        return 0

    # resolve.py caches {"closed":…, "markets":…} per slug; clv.py caches the raw
    # event dict under the same key. Sharing one dict makes whichever runs second
    # read the other's shape — separate caches, deliberately.
    rcache, ccache, hcache, scored, pending = {}, {}, {}, [], 0
    for r in sigs:
        won = R.outcome_for(r["slug"], r["market"], r["side"], rcache)
        if won is None:
            pending += 1
            continue
        r["won"] = won
        entry = r.get("entry") or 0
        if 0 < entry < 1 and r.get("start"):
            tok = C.token_for(r["slug"], r["market"], r["side"], ccache)
            close = C.closing_price(tok, r["start"], hcache) if tok else None
            r["close"] = close
            r["clv"] = (close - entry) if close is not None else None
        scored.append(r)

    scope = (f"last {days}d" if days else "all time") + (f" · model {model}" if model else "")
    print(f"SCORECARD · {scope} · {len(sigs)} distinct signals · "
          f"{len(scored)} settled · {pending} pending\n")
    if not scored:
        print("Nothing settled yet.")
        return 0

    good = [x for x in scored if 0 < (x.get("entry") or 0) < 1]
    games = len({x["slug"] for x in good})
    indep = len({(x["user"], x["slug"]) for x in good})
    r = roi(good)
    ci = boot_roi(good)

    print(f"{'':18} {'RECORD':>11}   {'FLAT-$100':>12}   {'SAMPLE':>18}   CLV")
    print("-" * 82)
    block("OVERALL", good)
    print()
    print("BY MODEL VERSION")
    for m in sorted({(x.get("model") or "v1") for x in good}):
        block(m, [x for x in good if (x.get("model") or "v1") == m])
    print("\nBY TIER")
    for t in ("CONVICTION", "SIZE", "EDGE", "BOARD"):
        block(t, [x for x in good if x.get("why") == t])
    print("\nBY TRAIT")
    for label, fn in [
        ("stacked (x2+)", lambda x: x.get("stack", 1) >= 2),
        ("single market", lambda x: x.get("stack", 1) < 2),
        ("underdog entry", lambda x: x.get("dog")),
        ("favourite entry", lambda x: not x.get("dog")),
        ("fresh entry", lambda x: x.get("fresh")),
        ("hold >= 3%", lambda x: (x.get("hold") or 0) >= 3),
        ("hold < 3%", lambda x: (x.get("hold") or 0) < 3),
    ]:
        block(label, [x for x in good if fn(x)])

    print("\n" + "=" * 82)
    if ci:
        lo, hi = ci
        print(f"Flat-$100 ROI {r:+.1f}%  ·  95% CI [{lo:+.1f}%, {hi:+.1f}%] "
              f"(bootstrap over {games} games, clustered)")
        if lo <= 0 <= hi:
            print("\nThat interval CONTAINS ZERO. There is no measured edge here yet — "
                  "\nthe point estimate is what noise looks like at this sample size.")
        elif lo > 0:
            print("\nThe interval excludes zero on the upside. Real, at this sample, "
                  "\nfor this model version — keep collecting before changing weights.")
        else:
            print("\nThe interval excludes zero on the DOWNSIDE. These signals lost "
                  "\nmoney by more than noise explains. Investigate before trusting them.")
    else:
        print(f"Flat-$100 ROI {r:+.1f}% · too few distinct games ({games}) to bootstrap.")

    print(f"\n{len(good)} settled legs are only {indep} independent wallet-game "
          f"outcomes across {games} games.")
    if games < 30:
        print(f"{games} games cannot separate skill from variance. Treat every rate "
              f"above as provisional.")

    if verbose:
        print("\nDETAIL (worst to best by CLV)")
        for x in sorted(good, key=lambda y: (y.get("clv") is None, y.get("clv") or 0)):
            c = f"{x['clv']*100:>+6.2f}" if x.get("clv") is not None else "     —"
            print(f"  {'WON ' if x['won'] else 'LOST'} {c}  {chk.m0(x['stake']):>9} "
                  f"{x['side'][:20]:22} {x.get('league',''):6} {x['game'][:34]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
