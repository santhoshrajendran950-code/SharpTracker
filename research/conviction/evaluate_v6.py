#!/usr/bin/env python3
"""EXP-039 step 3: grade conviction v6 (score.py) on the research play table.

    python3 research/conviction/evaluate_v6.py --plays ~/nl-research/conviction/plays.parquet \
        --out results/exp039-v6-research.md

v6 is not fitted, so every month is out of sample for its *parameters*; its
*design* was chosen after EXP-030/034/036 had been run on this same corpus,
so this is a consistency check, not an independent test. The independent tests
are the live ledger (live_check.py) and the forward test pre-registered in
the LEDGER. Grading as model.py: follow = T-60 price + 1.5 pts vig.
"""
import argparse, os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score as S
import model as M


def apply(P, **override):
    old = {k: getattr(S, k) for k in override}
    for k, v in override.items():
        setattr(S, k, v)
    try:
        res = [S.score({"n_sized": r.s_n_x34, "n_sharps": r.s_n, "n_opp": r.s_n_opp,
                        "move": r.m_move, "big_roi": r.p_big_roi}) for r in P.itertuples()]
    finally:
        for k, v in old.items():
            setattr(S, k, v)
    out = P.copy()
    out["v6"] = [c for c, _, _ in res]
    out["v6_bet"] = [b for _, b, _ in res]
    return out


BANDS = ((0, 40), (40, 55), (55, 70), (70, 85), (85, 101))


def section(L, title, D):
    L.append(f"### {title}\n")
    L.append(M.HDR)
    L.append(M.line("every play", D))
    for a, b in BANDS:
        L.append(M.line(f"v6 {a}-{min(b, 100)}", D[(D.v6 >= a) & (D.v6 < b)]))
    L.append(M.line("**BET (v6 gates pass)**", D[D.v6_bet]))
    L.append(M.line("no bet", D[~D.v6_bet]))
    L.append("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plays", default="~/nl-research/conviction/plays.parquet")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    P = apply(M.prep(pd.read_parquet(os.path.expanduser(a.plays))))
    L = []
    section(L, f"All months ({P.month.min()} to {P.month.max()})", P)
    for nm, D in (("Before 2026 (2025-09 to 2025-12)", P[P.month < "2026-01"]),
                  ("2026-01 to 2026-05", P[(P.month >= "2026-01") & (P.month < "2026-06")]),
                  ("2026-06 to 2026-10", P[P.month >= "2026-06"])):
        section(L, nm, D)

    L.append("### Each component on its own (all months)\n")
    L.append(M.HDR)
    L.append(M.line("STACK: >=2 sized sharps on the side", P[P.s_n_x34 >= 2]))
    L.append(M.line("STACK: 1 sized sharp", P[P.s_n_x34 == 1]))
    L.append(M.line("STACK: 0 sized sharps", P[P.s_n_x34 == 0]))
    L.append(M.line("OPPOSITION: a gated sharp opposite (flag)", P[P.s_n_opp > 0]))
    L.append(M.line("OPPOSITION: none", P[P.s_n_opp == 0]))
    L.append(M.line("MOVE: >= 2 pts away since entry", P[P.m_move <= -2]))
    L.append(M.line("MOVE: -2 .. -0.5", P[(P.m_move > -2) & (P.m_move <= -0.5)]))
    L.append(M.line("MOVE: -0.5 .. +2", P[(P.m_move > -0.5) & (P.m_move < 2)]))
    L.append(M.line("MOVE: >= 2 pts toward", P[P.m_move >= 2]))
    L.append(M.line("PROFILE: lead big-bet ROI >= +5%", P[P.p_big_roi >= .05]))
    L.append(M.line("PROFILE: lead big-bet ROI <= -5%", P[P.p_big_roi <= -.05]))
    L.append(M.line("PROFILE: lead stake > 2x own median", P[P.p_rel_med > 2]))
    L.append(M.line("PROFILE: lead stake <= 2x own median", P[P.p_rel_med <= 2]))
    L.append("")

    L.append("### Ablation: the bet with one gate removed (all months)\n")
    L.append(M.HDR)
    b = P
    L.append(M.line("full bet: >=2 sized, uncontested, line not >= 2 away", b[b.v6_bet]))
    L.append(M.line("drop the move gate", b[(b.s_n_x34 >= 2) & (b.s_n_opp == 0)]))
    L.append(M.line("drop the opposition gate", b[(b.s_n_x34 >= 2) & (b.m_move > -2)]))
    L.append(M.line("drop the stack gate (>=1 sized)", b[(b.s_n_x34 >= 1) & (b.s_n_opp == 0) & (b.m_move > -2)]))
    L.append(M.line("drop the stack gate (any sharp)", b[(b.s_n_opp == 0) & (b.m_move > -2)]))
    L.append(M.line("full bet + lead big-bet ROI >= 0", b[b.v6_bet & (b.p_big_roi >= 0)]))
    L.append(M.line("full bet + 3 or more sized", b[b.v6_bet & (b.s_n_x34 >= 3)]))
    L.append("")

    bet = P[P.v6_bet]
    L.append("### The bet, by sport, market type and price (all months)\n")
    L.append(M.HDR)
    for k, d in bet.groupby("league"):
        if len(d) >= 15:
            L.append(M.line(k, d))
    for k, d in bet.groupby("mtype"):
        L.append(M.line(k, d))
    L.append(M.line("favourite (T-60 price >= 0.5)", bet[bet.now >= .5]))
    L.append(M.line("underdog (< 0.5)", bet[bet.now < .5]))
    L.append(M.line("excluding the best month", bet[bet.month != bet.groupby("month").y_follow.mean().idxmax()]))
    L.append("")
    L.append("### The bet, month by month\n")
    L.append("| month | plays | W-L | follow ROI | CLV |\n|---|---:|---|---:|---:|")
    for m, d in bet.groupby("month"):
        L.append(f"| {m} | {len(d)} | {int(d.won.sum())}-{int((~d.won).sum())} | {d.y_follow.mean()*100:+.1f}% | {d.y_clv.mean():+.2f} |")
    L.append("")
    rep = "\n".join(L)
    print(rep)
    if a.out:
        open(a.out, "w").write(rep + "\n")


if __name__ == "__main__":
    main()
