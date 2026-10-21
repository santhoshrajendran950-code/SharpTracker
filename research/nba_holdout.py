#!/usr/bin/env python3
"""Out-of-sample test: does NBA sharp selection persist within the season?

Train = 2025-10..2026-01, Test = 2026-02..2026-05 (same 2025-26 season).
Select wallets on TRAIN only, then grade them on TEST. Nothing is written
outside ~/nl-research.
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nba_sharps as S

TRAIN = {"2025-10", "2025-11", "2025-12", "2026-01"}
TEST = {"2026-02", "2026-03", "2026-04", "2026-05"}


def pooled(rows, rng, label):
    b = S.band_stats(rows)
    if b["n"] == 0:
        return f"| {label} | 0 | - | - | - | - |", None
    lo, hi = S.bootstrap_ci(rows, rng)
    ci = "[%+.1f, %+.1f]" % (lo, hi) if lo is not None else "n/a"
    return ("| %s | %d | %d-%d | %+.1f%% | %s | %+.4f |"
            % (label, b["n"], b["w"], b["l"], b["roi"], ci, b["avg_clv"])), b["roi"]


def main():
    rows, _ = S.load_rows()
    rows = [S.grade(r) for r in rows]
    rng = np.random.default_rng(20261020)

    tr = defaultdict(list)
    te = defaultdict(list)
    for r in rows:
        (tr if r["_month"] in TRAIN else te)[r["wallet"]].append(r)

    # --- build train-side wallet stats and apply the same candidate gate
    def stats(rs):
        rs = sorted(rs, key=lambda r: (r["_month"], r["t_ts"]))
        latest = rs[-1]
        b = S.band_stats(rs)
        lo, hi = S.bootstrap_ci(rs, rng)
        return dict(n=b["n"], roi=b["roi"], avg_clv=b["avg_clv"], ci_lo=lo,
                    prior_hold=latest.get("prior_hold"), user=latest.get("user") or "")

    tr_stats = {w: stats(rs) for w, rs in tr.items()}

    sel_cand = {w for w, s in tr_stats.items() if S.flag_for(
        dict(n=s["n"], roi=s["roi"], ci_lo=s["ci_lo"], avg_clv=s["avg_clv"],
             prior_hold=s["prior_hold"])) == "candidate"}
    sel_loose = {w for w, s in tr_stats.items() if s["n"] >= 20 and s["roi"] > 0}
    deep = [w for w, s in tr_stats.items() if s["n"] >= 20]
    by_roi = sorted(deep, key=lambda w: -tr_stats[w]["roi"])
    sel_top = set(by_roi[:max(1, len(by_roi) // 10)])
    by_clv = sorted(deep, key=lambda w: -tr_stats[w]["avg_clv"])
    sel_clv = set(by_clv[:max(1, len(by_clv) // 10)])

    L = []
    L.append("# NBA sharp selection - within-season out-of-sample test\n")
    L.append("Train = %s. Test = %s. Wallets are picked using TRAIN rows only, "
             "then graded on TEST rows. Flat 1u, %.1f pts vig, 90%% bootstrap CI "
             "resampling games.\n" % (", ".join(sorted(TRAIN)), ", ".join(sorted(TEST)), S.VIG * 100))
    L.append("Train: %d rows, %d wallets (%d with n>=20). Test: %d rows, %d wallets.\n"
             % (sum(len(v) for v in tr.values()), len(tr), len(deep),
                sum(len(v) for v in te.values()), len(te)))

    L.append("## Pooled TEST performance of each train-side selection\n")
    L.append("| Selection (made on train) | Test n | W-L | Test flat ROI | 90% CI | Avg CLV |")
    L.append("|---|---:|---:|---:|---|---:|")
    all_test = [r for rs in te.values() for r in rs]
    line, base = pooled(all_test, rng, "ALL wallets (baseline)")
    L.append(line)
    for name, sel in (("Candidate gate (n>=20, ROI>0, CI lo>-10, CLV>=0, hold>=1.5%)", sel_cand),
                      ("Loose (train n>=20 and ROI>0)", sel_loose),
                      ("Top decile by train ROI", sel_top),
                      ("Top decile by train CLV", sel_clv)):
        sub = [r for w in sel for r in te.get(w, [])]
        line, _ = pooled(sub, rng, "%s - %d wallets" % (name, len(sel)))
        L.append(line)
    # the complement of the candidate gate, for contrast
    sub = [r for w, rs in te.items() if w not in sel_cand for r in rs]
    line, _ = pooled(sub, rng, "NOT selected by candidate gate")
    L.append(line)
    L.append("")

    # --- rank correlation train vs test
    both = [w for w in deep if len(te.get(w, [])) >= 20]
    L.append("## Does train ROI / CLV rank predict test rank?\n")
    if len(both) >= 10:
        a = np.array([tr_stats[w]["roi"] for w in both])
        b = np.array([S.band_stats(te[w])["roi"] for w in both])
        ca = np.array([tr_stats[w]["avg_clv"] for w in both])
        cb = np.array([S.band_stats(te[w])["avg_clv"] for w in both])

        def spearman(x, y):
            rx = np.argsort(np.argsort(x)).astype(float)
            ry = np.argsort(np.argsort(y)).astype(float)
            return float(np.corrcoef(rx, ry)[0, 1])

        L.append("%d wallets have >=20 settled positions in BOTH halves.\n" % len(both))
        L.append("| Pair | Spearman rank corr |")
        L.append("|---|---:|")
        L.append("| train ROI -> test ROI | %+.3f |" % spearman(a, b))
        L.append("| train CLV -> test CLV | %+.3f |" % spearman(ca, cb))
        L.append("| train CLV -> test ROI | %+.3f |" % spearman(ca, b))
        L.append("| train ROI -> test CLV | %+.3f |" % spearman(a, cb))
        L.append("")
        L.append("Train-positive wallets with >=20 test bets: %d of %d; "
                 "%d of those stayed positive in test (%.0f%%, coin flip = 50%%).\n"
                 % (sum(1 for w in both if tr_stats[w]["roi"] > 0), len(both),
                    sum(1 for w in both if tr_stats[w]["roi"] > 0
                        and S.band_stats(te[w])["roi"] > 0),
                    100.0 * sum(1 for w in both if tr_stats[w]["roi"] > 0
                                and S.band_stats(te[w])["roi"] > 0)
                    / max(1, sum(1 for w in both if tr_stats[w]["roi"] > 0))))

        L.append("## Per-wallet carry-over (>=20 in both halves)\n")
        L.append("| User | Train n | Train ROI | Test n | Test ROI | Carried? |")
        L.append("|---|---:|---:|---:|---:|---|")
        for w in sorted(both, key=lambda w: -tr_stats[w]["roi"]):
            t = S.band_stats(te[w])
            L.append("| %s | %d | %+.1f%% | %d | %+.1f%% | %s |" % (
                tr_stats[w]["user"] or w[:10] + "…", tr_stats[w]["n"],
                tr_stats[w]["roi"], t["n"], t["roi"],
                "yes" if (tr_stats[w]["roi"] > 0) == (t["roi"] > 0) else "no"))
    else:
        L.append("Too few wallets with >=20 positions in both halves (%d).\n" % len(both))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_holdout.md")
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("wrote", out)
    print("train candidates:", len(sel_cand))


if __name__ == "__main__":
    main()
