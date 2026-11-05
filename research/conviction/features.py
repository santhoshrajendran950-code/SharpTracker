#!/usr/bin/env python3
"""EXP-039 step 1: point-in-time play table for the conviction model.

    python3 research/conviction/features.py --corpus ~/nl-research/windows \
        --out ~/nl-research/conviction/plays.parquet

One row per PLAY = (slug, market, side) that at least one gated sharp holds at
T-60 (kickoff - 60 min, the research "posting" moment). Every feature is known
at T-60. Nothing uses the close, the result, `month_pnl` (it covers the window
being scored) or the corpus `drift` field (worst adverse move AFTER T, i.e. a
look-ahead).

Feature families (the three asks, kept separable so each can be tested alone):

  PROFILE   who the lead sharp is, from data before this bet:
            prior 90-day hold / P/L / volume (hist_prior.py), stake, stake vs
            the wallet's expanding career median, stake vs its open-book
            median (`typical`), bankroll share (stake / prior volume), number
            of earlier positions, and the wallet's own record on earlier
            settled positions: flat ROI and CLV overall and on its BIG bets
            (> 1.5x its career median at the time), shrunk toward zero.
  STACK     how many gated sharps are on the side, how many of them are
            oversized, their money; how many gated sharps are on another side
            of the same market (flagged as opposition), their money, and how
            many other $2.5K wallets sit opposite.
  MOVE      the line move from the sharps' entry to T-60, in points:
            T-60 price - lead entry, and the money-weighted version across
            every sharp on the side.

Labels (never features): won; follow ROI at the T-60 price + 1.5 pts vig
(what a subscriber could get); entry ROI at the lead's own price + vig
(reference only); follower CLV = close - T-60 price.

Wallet history is EXPANDING: a wallet's earlier position only counts once its
game had kicked off before this row's T, so its result was knowable.
Needs pandas + numpy (research only; production scoring is stdlib).
"""
import argparse, bisect, glob, json, math, os, sys
from collections import defaultdict

import numpy as np
import pandas as pd

VIG = 0.015
MIN_STAKE = 2500.0
# research gate, cut A (hist_cuts.py): the population the tracker would show
GATE_HOLD, GATE_PNL, GATE_VOL = 1.5, 50_000.0, 500_000.0
BIG = 1.5                  # "big bet" = stake > 1.5x own career median
MIN_HIST = 10              # earlier positions before a career median is trusted
SHRINK = 20                # pseudo-count for shrinking a wallet's record to 0
PX_LO, PX_HI = 0.05, 0.95
MARKET_TOTAL = __import__("re").compile(r"O/U [\d.]+")


def load(root):
    root = os.path.expanduser(root)
    fs = sorted(glob.glob(f"{root}/*/*/bt_scored.jsonl")) + sorted(glob.glob(f"{root}/20*/bt_scored.jsonl"))
    rows, seen = [], set()
    for f in fs:
        w = os.path.relpath(os.path.dirname(f), root)
        for line in open(f):
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
            r["window"] = w
            rows.append(r)
    return pd.DataFrame(rows)


def mtype(q):
    q = q or ""
    if MARKET_TOTAL.search(q):
        return "total"
    if q.lower().startswith("spread"):
        return "spread"
    if "draw" in q.lower():
        return "draw"
    return "moneyline"


def grade(price, won):
    q = min(0.99, price + VIG)
    return (1 - q) / q if won else -1.0


def wallet_history(df):
    """Expanding per-wallet record, written onto every row (index-aligned)."""
    n = len(df)
    out = {k: np.full(n, np.nan) for k in (
        "h_n", "h_med", "h_rel", "h_roi", "h_clv", "h_big_n", "h_big_roi", "h_big_clv",
        "h_roi_raw", "h_big_roi_raw")}
    for w, g in df.groupby("wallet", sort=False):
        idx = g.index.to_numpy()
        t = g["t_ts"].to_numpy(); ko = g["kickoff_ts"].to_numpy()
        stake = g["stake"].to_numpy(); entry = g["entry"].to_numpy()
        won = g["won"].to_numpy(); clve = g["clv_entry"].to_numpy()
        done_order = np.argsort(ko, kind="stable")
        todo_order = np.argsort(t, kind="stable")
        stakes_sorted = []
        cnt = 0; roi_s = 0.0; roi_n = 0; clv_s = 0.0; clv_n = 0
        b_n = 0; b_roi = 0.0; b_clv = 0.0; b_clvn = 0
        rel_at = np.full(len(g), np.nan)   # rel size at the time that row was placed
        med_at = np.full(len(g), np.nan)
        j = 0
        for i in todo_order:
            while j < len(done_order) and ko[done_order[j]] < t[i]:
                d = done_order[j]; j += 1
                cnt += 1
                bisect.insort(stakes_sorted, stake[d])
                e = entry[d]
                if e and e > 0 and stake[d] >= 250:
                    f = (1 / (e + VIG) - 1) if won[d] else -1.0
                    roi_s += f; roi_n += 1
                    if clve[d] == clve[d]:
                        clv_s += clve[d] * 100; clv_n += 1
                    # was d a big bet when it was placed? rel_at[d] is set because
                    # d's T precedes its kickoff, which precedes t[i]
                    if rel_at[d] == rel_at[d] and rel_at[d] > BIG:
                        b_n += 1; b_roi += f
                        if clve[d] == clve[d]:
                            b_clv += clve[d] * 100; b_clvn += 1
            k = len(stakes_sorted)
            if k >= MIN_HIST:
                med = stakes_sorted[k // 2] if k % 2 else 0.5 * (stakes_sorted[k // 2 - 1] + stakes_sorted[k // 2])
                if med > 0:
                    med_at[i] = med; rel_at[i] = stake[i] / med
            r = idx[i]
            out["h_n"][r] = cnt
            out["h_med"][r] = med_at[i]
            out["h_rel"][r] = rel_at[i]
            out["h_roi_raw"][r] = roi_s / roi_n if roi_n else np.nan
            out["h_roi"][r] = roi_s / (roi_n + SHRINK)
            out["h_clv"][r] = clv_s / (clv_n + SHRINK)
            out["h_big_n"][r] = b_n
            out["h_big_roi_raw"][r] = b_roi / b_n if b_n else np.nan
            out["h_big_roi"][r] = b_roi / (b_n + SHRINK)
            out["h_big_clv"][r] = b_clv / (b_clvn + SHRINK)
    for k, v in out.items():
        df[k] = v
    return df


def build(df):
    df = df.copy()
    df["vol"] = np.where(df.prior_hold.abs() > 1e-9, df.prior_pnl / (df.prior_hold / 100.0), 0.0)
    df = wallet_history(df.reset_index(drop=True))
    pre = (~df.decided) & (~df.two_sided) & (df.t_ts < df.kickoff_ts) & df.now.between(PX_LO, PX_HI) \
        & df.entry.notna() & (df.entry > 0)
    big = pre & (df.stake >= MIN_STAKE)
    sharp = big & (df.prior_hold >= GATE_HOLD) & (df.prior_pnl >= GATE_PNL) & (df.vol >= GATE_VOL)
    df["is_sharp"] = sharp
    df["is_big"] = big
    df["mkey"] = df.slug + "|" + df.market.fillna("")
    pos = df[big].copy()

    plays = []
    for mk, g in pos.groupby("mkey", sort=False):
        sides = g.side.unique()
        if len(sides) == 0:
            continue
        for s in sides:
            on = g[g.side == s]
            sh = on[on.is_sharp]
            if sh.empty:
                continue
            opp = g[g.side != s]
            osh = opp[opp.is_sharp]
            lead = sh.sort_values("stake", ascending=False).iloc[0]   # biggest sharp = the headline
            first = sh.sort_values("entry").iloc[0]
            now = float(lead.now)
            wentry = float((sh.entry * sh.stake).sum() / sh.stake.sum())
            rel = sh.h_rel
            p = {
                "slug": lead.slug, "market": lead.market, "side": s, "league": lead.league,
                "mtype": mtype(lead.market), "window": lead.window,
                "kickoff_ts": int(lead.kickoff_ts), "t_ts": int(lead.t_ts),
                "lead_wallet": lead.wallet,
                "now": now, "close": lead.close, "won": bool(lead.won),
                # ---- PROFILE (lead sharp) ----
                "p_hold": lead.prior_hold, "p_logpnl": math.log10(max(lead.prior_pnl, 1)),
                "p_logvol": math.log10(max(lead.vol, 1)),
                "p_logstake": math.log10(lead.stake),
                "p_rel_med": lead.h_rel if lead.h_med == lead.h_med and lead.h_med >= 250 else np.nan,
                "p_med": lead.h_med, "p_rel_typ": lead.vs_typical,
                "p_bankroll": lead.stake / lead.vol if lead.vol > 0 else np.nan,
                "p_logn": math.log10(1 + lead.h_n), "p_nopen": lead.n_open,
                "p_roi": lead.h_roi, "p_clv": lead.h_clv,
                "p_big_n": lead.h_big_n, "p_big_roi": lead.h_big_roi, "p_big_clv": lead.h_big_clv,
                "p_is_big": float(lead.h_rel > BIG) if lead.h_rel == lead.h_rel else np.nan,
                "p_hedged": float(bool(lead.hedged)), "p_stack_legs": lead.stack,
                "p_max_hold": sh.prior_hold.max(), "p_max_rel": rel.max(),
                "p_best_big_roi": sh.h_big_roi.max(),
                # ---- STACK ----
                "s_n": sh.wallet.nunique(),
                "s_n_big": int((rel > BIG).sum()),
                # EXP-034 / EXP-036 reference counts
                "s_n_x34": int(((sh.prior_hold > 3) & (rel > 2) & (sh.h_med >= 250)).sum()),
                "s_logmoney": math.log10(sh.stake.sum()),
                "s_n_opp": osh.wallet.nunique(),
                "s_n_opp_big": int((osh.h_rel > BIG).sum()),
                "s_opp_share": osh.stake.sum() / (osh.stake.sum() + sh.stake.sum()),
                "s_n_other_2k5": opp.wallet.nunique(),
                "s_n_same_2k5": on.wallet.nunique(),
                "s_money_share": on.stake.sum() / (on.stake.sum() + opp.stake.sum()),
                # ---- MOVE (entry -> T-60, points) ----
                "m_move": (now - float(lead.entry)) * 100,
                "m_move_w": (now - wentry) * 100,
                "m_move_first": (now - float(first.entry)) * 100,
                # ---- labels ----
                "y_follow": grade(now, bool(lead.won)),
                "y_entry": grade(float(lead.entry), bool(lead.won)),
                "y_clv": (lead.close - now) * 100 if lead.close == lead.close else np.nan,
                "y_clv_entry": (lead.close - lead.entry) * 100 if lead.close == lead.close else np.nan,
                "conv_v4": lead.conv,
            }
            plays.append(p)
    P = pd.DataFrame(plays)
    P["month"] = pd.to_datetime(P.kickoff_ts, unit="s", utc=True).dt.strftime("%Y-%m")
    P["logit_now"] = np.log(P.now / (1 - P.now))
    # EXP-034 X-gate as a reference rule: >=2 sharps with hold>3% and >2x median, no sharp opposed
    return P, df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="~/nl-research/windows")
    ap.add_argument("--out", default="~/nl-research/conviction/plays.parquet")
    a = ap.parse_args()
    raw = load(a.corpus)
    print(f"corpus rows {len(raw):,}", flush=True)
    P, _ = build(raw)
    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    P.to_parquet(out)
    print(f"plays {len(P):,} · games {P.slug.nunique():,} · months {P.month.min()}..{P.month.max()} -> {out}")
