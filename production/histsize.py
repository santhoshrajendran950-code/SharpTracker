#!/usr/bin/env python3
"""
The wager-vs-history sizing system: full feature vector, data-fitted weights.

Santhosh (2026-10-20): "I need this to be an exhaustive system capable to size
bets appropriately based on wager vs user history."

So this computes EVERY reading of "how big is this bet for this bettor" that
`wallethist.py`'s career store can support, and turns them into a size
multiplier through a calibration table that is FIT FROM OUTCOMES, never asserted:

    rel_med      stake / career median        p90 of the store's own medians
    rel_mean     stake / career mean          exact (n and total are kept)
    rel_p90      stake / career p90           vs their big-bet habit
    pctile       career rank of this stake    scale-free, skew-immune
    z_log        (log stake - mu) / sd        standardised by their own spread
    is_max       beats their career largest
    rel_recent   stake / median(last 50)      vs current form, not career
    ramp         median(last 50) / career med are they sizing up lately
    skew         mean / median                their style, not this bet
    n_hist       career wagers                experience itself

WHAT THE FIT SAYS TODAY, and it matters more than the machinery. `histfeat.py`
screened all ten features plus conjunctions over 106,293 bets (12,998 games,
2,228 wallets), chronologically split, Bonferroni-adjusted:

  * Every Spearman against flat unit P/L falls in [-0.014, +0.013].
  * On EXPERIENCED wallets (n_hist >= 100, 68,242 bets) NOTHING survives.
  * Across the whole space the extremes land -1.7% to -2.8% against a -2.3%
    baseline: a spread of about one point, with every CI overlapping.
  * Conjunctions: big vs BOTH centres >= 2x is -2.25%, >= 5x is -1.87%,
    neither is -2.75%. Faintly monotone, entirely inside the noise.

So the fitted multipliers come out at or near 1.00 and this system, today, sizes
flat. That is the honest output of the measurement, not a placeholder. The point
of building it anyway is that the table is DATA, re-fittable with `--fit` as the
windows grow: if a bin ever separates, sizing follows it without anyone
rewriting a ladder from intuition. `TABLE_PATH` is the fitted artefact.

SHRINKAGE. A bin's measured ROI is mostly noise, so the multiplier is
    mult = 1 + SHRINK * (bin_roi - baseline) / SCALE,  clipped to [MULT_MIN, MULT_MAX]
with SHRINK = n/(n+PRIOR_N) per bin. A bin of 30 bets barely moves; a bin of
20,000 moves most of the way. This is the same estimator units.py uses on CLV.

  histsize.py --fit            re-fit the table from the research windows
  histsize.py --show           print the fitted table
  histsize.py --explain NAME STAKE   what the system would do with one wager
"""
import json
import math
import os
import sys

HOME = os.path.expanduser("~")
LIVE = os.path.join(HOME, ".nightly-line")
TABLE_PATH = os.path.join(LIVE, "histsize.json")

FEATURES = ["rel_med", "rel_mean", "rel_p90", "pctile", "z_log", "is_max",
            "rel_recent", "ramp", "skew", "n_hist"]

PRIOR_N = 2000      # per-bin shrinkage strength. Deliberately heavy: the screen
                    # says these bins do not separate, so a bin must be large
                    # before it is allowed to move the stake at all.
SCALE = 10.0        # ROI points that would earn a full step of multiplier
SHRINK_CAP = 1.0
MULT_MIN, MULT_MAX = 0.5, 2.0
NBINS = 5


def features(key, stake, store=None):
    """The whole feature vector for one wager, from the career store.

    Returns None when the wallet has no usable history -- callers must treat
    that as unknown, never as average.
    """
    import wallethist as WH
    st = store if store is not None else WH._store()
    w = WH.find(key, st)
    if not w or not (w.get("n") or 0):
        return None
    n = w["n"]
    med = WH.quantile(w, 0.5)
    p90 = WH.quantile(w, 0.90)
    mean = w["total"] / n if n else None
    if not med or not mean:
        return None
    mu, sd = WH.log_moments(w)
    rec = w.get("recent") or []
    rmed = None
    if rec:
        s = sorted(rec)
        rmed = s[len(s) // 2]
    lg = math.log(stake) if stake and stake > 0 else None
    return {
        "rel_med": (stake / med) if stake else None,
        "rel_mean": (stake / mean) if stake else None,
        "rel_p90": (stake / p90) if (stake and p90) else None,
        "pctile": WH.cume_below(w, stake) if stake else None,
        "z_log": ((lg - mu) / sd) if (lg is not None and sd and sd > 1e-9) else None,
        "is_max": (1.0 if (stake and stake > (w.get("max") or 0)) else 0.0),
        "rel_recent": (stake / rmed) if (stake and rmed) else None,
        "ramp": (rmed / med) if (rmed and med) else None,
        "skew": (mean / med) if med else None,
        "n_hist": float(n),
        "_median": med, "_mean": mean, "_n": n,
        "_experienced": n >= WH.EXPERIENCED_N,
        "_trusted": n >= WH.MIN_N and med >= WH.MIN_MEDIAN,
    }


def load_table(path=TABLE_PATH):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


_TABLE = None


def table(path=TABLE_PATH):
    global _TABLE
    if _TABLE is None:
        _TABLE = load_table(path) or {}
    return _TABLE


def _bin_of(edges, v):
    import bisect
    return bisect.bisect_right(edges, v)


def multiplier(key_or_row, stake=None, store=None):
    """(mult, why, feats) -- the size multiplier this system recommends.

    Accepts a board row (uses its user/wallet and stake) or an explicit key and
    stake. Multipliers from every feature whose bin is populated are combined by
    GEOMETRIC MEAN, so ten near-1.0 readings cannot compound into a large number
    and one extreme feature cannot dominate.
    """
    if isinstance(key_or_row, dict) and stake is None:
        h = key_or_row
        key = h.get("wallet") or h.get("user")
        stake = h.get("stake") or 0
    else:
        key = key_or_row
    f = features(key, stake, store)
    if not f:
        return 1.0, "no career history", None
    tb = table()
    if not tb or not tb.get("bins"):
        return 1.0, "no fitted table (run --fit)", f
    mults, used = [], []
    for name in FEATURES:
        v = f.get(name)
        spec = (tb["bins"] or {}).get(name)
        if v is None or not spec:
            continue
        m = spec["mult"][min(_bin_of(spec["edges"], v), len(spec["mult"]) - 1)]
        mults.append(m)
        if abs(m - 1.0) >= 0.01:
            used.append("%s %.2fx" % (name, m))
    if not mults:
        return 1.0, "no populated bins", f
    g = math.exp(sum(math.log(m) for m in mults) / len(mults))
    g = max(MULT_MIN, min(MULT_MAX, g))
    why = ("history mult %.2f from %d features" % (g, len(mults))
           + ((" · " + ", ".join(used)) if used else " · all flat"))
    return g, why, f


def fit(verbose=True):
    """Fit the bin table from the research windows. Rerunnable."""
    sys.path.insert(0, os.path.join(HOME, "nl-research"))
    import histfeat as HF
    import numpy as np

    rows = HF.featurise(HF.load_rows())
    if not rows:
        print("no rows to fit on", file=sys.stderr)
        return None
    base = HF.roi(rows)
    out = {"fitted": __import__("datetime").date.today().isoformat(),
           "n_rows": len(rows), "n_games": len({r["slug"] for r in rows}),
           "baseline_roi": base, "prior_n": PRIOR_N, "scale": SCALE,
           "bins": {}}
    for name in HF.FEATURES:
        edges = HF.bins_for(rows, name)
        if not edges:
            continue
        b = HF.binned(rows, name, edges)
        nb = len(edges) + 1
        mult, stats = [], []
        for k in range(nb):
            sel = b.get(k) or []
            if len(sel) < 30:
                mult.append(1.0)
                stats.append({"n": len(sel), "roi": None, "mult": 1.0})
                continue
            r = HF.roi(sel)
            shrink = min(SHRINK_CAP, len(sel) / float(len(sel) + PRIOR_N))
            m = 1.0 + shrink * (r - base) / SCALE
            m = max(MULT_MIN, min(MULT_MAX, m))
            mult.append(round(m, 4))
            stats.append({"n": len(sel), "roi": round(r, 3),
                          "shrink": round(shrink, 3), "mult": round(m, 4)})
        out["bins"][name] = {"edges": [round(float(e), 6) for e in edges],
                             "mult": mult, "stats": stats}
    with open(TABLE_PATH, "w") as fh:
        json.dump(out, fh, indent=1)
    if verbose:
        print("fitted %s on %d rows / %d games, baseline %+.2f%%"
              % (TABLE_PATH, out["n_rows"], out["n_games"], base))
    return out


def main(argv):
    if "--fit" in argv:
        fit()
        return 0
    tb = load_table()
    if "--show" in argv:
        if not tb:
            print("no table — run --fit")
            return 1
        print("fitted %s · %d rows / %d games · baseline %+.2f%% · prior_n %d · scale %.1f"
              % (tb["fitted"], tb["n_rows"], tb["n_games"], tb["baseline_roi"],
                 tb["prior_n"], tb["scale"]))
        for name, spec in tb["bins"].items():
            print("\n%s  edges %s" % (name, spec["edges"]))
            print("  %-6s %9s %9s %8s %7s" % ("bin", "n", "roi", "shrink", "mult"))
            for i, s in enumerate(spec["stats"]):
                print("  %-6d %9d %9s %8s %7.3f"
                      % (i, s["n"],
                         ("%+.2f%%" % s["roi"]) if s["roi"] is not None else "—",
                         ("%.3f" % s["shrink"]) if s.get("shrink") is not None else "—",
                         s["mult"]))
        span = []
        for name, spec in tb["bins"].items():
            span.append((max(spec["mult"]) - min(spec["mult"]), name))
        span.sort(reverse=True)
        print("\nwidest multiplier span across bins:")
        for d, name in span[:5]:
            print("  %-12s %.3f" % (name, d))
        return 0
    if "--explain" in argv:
        i = argv.index("--explain")
        key, stake = argv[i + 1], float(argv[i + 2])
        m, why, f = multiplier(key, stake)
        print("%s · wager $%,.0f".replace(",", "") % (key, stake))
        if not f:
            print("  no career history in the store")
            return 1
        print("  career: n=%d  median $%.0f  mean $%.0f  %s%s"
              % (f["_n"], f["_median"], f["_mean"],
                 "experienced" if f["_experienced"] else "not experienced",
                 "" if f["_trusted"] else ", UNTRUSTED denominator"))
        for name in FEATURES:
            v = f.get(name)
            if v is not None:
                print("    %-11s %10.3f" % (name, v))
        print("  -> %s" % why)
        print("  -> multiplier %.3f" % m)
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, LIVE)
    sys.exit(main(sys.argv[1:]))
