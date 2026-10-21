#!/usr/bin/env python3
"""
Does a bet's size relative to the bettor's OWN WAGER HISTORY order outcomes?

Why this is not a repeat of bt_sizing.py / win3. Those tested `vs_typical`,
which lib/bt_rebuild.py builds as the median of the wallet's CONCURRENTLY OPEN
positions at T. That denominator is pathological in both directions: a wallet
holding 2 positions compares its bet to itself (always ~1.0x), and a wallet
holding 100 dust positions makes an ordinary bet read as 400x. Three rejections
of that feature say nothing about the feature Santhosh actually described.

Here the denominator is a TRAILING MEDIAN OF THE WALLET'S PRIOR BET STAKES,
expanding over the season in t_ts order, with a minimum history requirement.
It uses only bets strictly earlier than the one being scored, so it is
computable live and carries no look-ahead.

Flat 1u grading at the sharp's entry + VIG, game-clustered bootstrap, and a
chronological train/test split so a ladder cannot be fit and reported on the
same rows.
"""
import json, os, sys, glob
from collections import defaultdict
import numpy as np

HOME = os.path.expanduser("~")
WIN = os.path.join(HOME, "nl-research", "windows")
VIG = 0.015
MIN_STAKE = 2500.0
MIN_HIST = 10          # prior bets needed before a wallet's median is believable
MIN_MEDIAN = 250.0     # below this the median is real but trivially small (TRUST_TYPICAL)
BOOT_N = 2000
SEED = 20261020

# stake / trailing median -> bucket. Matches the s1 ladder's rungs so the
# answer is directly comparable to the version that was rejected.
BUCKETS = [(0.0, 0.5, "<0.5x"), (0.5, 1.0, "0.5-1x"), (1.0, 2.0, "1-2x"),
           (2.0, 5.0, "2-5x"), (5.0, 15.0, "5-15x"), (15.0, 1e9, "15x+")]


def bucket(rel):
    for lo, hi, name in BUCKETS:
        if lo <= rel < hi:
            return name
    return None


def load():
    """Every scored row, deduped, newest schema only."""
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
                except Exception:
                    continue
                k = (r.get("wallet"), r.get("slug"), r.get("asset"), r.get("t_ts"))
                if k in seen:
                    continue
                seen.add(k)
                r["_sport"] = sport
                rows.append(r)
    return rows


def usable(r):
    e = r.get("entry")
    return (r.get("won") is not None and e is not None and 0.0 < e < 1.0
            and (r.get("stake") or 0) >= MIN_STAKE and r.get("t_ts")
            and r.get("wallet") and r.get("slug"))


def add_trailing(rows):
    """stake / expanding median of that wallet's EARLIER stakes."""
    by_w = defaultdict(list)
    for r in rows:
        by_w[r["wallet"]].append(r)
    out = []
    for w, rs in by_w.items():
        rs.sort(key=lambda x: x["t_ts"])
        hist = []
        for r in rs:
            if len(hist) >= MIN_HIST:
                med = float(np.median(hist))
                if med >= MIN_MEDIAN:
                    r["_med"] = med
                    r["_rel"] = r["stake"] / med
                    r["_nhist"] = len(hist)
                    out.append(r)
            hist.append(r["stake"])
    return out


def grade(r):
    e = r["entry"]
    r["_flat"] = (1.0 / (e + VIG) - 1.0) if r["won"] else -1.0
    return r


def roi(rows):
    return (100.0 * sum(r["_flat"] for r in rows) / len(rows)) if rows else 0.0


def boot(rows, rng, n_boot=BOOT_N, alpha=0.10):
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["slug"]].append(r["_flat"])
    games = list(by_game.values())
    g = len(games)
    if g < 2:
        return None, None, None
    sums = np.array([sum(v) for v in games], dtype=float)
    cnts = np.array([len(v) for v in games], dtype=float)
    idx = rng.integers(0, g, size=(n_boot, g))
    r = 100.0 * sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.percentile(r, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return lo, hi, float((r <= 0).mean())


def month_of(r):
    import datetime
    return datetime.datetime.utcfromtimestamp(r["t_ts"]).strftime("%Y-%m")


def table(rows, rng, label, L):
    L.append("### %s\n" % label)
    L.append("| Bucket | n | Games | Win%% | Flat ROI | 90%% CI | P(ROI<=0) |")
    L.append("|---|---:|---:|---:|---:|---|---:|")
    for lo, hi, name in BUCKETS:
        sel = [r for r in rows if r["_bucket"] == name]
        if not sel:
            L.append("| %s | 0 | — | — | — | — | — |" % name)
            continue
        lo_ci, hi_ci, p = boot(sel, rng)
        wr = 100.0 * sum(1 for r in sel if r["won"]) / len(sel)
        ci = "[%+.1f, %+.1f]" % (lo_ci, hi_ci) if lo_ci is not None else "—"
        L.append("| %s | %d | %d | %.1f%% | %+.2f%% | %s | %s |" % (
            name, len(sel), len({r["slug"] for r in sel}), wr, roi(sel), ci,
            ("%.1f%%" % (100 * p)) if p is not None else "—"))
    lo_ci, hi_ci, p = boot(rows, rng)
    L.append("| **all** | %d | %d | %.1f%% | %+.2f%% | %s | %s |\n" % (
        len(rows), len({r["slug"] for r in rows}),
        100.0 * sum(1 for r in rows if r["won"]) / len(rows), roi(rows),
        "[%+.1f, %+.1f]" % (lo_ci, hi_ci) if lo_ci is not None else "—",
        ("%.1f%%" % (100 * p)) if p is not None else "—"))


def main():
    rng = np.random.default_rng(SEED)
    raw = load()
    rows = [r for r in raw if usable(r)]
    rows = add_trailing(rows)
    for r in rows:
        grade(r)
        r["_bucket"] = bucket(r["_rel"])
        r["_month"] = month_of(r)
    rows = [r for r in rows if r["_bucket"]]

    L = []
    L.append("# Relative-stake sizing — trailing wager-history baseline\n")
    L.append("Generated %s. Denominator = expanding median of the wallet's own "
             "EARLIER stakes (>= %d prior bets, median >= $%.0f). Flat 1u at "
             "entry + %.1f%% vig. CIs bootstrap %d resamples clustered by game.\n"
             % (__import__("datetime").date.today(), MIN_HIST, MIN_MEDIAN,
                VIG * 100, BOOT_N))
    L.append("Raw rows %d -> usable %d -> with trailing history %d · %d wallets · %d games\n"
             % (len(raw), len([r for r in raw if usable(r)]), len(rows),
                len({r["wallet"] for r in rows}), len({r["slug"] for r in rows})))

    L.append("## 1. Full sample\n")
    table(rows, rng, "All sports, all months", L)

    L.append("## 2. Chronological train/test\n")
    months = sorted({r["_month"] for r in rows})
    cut = months[int(len(months) * 0.6)]
    train = [r for r in rows if r["_month"] < cut]
    test = [r for r in rows if r["_month"] >= cut]
    L.append("Split at %s — train %s..%s (%d rows), test %s..%s (%d rows)\n"
             % (cut, months[0], cut, len(train), cut, months[-1], len(test)))
    table(train, rng, "TRAIN", L)
    table(test, rng, "TEST", L)

    L.append("## 3. By sport (full sample)\n")
    for sp in sorted({r["_sport"] for r in rows}):
        sel = [r for r in rows if r["_sport"] == sp]
        if len(sel) >= 500:
            table(sel, rng, "%s (n=%d)" % (sp, len(sel)), L)

    L.append("## 4. Monotonicity\n")
    xs = np.array([np.log(r["_rel"]) for r in rows])
    ys = np.array([r["_flat"] for r in rows])
    from scipy_free import spearman
    L.append("Spearman(log relative stake, flat unit P/L) = %+.4f on %d rows\n"
             % (spearman(xs, ys), len(rows)))

    out_md = os.path.join(HOME, "nl-research", "relsize.md")
    with open(out_md, "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nwrote %s" % out_md)


if __name__ == "__main__":
    main()
