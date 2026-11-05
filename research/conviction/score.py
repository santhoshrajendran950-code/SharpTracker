#!/usr/bin/env python3
"""Conviction v6 — a points score (0-100) and a bet / no-bet call for one SIDE.

Pure stdlib, so the launchd python (3.9, no numpy) can import it unchanged.
Nothing here is fitted to ROI: the components and their signs come from the
three asks (profile, stacked conviction, line move) and the only effects that
held up out of sample in EXP-027/030/034/036/039. See
docs/reviews/2026-11-04-conviction-model.md for the evidence per component.

A side is described by a dict:

    n_sized     gated sharps on the side with hold > 3% AND stake > 2x their own
                career median (EXP-034's "sized sharp")
    n_sharps    all gated sharps on the side (sized or not)
    n_opp       gated sharps on ANY other side of the same market
    move        line move since the sharps entered, in points:
                price at posting - lead sharp's entry (positive = toward them)
    big_roi     lead sharp's shrunk flat ROI on their earlier bets that were
                > 1.5x their median (fraction, e.g. 0.06). None if unknown.

    score(side) -> (conviction 0..100, bet True/False, [reasons])
"""

BASE = 50
SIZED_PTS = {0: 0, 1: 10, 2: 25}      # 3+ -> SIZED_3
SIZED_3 = 30
OTHER_PTS, OTHER_CAP = 3, 6           # unsized gated sharps alongside
OPP_PTS = -30                         # any gated sharp on the other side
MOVE_AWAY, MOVE_AWAY_PTS = -2.0, -25  # line ran >= 2 pts against the sharps
MOVE_SOFT, MOVE_SOFT_PTS = -0.5, -5
MOVE_TOWARD, MOVE_TOWARD_PTS = 2.0, 5
BIG_ROI_EDGE, BIG_ROI_PTS = 0.05, 5

# the binary call: every gate must pass, the score only orders what passes
BET_SIZED, BET_MOVE = 2, MOVE_AWAY


def score(s):
    pts, why = BASE, []
    n_sized = int(s.get("n_sized") or 0)
    n_sh = int(s.get("n_sharps") or 0)
    n_opp = int(s.get("n_opp") or 0)
    move = s.get("move")
    big = s.get("big_roi")

    p = SIZED_PTS.get(n_sized, SIZED_3)
    pts += p
    if p:
        why.append(f"{n_sized} sized sharp{'s' if n_sized > 1 else ''} +{p}")
    other = min(max(n_sh - n_sized, 0) * OTHER_PTS, OTHER_CAP)
    if other:
        pts += other; why.append(f"{n_sh - n_sized} other sharp(s) +{other}")
    if n_opp:
        pts += OPP_PTS; why.append(f"CONTESTED: {n_opp} sharp(s) opposite {OPP_PTS}")
    if move is not None:
        if move <= MOVE_AWAY:
            pts += MOVE_AWAY_PTS; why.append(f"line {move:+.1f} pts away {MOVE_AWAY_PTS}")
        elif move <= MOVE_SOFT:
            pts += MOVE_SOFT_PTS; why.append(f"line {move:+.1f} pts away {MOVE_SOFT_PTS}")
        elif move >= MOVE_TOWARD:
            pts += MOVE_TOWARD_PTS; why.append(f"line {move:+.1f} pts toward +{MOVE_TOWARD_PTS}")
    if big is not None:
        if big >= BIG_ROI_EDGE:
            pts += BIG_ROI_PTS; why.append(f"lead wins on their big bets +{BIG_ROI_PTS}")
        elif big <= -BIG_ROI_EDGE:
            pts -= BIG_ROI_PTS; why.append(f"lead loses on their big bets -{BIG_ROI_PTS}")
    conv = max(0, min(100, pts))
    bet = (n_sized >= BET_SIZED and n_opp == 0
           and (move is None or move > BET_MOVE))
    return conv, bet, why


if __name__ == "__main__":
    ex = {"n_sized": 2, "n_sharps": 3, "n_opp": 0, "move": 1.2, "big_roi": 0.07}
    print(ex, "->", score(ex))
    ex = {"n_sized": 2, "n_sharps": 2, "n_opp": 1, "move": -2.5, "big_roi": None}
    print(ex, "->", score(ex))
