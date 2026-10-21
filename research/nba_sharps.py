#!/usr/bin/env python3
"""Rank Polymarket NBA wallets from the already-collected backtest windows.

Adapted from nhl_sharps.py (2026-09-30), same filters and grading so the two
sports' rankings are directly comparable.

Inputs : ~/nl-research/windows/nba/<YYYY-MM>/bt_scored.jsonl  (read-only)
Outputs: ~/nl-research/nba_sharps.json, ~/nl-research/nba_sharps.md

No network, nothing under ~/.nightly-line is touched.
"""
import glob
import json
import os
import statistics
from collections import defaultdict

import numpy as np

HOME = os.path.expanduser("~")
WIN_DIR = os.path.join(HOME, "nl-research", "windows", "nba")
OUT_JSON = os.path.join(HOME, "nl-research", "nba_sharps.json")
OUT_MD = os.path.join(HOME, "nl-research", "nba_sharps.md")

VIG = 0.015
MIN_STAKE = 2500.0
BOOT_N = 2000
SEED = 20261020

# NBA 2025-26: regular season Oct-mid Apr, playoffs mid Apr - Jun.
PLAYOFF_MONTHS = {"2026-05", "2026-06"}


def mtype(r):
    m = r.get("market") or ""
    if "O/U" in m or "Total" in m or r.get("side") in ("Over", "Under"):
        return "total"
    if "Spread" in m:
        return "spread"
    return "moneyline"


def load_rows():
    coverage = []
    seen = set()
    rows = []
    for path in sorted(glob.glob(os.path.join(WIN_DIR, "*", "bt_scored.jsonl"))):
        month = os.path.basename(os.path.dirname(path))
        raw = kept = 0
        m_wallets, m_games = set(), set()
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw += 1
                r = json.loads(line)
                if r.get("won") not in (True, False):
                    continue
                if r.get("decided") is not False or r.get("two_sided") is not False:
                    continue
                stake = r.get("stake") or 0.0
                entry = r.get("entry")
                if stake < MIN_STAKE or entry is None or not (0.0 < entry < 1.0):
                    continue
                key = (r["wallet"], r["slug"], r["market"], r["side"])
                if key in seen:
                    continue
                seen.add(key)
                r["_month"] = month
                r["_mt"] = mtype(r)
                r["_playoff"] = month in PLAYOFF_MONTHS
                rows.append(r)
                kept += 1
                m_wallets.add(r["wallet"])
                m_games.add(r["slug"])
        coverage.append(dict(month=month, raw=raw, kept=kept,
                             wallets=len(m_wallets), games=len(m_games)))
    return rows, coverage


def grade(r):
    entry = r["entry"]
    if r["won"]:
        r["_flat"] = 1.0 / (entry + VIG) - 1.0
        r["_usd"] = r["stake"] * (1.0 / entry - 1.0)
    else:
        r["_flat"] = -1.0
        r["_usd"] = -r["stake"]
    r["_band_total"] = r["_mt"] == "total"
    r["_band_fav"] = entry >= 0.55
    r["_band_dog"] = entry < 0.50
    return r


def band_stats(rows):
    n = len(rows)
    if n == 0:
        return dict(n=0, w=0, l=0, roi=None, wagered=0.0, pnl=0.0, avg_clv=None,
                    beat_close=None, median_stake=None)
    w = sum(1 for r in rows if r["won"])
    flat = sum(r["_flat"] for r in rows)
    clvs = [r["clv"] for r in rows]
    return dict(
        n=n, w=w, l=n - w,
        roi=round(100.0 * flat / n, 2),
        wagered=round(sum(r["stake"] for r in rows), 2),
        pnl=round(sum(r["_usd"] for r in rows), 2),
        avg_clv=round(statistics.mean(clvs), 4),
        beat_close=round(100.0 * sum(1 for c in clvs if c > 0) / n, 1),
        median_stake=round(statistics.median(r["stake"] for r in rows), 2),
    )


def bootstrap_ci(rows, rng, n_boot=BOOT_N, alpha=0.10):
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["slug"]].append(r["_flat"])
    games = list(by_game.values())
    g = len(games)
    if g < 2:
        return None, None
    sums = np.array([sum(v) for v in games])
    cnts = np.array([len(v) for v in games])
    idx = rng.integers(0, g, size=(n_boot, g))
    roi = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.percentile(roi, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return round(100.0 * float(lo), 2), round(100.0 * float(hi), 2)


def flag_for(w):
    n, roi, lo, clv, ph = w["n"], w["roi"], w["ci_lo"], w["avg_clv"], w["prior_hold"]
    cand = (n >= 20 and roi > 0 and lo is not None and lo > -10.0
            and clv >= 0 and ph is not None and ph >= 1.5)
    if cand:
        return "candidate"
    if n >= 12 and roi > 5.0 and clv > 0:
        return "watch"
    return ""


def best_band(w):
    opts = []
    for name in ("fav", "dog", "total"):
        b = w[name]
        if b["n"] >= 5 and b["roi"] is not None:
            opts.append((b["roi"], b["n"], name))
    if not opts:
        return "-"
    roi, n, name = max(opts)
    return "%s (%+.1f%% / %d)" % (name, roi, n)


def build():
    rows, coverage = load_rows()
    rows = [grade(r) for r in rows]
    rng = np.random.default_rng(SEED)

    by_wallet = defaultdict(list)
    for r in rows:
        by_wallet[r["wallet"]].append(r)

    wallets = []
    for wal, rs in by_wallet.items():
        rs.sort(key=lambda r: (r["_month"], r["t_ts"]))
        latest = rs[-1]
        base = band_stats(rs)
        lo, hi = bootstrap_ci(rs, rng)
        months = sorted({r["_month"] for r in rs})
        w = dict(
            user=latest.get("user") or "",
            wallet=wal,
            months=months,
            n_months=len(months),
            prior_hold=round(latest["prior_hold"], 2) if latest.get("prior_hold") is not None else None,
            prior_pnl=round(latest["prior_pnl"], 2) if latest.get("prior_pnl") is not None else None,
            ci_lo=lo, ci_hi=hi,
            fav=band_stats([r for r in rs if r["_band_fav"]]),
            dog=band_stats([r for r in rs if r["_band_dog"]]),
            total=band_stats([r for r in rs if r["_band_total"]]),
            sides=band_stats([r for r in rs if not r["_band_total"]]),
            ml=band_stats([r for r in rs if r["_mt"] == "moneyline"]),
            spread=band_stats([r for r in rs if r["_mt"] == "spread"]),
            regular=band_stats([r for r in rs if not r["_playoff"]]),
            playoff=band_stats([r for r in rs if r["_playoff"]]),
            by_month={m: band_stats([r for r in rs if r["_month"] == m]) for m in months},
        )
        w.update(base)
        w["flag"] = flag_for(w)
        w["best_band"] = best_band(w)
        wallets.append(w)

    cands = sorted([w for w in wallets if w["flag"] == "candidate"], key=lambda w: -w["roi"])
    watch = sorted([w for w in wallets if w["flag"] == "watch"], key=lambda w: -w["roi"])
    rest = sorted([w for w in wallets if w["flag"] == ""], key=lambda w: -w["n"])[:50]
    rest.sort(key=lambda w: -w["roi"])
    out = cands + watch + rest
    for w in out:
        w["flag"] = w["flag"] or "none"

    field_order = [
        "flag", "user", "wallet", "n", "w", "l", "roi", "ci_lo", "ci_hi", "wagered", "pnl",
        "avg_clv", "beat_close", "median_stake", "months", "n_months", "prior_hold",
        "prior_pnl", "best_band", "fav", "dog", "total", "sides", "ml", "spread",
        "regular", "playoff", "by_month",
    ]
    with open(OUT_JSON, "w") as fh:
        json.dump([{k: w[k] for k in field_order} for w in out], fh, indent=1)

    write_md(rows, coverage, wallets, cands, watch)
    return coverage, cands, watch, rows, wallets


def short(addr):
    return addr[:10] + "…"


def fmt_row(w):
    ci = "[%s, %s]" % (
        "%+.1f" % w["ci_lo"] if w["ci_lo"] is not None else "n/a",
        "%+.1f" % w["ci_hi"] if w["ci_hi"] is not None else "n/a",
    )
    return "| %s | %s | %d | %d | %d-%d | %+.1f%% | %s | %s | %+.4f | %.0f%% | %s | %+.1f%% | %s |" % (
        w["user"] or short(w["wallet"]), w["flag"], w["n_months"],
        w["n"], w["w"], w["l"], w["roi"], ci,
        "${:,.0f}".format(w["pnl"]), w["avg_clv"], w["beat_close"],
        "${:,.0f}".format(w["median_stake"]), w["prior_hold"], w["best_band"])


def pool_line(label, rs):
    b = band_stats(rs)
    if b["n"] == 0:
        return "| %s | 0 | - | - | - | - |" % label
    return "| %s | %d | %d-%d | %+.1f%% | %+.4f | %.0f%% |" % (
        label, b["n"], b["w"], b["l"], b["roi"], b["avg_clv"], b["beat_close"])


def write_md(rows, coverage, wallets, cands, watch):
    L = []
    L.append("# Polymarket NBA sharps - 2025-26 season backtest\n")
    L.append("Generated by `~/nl-research/nba_sharps.py` from "
             "`~/nl-research/windows/nba/*/bt_scored.jsonl`. Same filters, grading and CI method "
             "as `nhl_sharps.py` so the two are comparable: positions at T = tipoff - 60 min, "
             "settled, decided == false, two_sided == false, stake >= $%d, 0 < entry < 1, deduped "
             "on (wallet, slug, market, side). Flat grading 1u/position, %.1f pts vig; CI = "
             "bootstrap 90%% on flat ROI resampling games (%d draws).\n"
             % (MIN_STAKE, VIG * 100, BOOT_N))

    L.append("## 1. Data coverage\n")
    L.append("| Month | Raw rows | Kept rows | Wallets | Games |")
    L.append("|---|---:|---:|---:|---:|")
    for c in coverage:
        L.append("| %s | %d | %d | %d | %d |" % (c["month"], c["raw"], c["kept"],
                                                 c["wallets"], c["games"]))
    L.append("| **All** | %d | %d | %d | %d |\n" % (
        sum(c["raw"] for c in coverage), len(rows),
        len({r["wallet"] for r in rows}), len({r["slug"] for r in rows})))

    L.append("## 2. Candidates and watch list\n")
    L.append("Candidate = n >= 20, flat ROI > 0, CI lower > -10%, avg CLV >= 0, prior hold >= 1.5%. "
             "Watch = n >= 12, flat ROI > +5%, avg CLV > 0, but fails one of the others. "
             "Mo = distinct months the wallet appears in (1 = a single hot month, not a season).\n")
    L.append("| User | Flag | Mo | n | W-L | Flat ROI | 90% CI | $ P/L | Avg CLV | Beat close | "
             "Median stake | Prior hold | Best band |")
    L.append("|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|")
    if not cands and not watch:
        L.append("| (none) | | | | | | | | | | | | |")
    for w in cands + watch:
        L.append(fmt_row(w))
    L.append("")

    n_pos = sum(1 for w in wallets if w["n"] >= 20)
    L.append("Of %d wallets with n >= 20: %d flat ROI > 0, %d avg CLV > 0, %d prior hold >= 1.5%%, "
             "%d meet all candidate rules.\n" % (
                 n_pos,
                 sum(1 for w in wallets if w["n"] >= 20 and w["roi"] > 0),
                 sum(1 for w in wallets if w["n"] >= 20 and w["avg_clv"] > 0),
                 sum(1 for w in wallets if w["n"] >= 20 and (w["prior_hold"] or 0) >= 1.5),
                 len(cands)))

    L.append("## 3. Candidate month-by-month\n")
    L.append("A season-long edge and one hot month look identical in the table above. "
             "This is each candidate / watch wallet split by month (n, W-L, flat ROI).\n")
    L.append("| User | " + " | ".join(c["month"] for c in coverage) + " |")
    L.append("|---|" + "---|" * len(coverage))
    for w in cands + watch:
        cells = []
        for c in coverage:
            b = w["by_month"].get(c["month"])
            cells.append("-" if not b or b["n"] == 0
                         else "%d: %d-%d %+.0f%%" % (b["n"], b["w"], b["l"], b["roi"]))
        L.append("| %s | %s |" % (w["user"] or short(w["wallet"]), " | ".join(cells)))
    L.append("")

    L.append("## 4. Pooled bands - what shape of NBA bettor shows up\n")
    L.append("| Band | n | W-L | Flat ROI | Avg CLV | Beat close |")
    L.append("|---|---:|---:|---:|---:|---:|")
    L.append(pool_line("All", rows))
    L.append(pool_line("Favourites (entry >= 0.55)", [r for r in rows if r["_band_fav"]]))
    L.append(pool_line("Dogs (entry < 0.50)", [r for r in rows if r["_band_dog"]]))
    L.append(pool_line("Moneyline", [r for r in rows if r["_mt"] == "moneyline"]))
    L.append(pool_line("Spread", [r for r in rows if r["_mt"] == "spread"]))
    L.append(pool_line("Totals (O/U)", [r for r in rows if r["_mt"] == "total"]))
    L.append(pool_line("Regular season", [r for r in rows if not r["_playoff"]]))
    L.append(pool_line("Playoffs", [r for r in rows if r["_playoff"]]))
    cw = [r for r in rows if r["wallet"] in {w["wallet"] for w in cands}]
    L.append(pool_line("Candidates only - all", cw))
    L.append(pool_line("Candidates only - favourites", [r for r in cw if r["_band_fav"]]))
    L.append(pool_line("Candidates only - dogs", [r for r in cw if r["_band_dog"]]))
    L.append(pool_line("Candidates only - moneyline", [r for r in cw if r["_mt"] == "moneyline"]))
    L.append(pool_line("Candidates only - spread", [r for r in cw if r["_mt"] == "spread"]))
    L.append("")

    L.append("## 5. The live v4 gate on NBA\n")
    L.append("`qualifies` is the frozen v4 pick gate as scored in each window - i.e. what the live "
             "rule would have fired on, before the 2026-10 contested/line-move/thin-wallet rules.\n")
    q = [r for r in rows if r.get("qualifies")]
    L.append("| Set | n | W-L | Flat ROI | Avg CLV | Beat close |")
    L.append("|---|---:|---:|---:|---:|---:|")
    L.append(pool_line("qualifies == true", q))
    L.append(pool_line("  moneyline", [r for r in q if r["_mt"] == "moneyline"]))
    L.append(pool_line("  spread", [r for r in q if r["_mt"] == "spread"]))
    L.append(pool_line("  favourites", [r for r in q if r["_band_fav"]]))
    L.append(pool_line("  dogs", [r for r in q if r["_band_dog"]]))
    for c in coverage:
        L.append(pool_line("  " + c["month"], [r for r in q if r["_month"] == c["month"]]))
    L.append("")
    lo, hi = bootstrap_ci(q, np.random.default_rng(SEED))
    L.append("90%% CI on the qualifying set's flat ROI: [%s, %s].\n" % (
        "%+.1f%%" % lo if lo is not None else "n/a",
        "%+.1f%%" % hi if hi is not None else "n/a"))

    zero_clv = 100.0 * sum(1 for r in rows if r["clv"] == 0) / max(len(rows), 1)
    L.append("## 6. Caveats\n")
    L.append(
        "One NBA season on one venue. The pool is survivor-biased: these wallets were discovered "
        "because they already looked good on-chain, and `prior_hold` / `prior_pnl` look back only "
        "90 days from each window, so the prior gate is not a clean out-of-sample test "
        "(`in_sample` is true for %d of %d rows). Flat 1u grading ignores how these wallets size, "
        "and the $ P/L column is dominated by a few very large stakes. CLV here is entry vs the "
        "Polymarket price at tipoff, not a sportsbook close, and %.0f%% of positions have CLV of "
        "exactly zero, so avg CLV barely discriminates between wallets. Playoff months have far "
        "fewer games and heavier favourites than the regular season.\n"
        % (sum(1 for r in rows if r.get("in_sample")), len(rows), zero_clv))

    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    coverage, cands, watch, rows, wallets = build()
    print("rows", len(rows), "wallets", len(wallets),
          "candidates", len(cands), "watch", len(watch))
    print("wrote", OUT_JSON, OUT_MD)
