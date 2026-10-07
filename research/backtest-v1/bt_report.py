#!/usr/bin/env python3
"""
Stage 5 — what the 30-day out-of-sample backtest actually says.

    python3 bt_report.py               # the headline report
    python3 bt_report.py --verbose     # + per-league and drift sweep detail

Every number here is bootstrapped by RESAMPLING GAMES, never signals. Legs of
one wallet's stack on one game share a single final score; 47 signals over 20
games with seven losses from one scoreline is 20 outcomes, not 47, and a CI
computed on 47 is roughly 1.5x too tight. Each block prints both counts so the
difference stays visible.

Three things this is built to be able to say, including the boring one:
  SUPPORTED     the interval on the trait's effect excludes zero in its favour
  CONTRADICTED  it excludes zero against
  UNDETERMINED  it contains zero — the weight cannot be justified at this size

UNDETERMINED is the expected answer for most traits and is a real result, not a
failure to find one. The v2 weights were set on 31 CLV observations; this is 552
games, and if that still cannot separate them then no forward ledger will do it
in a useful timeframe either.
"""
import json, math, random, sys
from collections import defaultdict

import btlib as B
chk = B.chk

FLAT = 100.0
BOOT = 4000
random.seed(20260917)          # reproducible intervals


def roi(rs):
    rs = [x for x in rs if 0 < (x.get("entry") or 0) < 1 and x.get("won") is not None]
    if not rs:
        return None
    staked = FLAT * len(rs)
    ret = sum(FLAT / x["entry"] for x in rs if x["won"])
    return (ret - staked) / staked * 100


def clv(rs, field="clv"):
    v = [x[field] for x in rs if x.get(field) is not None]
    return (sum(v) / len(v) * 100) if v else None


def by_game(rs):
    g = defaultdict(list)
    for r in rs:
        g[r["slug"]].append(r)
    return g


def boot(rs, stat=roi, n=BOOT):
    """Clustered bootstrap: resample GAMES with replacement, recompute."""
    g = by_game(rs)
    keys = list(g)
    if len(keys) < 5:
        return None
    out = []
    for _ in range(n):
        legs = [x for k in (random.choice(keys) for _ in keys) for x in g[k]]
        v = stat(legs)
        if v is not None:
            out.append(v)
    if len(out) < n // 4:
        return None
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


def boot_diff(a, b, stat=roi, n=BOOT):
    """CI on stat(a) - stat(b), resampling games independently in each arm."""
    ga, gb = by_game(a), by_game(b)
    ka, kb = list(ga), list(gb)
    if len(ka) < 5 or len(kb) < 5:
        return None
    out = []
    for _ in range(n):
        la = [x for k in (random.choice(ka) for _ in ka) for x in ga[k]]
        lb = [x for k in (random.choice(kb) for _ in kb) for x in gb[k]]
        va, vb = stat(la), stat(lb)
        if va is not None and vb is not None:
            out.append(va - vb)
    if len(out) < n // 4:
        return None
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


def verdict(ci, favourable_is_positive=True):
    if ci is None:
        return "UNDETERMINED (too few games)"
    lo, hi = ci
    if lo <= 0 <= hi:
        return "UNDETERMINED"
    good = lo > 0 if favourable_is_positive else hi < 0
    return "SUPPORTED" if good else "CONTRADICTED"


def line(label, rs, show_n=True):
    if not rs:
        print(f"  {label:22}        —")
        return
    w = sum(1 for x in rs if x["won"])
    games = len({x["slug"] for x in rs})
    indep = len({(x["wallet"], x["slug"]) for x in rs})
    r, c = roi(rs), clv(rs)
    s = (f"  {label:22} {w:>4}/{len(rs):<4} {w/len(rs)*100:>5.1f}%  "
         f"ROI {r:>+7.1f}%  CLV {(c if c is not None else 0):>+6.2f}")
    if show_n:
        s += f"  {indep:>4} indep / {games:>3} games"
    print(s)


def main():
    verbose = "--verbose" in sys.argv
    rows = B.load_jsonl("bt_scored.jsonl")
    meta = B.load("bt_score_meta.json")
    pool = B.load("bt_pool.json")

    oos = [r for r in rows if not r["in_sample"]]
    q = [r for r in oos if r["qualifies"]]
    games = len({r["slug"] for r in q})
    indep = len({(r["wallet"], r["slug"]) for r in q})

    print("=" * 86)
    print("OUT-OF-SAMPLE BACKTEST · 30 days · positions rebuilt at T = kickoff-60m")
    print("=" * 86)
    print(f"model {chk.MODEL_VERSION} · pool selected on PRIOR record (ALL minus MONTH), "
          f"bars {chk.MIN_HOLD}% hold / ${chk.MIN_PNL:,}")
    print(f"{meta['n_in']:,} positions rebuilt · {len(oos):,} scored out-of-sample · "
          f"{len(q):,} clear the live gate")
    print(f"{games} distinct games · {indep} independent wallet-game outcomes")
    print(f"(a further {meta['in_sample_setaside']:,} positions came from the MONTH/PNL "
          f"discovery slice and are\nexcluded — those wallets were chosen ON this "
          f"window's P/L and cannot test anything.)")

    print("\n" + "-" * 86)
    print("HEADLINE")
    print("-" * 86)
    line("ALL QUALIFYING", q)
    ci = boot(q)
    r = roi(q)
    if ci:
        print(f"\n  Flat-$100 ROI {r:+.1f}%   95% CI [{ci[0]:+.1f}%, {ci[1]:+.1f}%]  "
              f"(bootstrap over {games} games)")
        if ci[0] <= 0 <= ci[1]:
            print("  -> The interval CONTAINS ZERO. At 552 games the gate as a whole "
                  "shows no\n     measurable edge. That is the single most important "
                  "line in this report.")
        elif ci[0] > 0:
            print("  -> Excludes zero on the upside: a real edge at this sample size.")
        else:
            print("  -> Excludes zero on the DOWNSIDE: these signals lost more than "
                  "noise explains.")
    c = clv(q)
    if c is not None:
        print(f"  CLV vs price at T: {c:+.2f} pts   "
              + ("(market moved toward these positions)" if c > 0
                 else "(market moved AWAY after T)"))

    print("\n" + "-" * 86)
    print("BY CONVICTION TIER   — does the score order outcomes at all?")
    print("-" * 86)
    tiers = [("80-100", 80, 101), ("60-80", 60, 80), ("40-60", 40, 60), ("0-40", 0, 40)]
    for lab, lo, hi in tiers:
        line(lab, [x for x in q if lo <= x["conv"] < hi])

    print("\n" + "-" * 86)
    print("BY TRAIT")
    print("-" * 86)
    traits = [
        ("stacked (x2+)",    lambda x: x.get("stack", 1) >= chk.STACK_MIN),
        ("single market",    lambda x: x.get("stack", 1) < chk.STACK_MIN),
        ("underdog entry",   lambda x: x.get("stack_dog", x["dog"])),
        ("favourite entry",  lambda x: not x.get("stack_dog", x["dog"])),
        ("fresh entry",      lambda x: x["fresh"]),
        ("stale entry",      lambda x: not x["fresh"]),
        ("size >= 2x median", lambda x: (x.get("vs_typical") or 0) >= 2),
        ("size < 2x median", lambda x: 0 < (x.get("vs_typical") or 0) < 2),
        ("hold >= 3%",       lambda x: (x.get("hold") or 0) >= 3),
        ("hold < 3%",        lambda x: (x.get("hold") or 0) < 3),
    ]
    for lab, fn in traits:
        line(lab, [x for x in q if fn(x)])

    print("\n" + "=" * 86)
    print("THE v2 MULTIPLIERS — supported, contradicted, or undetermined?")
    print("=" * 86)
    print("v2 set stack 1.35 -> 1.15 and dog 1.20 -> 1.45, on 31 CLV observations.")
    print("Each row is the DIFFERENCE between having the trait and not, with a")
    print("game-clustered 95% interval. A multiplier above 1.0 is only justified")
    print("if that difference excludes zero in its favour.\n")
    tests = [
        ("stack x2+  vs single", lambda x: x.get("stack", 1) >= chk.STACK_MIN, 1.15),
        ("underdog   vs favourite", lambda x: x.get("stack_dog", x["dog"]), 1.45),
        ("fresh      vs stale", lambda x: x["fresh"], 1.10),
    ]
    print(f"  {'TEST':26} {'dROI':>9}  {'95% CI':>22}  {'dCLV':>7}  VERDICT (mult)")
    for lab, fn, mult in tests:
        a = [x for x in q if fn(x)]
        b = [x for x in q if not fn(x)]
        d = (roi(a) - roi(b)) if (roi(a) is not None and roi(b) is not None) else None
        ci = boot_diff(a, b)
        dc = ((clv(a) or 0) - (clv(b) or 0))
        cis = f"[{ci[0]:+.1f}%, {ci[1]:+.1f}%]" if ci else "—"
        print(f"  {lab:26} {(f'{d:+.1f}%' if d is not None else '—'):>9}  {cis:>22}  "
              f"{dc:>+6.2f}  {verdict(ci)} ({mult})")

    print("\n" + "=" * 86)
    print("PRE-GAME DRIFT FILTER   — doc §7: 'may be worth more than any trait weight'")
    print("=" * 86)
    print("Drift = how far the price moved AGAINST the position between T and kickoff.")
    print("Each row keeps only positions whose adverse drift stayed under the cut.\n")
    print(f"  {'CUT':>10} {'KEPT':>7} {'GAMES':>7} {'ROI':>9} {'95% CI':>22}  CLV")
    base = q
    for cut in (None, 0.10, 0.07, 0.05, 0.03, 0.02):
        s = base if cut is None else [x for x in base
                                      if x.get("drift") is not None and x["drift"] <= cut]
        if not s:
            continue
        r2, c2 = roi(s), clv(s)
        ci2 = boot(s)
        cis = f"[{ci2[0]:+.1f}%, {ci2[1]:+.1f}%]" if ci2 else "—"
        lab = "no filter" if cut is None else f"<= {cut*100:.0f} pts"
        print(f"  {lab:>10} {len(s):>7} {len({x['slug'] for x in s}):>7} "
              f"{(r2 if r2 is not None else 0):>+8.1f}% {cis:>22}  "
              f"{(c2 if c2 is not None else 0):>+6.2f}")

    print("\n" + "=" * 86)
    print("IN-SAMPLE BLOCK (labelled, not evidence)")
    print("=" * 86)
    ins = [r for r in rows if r["in_sample"] and r["qualifies"]]
    if ins:
        line("MONTH/PNL discovery", ins)
        print("  These wallets were selected BECAUSE they made money in this window.")
        print("  Any edge shown here is circular by construction. Reported for contrast.")
    else:
        print("  none scored (discovery wallets have no prior record to gate on)")

    print("\n" + "=" * 86)
    print("WHAT WOULD BIAS THIS UPWARD")
    print("=" * 86)
    for b in pool.get("biases", []):
        print(f"  * {b}")
    print(f"  * leagues are not balanced: "
          + ", ".join(f"{k} {v}" for k, v in sorted(
              ((k, sum(1 for x in q if x['league'] == k)) for k in {x['league'] for x in q}),
              key=lambda kv: -kv[1])[:5]))
    print(f"  * requests: {meta['requests']['ok']:,} ok / {meta['requests']['fail']} failed; "
          f"{meta['price_failures']} tokens had no price history")

    if verbose:
        print("\n" + "-" * 86)
        print("BY LEAGUE")
        print("-" * 86)
        for lg in sorted({x["league"] for x in q}):
            line(lg, [x for x in q if x["league"] == lg])
    return 0


if __name__ == "__main__":
    sys.exit(main())
