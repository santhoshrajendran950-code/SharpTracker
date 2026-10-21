#!/usr/bin/env python3
"""
EXHAUSTIVE screen of wager-vs-own-history features against outcome.

Santhosh: "I need this to be an exhaustive system capable to size bets
appropriately based on wager vs user history." relsize.py tested ONE reading of
that -- stake / trailing median -- and found nothing. One feature is not the
space, so this tests every reasonable way to ask "how big is this bet FOR THIS
BETTOR", each computed as of bet time from that wallet's own earlier wagers
only, with no look-ahead:

  rel_med      stake / median(prior)            the relsize.py feature
  rel_mean     stake / mean(prior)              skew-sensitive centre
  rel_p90      stake / p90(prior)               vs their big-bet habit
  pctile       rank of stake within prior       scale-free, skew-immune: the
                                                most literal reading of "unusual
                                                for them"
  z_log        (log stake - mu) / sd of logs     standardised by their OWN spread
  is_max       stake > max(prior)               a career-largest bet
  rel_recent   stake / median(last RECENT)      vs current form, not career
  ramp         median(last RECENT) / median(prior)   are they sizing up lately
  skew         mean(prior) / median(prior)      their style, not this bet
  n_hist       len(prior)                       experience itself

Plus the conjunction Santhosh asked for (big vs BOTH median and mean) and every
feature re-run on experienced wallets only (n >= EXPERIENCED).

Each feature is binned into quintiles by its own distribution, graded flat 1u at
entry + vig, bootstrapped clustered by game, and split chronologically
train/test. With ~12 features x 5 bins x 2 populations the multiple-comparison
problem is real, so the report prints a Bonferroni-adjusted bar alongside the
raw CI and a feature is only called SURVIVING if it holds sign and significance
in BOTH halves.
"""
import bisect
import datetime
import glob
import json
import math
import os
from collections import defaultdict

import numpy as np

HOME = os.path.expanduser("~")
WIN = os.path.join(HOME, "nl-research", "windows")
VIG = 0.015
MIN_STAKE = 2500.0
MIN_HIST = 20
RECENT = 20
EXPERIENCED = 100
BOOT_N = 2000
SEED = 20261020
NBINS = 5


def load_rows():
    files = sorted(glob.glob(os.path.join(WIN, "*", "*", "bt_scored.jsonl")))
    files += sorted(glob.glob(os.path.join(WIN, "20*", "bt_scored.jsonl")))
    seen, rows = set(), []
    for f in files:
        parts = f.split(os.sep)
        sport = parts[-3] if parts[-3] != "windows" else "football"
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                k = (r.get("wallet"), r.get("slug"), r.get("asset"), r.get("t_ts"))
                if k in seen:
                    continue
                seen.add(k)
                e = r.get("entry")
                if (r.get("won") is None or e is None or not (0.0 < e < 1.0)
                        or not r.get("t_ts") or not r.get("wallet")
                        or not r.get("slug")):
                    continue
                r["_sport"] = sport
                rows.append(r)
    return rows


def featurise(rows):
    """Expanding, bet-time-only history features. Returns the scored subset.

    Every wager the wallet placed counts toward its history -- including the
    sub-$2,500 ones the scanner would never surface -- because a bettor's normal
    size is their normal size. Only the SCORED rows are gated at MIN_STAKE.
    """
    by_w = defaultdict(list)
    for r in rows:
        by_w[r["wallet"]].append(r)
    out = []
    for w, rs in by_w.items():
        rs.sort(key=lambda x: x["t_ts"])
        hist = []              # chronological stakes
        shist = []             # same, kept sorted for percentiles
        slog = 0.0             # running sum of logs
        slog2 = 0.0            # running sum of squared logs
        for r in rs:
            stake = r.get("stake") or 0.0
            n = len(hist)
            if n >= MIN_HIST and stake >= MIN_STAKE:
                med = shist[n // 2]
                mean = sum(hist) / n
                p90 = shist[min(n - 1, int(0.90 * n))]
                mu = slog / n
                var = max(0.0, slog2 / n - mu * mu)
                sd = math.sqrt(var)
                lo = math.log(stake) if stake > 0 else None
                recent = hist[-RECENT:]
                rmed = sorted(recent)[len(recent) // 2]
                f = {
                    "rel_med": stake / med if med > 0 else None,
                    "rel_mean": stake / mean if mean > 0 else None,
                    "rel_p90": stake / p90 if p90 > 0 else None,
                    "pctile": bisect.bisect_left(shist, stake) / float(n),
                    "z_log": ((lo - mu) / sd) if (sd > 1e-9 and lo is not None) else None,
                    "is_max": 1.0 if stake > shist[-1] else 0.0,
                    "rel_recent": stake / rmed if rmed > 0 else None,
                    "ramp": rmed / med if med > 0 else None,
                    "skew": mean / med if med > 0 else None,
                    "n_hist": float(n),
                }
                r["_f"] = f
                r["_flat"] = (1.0 / (r["entry"] + VIG) - 1.0) if r["won"] else -1.0
                r["_month"] = datetime.datetime.utcfromtimestamp(r["t_ts"]).strftime("%Y-%m")
                r["_exp"] = n >= EXPERIENCED
                out.append(r)
            if stake > 0:
                hist.append(stake)
                bisect.insort(shist, stake)
                lg = math.log(stake)
                slog += lg
                slog2 += lg * lg
    return out


def boot(rows, rng, alpha=0.10):
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["slug"]].append(r["_flat"])
    games = list(by_game.values())
    g = len(games)
    if g < 2:
        return None, None, None
    sums = np.array([sum(v) for v in games], dtype=float)
    cnts = np.array([len(v) for v in games], dtype=float)
    idx = rng.integers(0, g, size=(BOOT_N, g))
    r = 100.0 * sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.percentile(r, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return lo, hi, float((r <= 0).mean())


def roi(rows):
    return (100.0 * sum(r["_flat"] for r in rows) / len(rows)) if rows else 0.0


def spearman(x, y):
    import sys
    sys.path.insert(0, os.path.join(HOME, "nl-research"))
    from scipy_free import spearman as sp
    return sp(x, y)


def bins_for(rows, name):
    vals = [r["_f"][name] for r in rows if r["_f"].get(name) is not None]
    if len(vals) < 100:
        return None
    if name == "is_max":
        return [0.5]
    qs = np.percentile(vals, [100.0 * k / NBINS for k in range(1, NBINS)])
    return sorted(set(round(float(q), 6) for q in qs))


def binned(rows, name, edges):
    out = defaultdict(list)
    for r in rows:
        v = r["_f"].get(name)
        if v is None:
            continue
        out[bisect.bisect_right(edges, v)].append(r)
    return out


FEATURES = ["rel_med", "rel_mean", "rel_p90", "pctile", "z_log", "is_max",
            "rel_recent", "ramp", "skew", "n_hist"]


def screen(rows, rng, label, L, n_tests):
    L.append("### %s — n=%d, %d games, %d wallets\n"
             % (label, len(rows), len({r["slug"] for r in rows}),
                len({r["wallet"] for r in rows})))
    months = sorted({r["_month"] for r in rows})
    if len(months) < 4:
        L.append("_too few months to split_\n")
        return []
    cut = months[int(len(months) * 0.6)]
    tr = [r for r in rows if r["_month"] < cut]
    te = [r for r in rows if r["_month"] >= cut]
    base = roi(rows)
    L.append("Baseline flat ROI %+.2f%% · train %s..%s (%d) · test %s..%s (%d)\n"
             % (base, months[0], cut, len(tr), cut, months[-1], len(te)))
    L.append("| Feature | Spearman | Top-bin TRAIN | Top-bin TEST | Bot-bin TEST | Verdict |")
    L.append("|---|---:|---|---|---|---|")
    survivors = []
    alpha_adj = 0.10 / max(1, n_tests)
    for name in FEATURES:
        edges = bins_for(rows, name)
        if not edges:
            L.append("| %s | — | — | — | — | too sparse |" % name)
            continue
        xs = [r["_f"][name] for r in rows if r["_f"].get(name) is not None]
        ys = [r["_flat"] for r in rows if r["_f"].get(name) is not None]
        rho = spearman(np.array(xs), np.array(ys))
        btr, bte = binned(tr, name, edges), binned(te, name, edges)
        top = max(btr.keys() | bte.keys()) if (btr or bte) else 0
        bot = 0

        def cell(d, k):
            s = d.get(k) or []
            if len(s) < 30:
                return "n=%d —" % len(s), None, None
            lo, hi, p = boot(s, rng)
            return ("%+.1f%% [%+.1f,%+.1f] n=%d" % (roi(s), lo, hi, len(s))), p, roi(s)

        c_tr, p_tr, r_tr = cell(btr, top)
        c_te, p_te, r_te = cell(bte, top)
        c_bo, p_bo, r_bo = cell(bte, bot)
        verdict = "no"
        if (r_tr is not None and r_te is not None and p_tr is not None
                and p_te is not None):
            same_sign = (r_tr > 0) == (r_te > 0)
            sig_tr = min(p_tr, 1 - p_tr) < alpha_adj
            sig_te = min(p_te, 1 - p_te) < alpha_adj
            if same_sign and sig_tr and sig_te:
                verdict = "**SURVIVES**"
                survivors.append(name)
            elif same_sign and (sig_tr or sig_te):
                verdict = "one half only"
            elif same_sign:
                verdict = "sign holds, not sig"
        L.append("| %s | %+.4f | %s | %s | %s | %s |"
                 % (name, rho, c_tr, c_te, c_bo, verdict))
    L.append("")
    return survivors


def conjunction(rows, rng, L):
    """Santhosh's own rule: big vs BOTH median and mean."""
    L.append("### Conjunction — big vs BOTH centres\n")
    L.append("| Rule | n | Games | Flat ROI | 90% CI | P(ROI<=0) |")
    L.append("|---|---:|---:|---:|---|---:|")
    tests = [
        ("rel_med >= 2 only", lambda f: f["rel_med"] >= 2 and f["rel_mean"] < 2),
        ("rel_mean >= 2 only", lambda f: f["rel_mean"] >= 2 and f["rel_med"] < 2),
        ("BOTH >= 2", lambda f: f["rel_med"] >= 2 and f["rel_mean"] >= 2),
        ("BOTH >= 3", lambda f: f["rel_med"] >= 3 and f["rel_mean"] >= 3),
        ("BOTH >= 5", lambda f: f["rel_med"] >= 5 and f["rel_mean"] >= 5),
        ("neither", lambda f: f["rel_med"] < 2 and f["rel_mean"] < 2),
        ("pctile >= 0.95", lambda f: f["pctile"] >= 0.95),
        ("pctile >= 0.99", lambda f: f["pctile"] >= 0.99),
        ("career-largest bet", lambda f: f["is_max"] >= 0.5),
    ]
    for lbl, fn in tests:
        sel = [r for r in rows
               if r["_f"].get("rel_med") is not None
               and r["_f"].get("rel_mean") is not None and fn(r["_f"])]
        if len(sel) < 30:
            L.append("| %s | %d | — | — | — | — |" % (lbl, len(sel)))
            continue
        lo, hi, p = boot(sel, rng)
        L.append("| %s | %d | %d | %+.2f%% | [%+.1f, %+.1f] | %.1f%% |"
                 % (lbl, len(sel), len({r["slug"] for r in sel}), roi(sel),
                    lo, hi, 100 * p))
    L.append("")


def main():
    rng = np.random.default_rng(SEED)
    rows = featurise(load_rows())
    exp = [r for r in rows if r["_exp"]]
    n_tests = len(FEATURES) * 2
    L = ["# Exhaustive wager-vs-history feature screen\n",
         "Generated %s. Features computed as of bet time from the wallet's own "
         "earlier wagers only (>= %d prior). Flat 1u at entry + %.1f%% vig, CIs "
         "bootstrap %d clustered by game. %d features x 2 populations, so the "
         "significance bar is Bonferroni-adjusted to alpha=%.4f; SURVIVES means "
         "same sign AND significant in BOTH halves of a chronological split.\n"
         % (datetime.date.today(), MIN_HIST, VIG * 100, BOOT_N, len(FEATURES),
            0.10 / n_tests),
         "Rows %d · experienced subset (n_hist >= %d) %d\n" % (len(rows), EXPERIENCED, len(exp))]
    L.append("## 1. All wallets with history\n")
    s1 = screen(rows, rng, "All wallets, n_hist >= %d" % MIN_HIST, L, n_tests)
    L.append("## 2. Experienced wallets only\n")
    s2 = screen(exp, rng, "n_hist >= %d" % EXPERIENCED, L, n_tests)
    L.append("## 3. Conjunctions (all wallets)\n")
    conjunction(rows, rng, L)
    L.append("## 4. Conjunctions (experienced only)\n")
    conjunction(exp, rng, L)
    L.append("## 5. Verdict\n")
    L.append("Surviving features, all wallets: %s" % (", ".join(s1) if s1 else "**none**"))
    L.append("")
    L.append("Surviving features, experienced: %s" % (", ".join(s2) if s2 else "**none**"))
    L.append("")
    out = os.path.join(HOME, "nl-research", "histfeat.md")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
