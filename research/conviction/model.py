#!/usr/bin/env python3
"""EXP-039 step 2: conviction model, walk-forward by kickoff month.

    python3 research/conviction/model.py --plays ~/nl-research/conviction/plays.parquet \
        --out results/exp039-ml-walkforward.md

What it does, in order (all settings below were fixed BEFORE the first run):

1. Feature families PROFILE / STACK / MOVE (see features.py) and ALL = the three.
   The market price is never a feature. It enters as an OFFSET: the model
   starts at the market's implied probability and can only learn a deviation
   from it, so a score cannot "find" edge by rediscovering that favourites win.
2. Walk-forward: for every test month M from 2026-01 to 2026-10, fit on plays
   whose game kicked off before M began, score M. Nothing in a test month is
   seen in training, and wallet history features are expanding.
3. Per model: gradient-boosted depth-2 trees (LightGBM, monotone where a sign
   is hypothesised, heavy regularisation) and, for reading, a single depth-3
   decision tree fit to the same residual target.
4. Conviction 0-100 = the percentile of the play's predicted edge (expected
   ROI at the T-60 price + vig) among that fold's TRAINING plays, so 50 is a
   typical play and 90 is the top tenth as judged before the month started.
   Bet = predicted edge > 0 AND conviction >= BET_MIN (pre-registered 80).
5. Report, pooled over the out-of-sample months: conviction bands, the bet
   rule, a log-loss test against the market price, month-by-month sign, and
   the same for each family alone, the reference rules (all plays, EXP-034,
   EXP-036 C08) and a CLV model. Intervals resample GAMES (90%).

Needs pandas, numpy, lightgbm, scikit-learn. Research only.
"""
import argparse, json, math, os, sys
from collections import OrderedDict

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.tree import DecisionTreeRegressor, export_text

VIG = 0.015
TEST_FROM = "2026-01"
BET_MIN = 80
BOOT = 1000
SEED = 7

# ---- feature families -------------------------------------------------------
# (name, monotone sign: +1 more is better, -1 more is worse, 0 unconstrained)
PROFILE = OrderedDict([
    ("p_hold", 0), ("p_logpnl", 0), ("p_logvol", 0), ("p_logstake", 0),
    ("p_lrel_med", 0), ("p_lrel_typ", 0), ("p_lbankroll", 0), ("p_logn", 0),
    ("p_nopen", 0), ("p_roi", 0), ("p_clv", 0), ("p_big_roi", 0), ("p_big_clv", 0),
    ("p_hedged", 0), ("p_stack_legs", 0),
])
STACK = OrderedDict([
    ("s_n", 0), ("s_n_big", 0), ("s_logmoney", 0), ("s_n_opp", 0), ("s_n_opp_big", 0),
    ("s_opp_share", 0), ("s_n_other_2k5", 0), ("s_money_share", 0),
])
MOVE = OrderedDict([("m_move_c", 0), ("m_move_w_c", 0), ("m_move_first_c", 0)])
FAMILIES = OrderedDict([("PROFILE", PROFILE), ("STACK", STACK), ("MOVE", MOVE)])
FAMILIES["ALL"] = OrderedDict(list(PROFILE.items()) + list(STACK.items()) + list(MOVE.items()))
# the hypotheses as stated, signs forced (monotone), stumps only: the most
# constrained model we could build. Run second, after the free models failed.
HYP = OrderedDict([("s_n_x34_c", 1), ("s_n_big", 1), ("opp_any", -1), ("s_opp_share", -1),
                   ("m_move_c", 1), ("m_move_w_c", 1), ("p_big_roi", 1), ("p_big_clv", 1),
                   ("p_clv", 1), ("p_lrel_med", 1)])
HYP_PARAMS = dict(learning_rate=0.01, min_data_in_leaf=500, num_leaves=3, max_depth=1)
HYP_ROUNDS = 150

LGB_PARAMS = dict(objective="binary", learning_rate=0.02, num_leaves=4, max_depth=2,
                  min_data_in_leaf=300, feature_fraction=0.8, bagging_fraction=0.8,
                  bagging_freq=1, lambda_l2=10.0, verbose=-1, seed=SEED)
N_ROUNDS = 250
TREE = dict(max_depth=3, min_samples_leaf=400)


def prep(P):
    P = P.copy()
    lg = lambda x: np.log10(np.clip(x, 1e-3, None))
    P["p_lrel_med"] = np.clip(lg(P.p_rel_med), -2, 3)
    P["p_lrel_typ"] = np.clip(lg(P.p_rel_typ), -2, 3)
    P["p_lbankroll"] = np.clip(lg(P.p_bankroll), -5, 0)
    P["p_nopen"] = np.log10(1 + P.p_nopen)
    for c in ("m_move", "m_move_w", "m_move_first"):
        P[c + "_c"] = P[c].clip(-20, 20)
    P["s_n_x34_c"] = P.s_n_x34.clip(upper=3)
    P["opp_any"] = (P.s_n_opp > 0).astype(float)
    P["q"] = (P.now + VIG).clip(upper=0.99)
    P["implied"] = P.now
    return P.sort_values("kickoff_ts").reset_index(drop=True)


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def fit_lgb(tr, feats):
    mono = [feats[f] for f in feats]
    d = lgb.Dataset(tr[list(feats)], label=tr.won.astype(int), init_score=tr.logit_now,
                    free_raw_data=False)
    hyp = feats is HYP
    return lgb.train(dict(LGB_PARAMS, monotone_constraints=mono, **(HYP_PARAMS if hyp else {})),
                     d, num_boost_round=HYP_ROUNDS if hyp else N_ROUNDS)


def exp_roi(p_hat, q):
    """Expected flat ROI at price q given win probability p_hat."""
    return p_hat / q - 1


def walk_forward(P, feats, kind="lgb"):
    months = sorted(m for m in P.month.unique() if m >= TEST_FROM)
    out = []
    for m in months:
        start = pd.Timestamp(m + "-01", tz="UTC").timestamp()
        tr = P[P.kickoff_ts < start]
        te = P[P.month == m]
        if te.empty or len(tr) < 2000:
            continue
        if kind == "lgb":
            b = fit_lgb(tr, feats)
            f_tr = b.predict(tr[list(feats)], raw_score=True)
            f_te = b.predict(te[list(feats)], raw_score=True)
            e_tr = exp_roi(sigmoid(tr.logit_now + f_tr), tr.q)
            e_te = exp_roi(sigmoid(te.logit_now + f_te), te.q)
        elif kind == "tree":
            # target = the result minus the price: what the market missed
            y = tr.won.astype(float) - tr.implied
            t = DecisionTreeRegressor(random_state=SEED, **TREE).fit(tr[list(feats)].fillna(-9), y)
            e_tr = (tr.implied + t.predict(tr[list(feats)].fillna(-9))) / tr.q - 1
            e_te = (te.implied + t.predict(te[list(feats)].fillna(-9))) / te.q - 1
        elif kind == "clv":
            d = lgb.Dataset(tr[list(feats)], label=tr.y_clv.fillna(0))
            b = lgb.train(dict(LGB_PARAMS, objective="regression"), d, num_boost_round=N_ROUNDS)
            e_tr = b.predict(tr[list(feats)]); e_te = b.predict(te[list(feats)])
        ref = np.sort(np.asarray(e_tr))
        conv = np.searchsorted(ref, np.asarray(e_te), side="right") / len(ref) * 100
        o = te[["slug", "month", "won", "now", "q", "implied", "y_follow", "y_entry", "y_clv",
                "league", "mtype", "s_n", "s_n_opp", "s_n_x34", "m_move", "lead_wallet"]].copy()
        o["edge"] = np.asarray(e_te); o["conv"] = conv
        if kind != "clv":
            o["p_hat"] = (np.asarray(e_te) + 1) * te.q
        out.append(o)
    return pd.concat(out, ignore_index=True)


# ---- grading ----------------------------------------------------------------
def boot_ci(df, col="y_follow", n=BOOT, lo=5, hi=95):
    if len(df) < 5:
        return (np.nan, np.nan)
    g = df.groupby("slug")[col].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    rng = np.random.default_rng(SEED)
    k = len(s)
    idx = rng.integers(0, k, size=(n, k))
    r = s[idx].sum(1) / c[idx].sum(1) * 100
    return (np.percentile(r, lo), np.percentile(r, hi))


def line(name, d):
    if d.empty:
        return f"| {name} | 0 | | | | | |"
    lo, hi = boot_ci(d)
    mpos = d.groupby("month").y_follow.mean()
    return (f"| {name} | {len(d):,} / {d.slug.nunique():,} | {d.won.mean()*100:.1f}% | "
            f"**{d.y_follow.mean()*100:+.1f}%** [{lo:+.1f}, {hi:+.1f}] | {d.y_entry.mean()*100:+.1f}% | "
            f"{d.y_clv.mean():+.2f} | {(mpos > 0).sum()}/{len(mpos)} |")


HDR = ("| cut | plays / games | hit | follow ROI [90% CI] | entry ROI | CLV pts | months + |\n"
       "|---|---:|---:|---|---:|---:|---:|")


def logloss_gain(o):
    """Mean log-loss improvement of p_hat over the market price, per play, with a
    game-clustered CI. > 0 means the model knows something the price does not."""
    y = o.won.astype(float).to_numpy()
    pm = o.implied.clip(1e-4, 1 - 1e-4).to_numpy(); ph = o.p_hat.clip(1e-4, 1 - 1e-4).to_numpy()
    ll = lambda p: -(y * np.log(p) + (1 - y) * np.log(1 - p))
    d = o.assign(_g=(ll(pm) - ll(ph)) * 1000)
    lo, hi = boot_ci(d, col="_g")
    return d._g.mean() * 1, lo / 100, hi / 100


def bands(o, edges=(0, 20, 40, 60, 80, 90, 101)):
    rows = []
    for a, b in zip(edges[:-1], edges[1:]):
        rows.append(line(f"conviction {a}-{min(b, 100)}", o[(o.conv >= a) & (o.conv < b)]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plays", default="~/nl-research/conviction/plays.parquet")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    P = prep(pd.read_parquet(os.path.expanduser(a.plays)))
    test = P[P.month >= TEST_FROM]
    L = []
    w = L.append
    w(f"Corpus: {len(P):,} plays on {P.slug.nunique():,} games, {P.month.min()} to {P.month.max()}. "
      f"Out-of-sample (walk-forward) months {TEST_FROM} to {P.month.max()}: {len(test):,} plays on "
      f"{test.slug.nunique():,} games.\n")

    # ---- reference rules on the OOS months
    w("### Reference rules, same out-of-sample months\n")
    w(HDR)
    w(line("every play (gated sharp on the side)", test))
    w(line("no gated sharp opposite", test[test.s_n_opp == 0]))
    w(line("gated sharp opposite (flagged)", test[test.s_n_opp > 0]))
    w(line(">=2 sharps, none opposite", test[(test.s_n >= 2) & (test.s_n_opp == 0)]))
    w(line(">=3 sharps, none opposite", test[(test.s_n >= 3) & (test.s_n_opp == 0)]))
    w(line("EXP-034 shape (>=2 hold>3% & >2x median, none opposite)", test[(test.s_n_x34 >= 2) & (test.s_n_opp == 0)]))
    w(line("line moved >= +2 pts toward since entry", test[test.m_move >= 2]))
    w(line("line moved <= -2 pts away since entry", test[test.m_move <= -2]))
    w(line("EXP-036 C08 (>=2 sharps & moved >= +2)", test[(test.s_n >= 2) & (test.m_move >= 2)]))
    w(line("v4 conv >= 70", test[test.conv_v4 >= 70]))
    w("")

    results = {}
    for fam, feats in FAMILIES.items():
        for kind in ("lgb", "tree"):
            o = walk_forward(P, feats, kind)
            results[(fam, kind)] = o
            print(fam, kind, len(o), flush=True)
    results[("HYP monotone", "lgb")] = walk_forward(P, HYP, "lgb")
    o_clv = walk_forward(P, FAMILIES["ALL"], "clv")
    o_clvm = walk_forward(P, FAMILIES["MOVE"], "clv")

    w("### Each family alone and combined: the bet rule and the market test\n")
    w(f"Bet = predicted edge > 0 and conviction >= {BET_MIN}. Log-loss gain is per 1,000 plays "
      "versus the market price; its interval excludes zero only if the model knows something the price does not.\n")
    w("| model | bet plays / games | follow ROI [90% CI] | CLV | months + | top-decile ROI | bottom-half ROI | log-loss gain [90% CI] | Spearman(conv, ROI) |")
    w("|---|---:|---|---:|---:|---:|---:|---|---:|")
    for (fam, kind), o in results.items():
        bet = o[(o.edge > 0) & (o.conv >= BET_MIN)]
        lo, hi = boot_ci(bet)
        mpos = bet.groupby("month").y_follow.mean()
        g, glo, ghi = logloss_gain(o)
        sp = o[["conv", "y_follow"]].corr("spearman").iloc[0, 1]
        w(f"| {fam} · {kind} | {len(bet):,} / {bet.slug.nunique():,} | {bet.y_follow.mean()*100:+.1f}% "
          f"[{lo:+.1f}, {hi:+.1f}] | {bet.y_clv.mean():+.2f} | {(mpos > 0).sum()}/{len(mpos)} | "
          f"{o[o.conv >= 90].y_follow.mean()*100:+.1f}% | {o[o.conv < 50].y_follow.mean()*100:+.1f}% | "
          f"{g:+.2f} [{glo:+.2f}, {ghi:+.2f}] | {sp:+.3f} |")
    w("")
    for (fam, kind) in [("ALL", "lgb"), ("MOVE", "lgb"), ("STACK", "lgb"), ("PROFILE", "lgb")]:
        w(f"### Conviction bands, {fam} model (gradient-boosted), out of sample\n")
        w(HDR)
        for r in bands(results[(fam, kind)]):
            w(r)
        w("")
    w("### A CLV model instead (predicts close - T-60 price), out of sample\n")
    w(HDR)
    for nm, oc in (("ALL", o_clv), ("MOVE", o_clvm)):
        for r in bands(oc, (0, 50, 80, 90, 101)):
            w(r.replace("| conviction", f"| {nm} CLV-score"))
    w("")

    # ---- stability: the ALL bet by sport, market type, half
    o = results[("ALL", "lgb")]
    bet = o[(o.edge > 0) & (o.conv >= BET_MIN)]
    w("### ALL model bet rule, cut by sport / market / half\n")
    w(HDR)
    for lg_, d in bet.groupby("league"):
        if len(d) >= 30:
            w(line(lg_, d))
    for mt, d in bet.groupby("mtype"):
        w(line(mt, d))
    ms = sorted(bet.month.unique()); half = ms[len(ms) // 2]
    w(line(f"first half (< {half})", bet[bet.month < half]))
    w(line(f"second half (>= {half})", bet[bet.month >= half]))
    w(line("excluding the single best month", bet[bet.month != bet.groupby("month").y_follow.mean().idxmax()]))
    w("")

    # ---- the readable model, fit on everything up to the last test month
    full = P
    feats = FAMILIES["ALL"]
    y = full.won.astype(float) - full.implied
    t = DecisionTreeRegressor(random_state=SEED, **TREE).fit(full[list(feats)].fillna(-9), y)
    w("### The single readable tree (ALL features, all data; leaf value = win rate above the price)\n")
    w("```\n" + export_text(t, feature_names=list(feats), decimals=3) + "```\n")
    b = fit_lgb(full, feats)
    imp = pd.Series(b.feature_importance("gain"), index=list(feats)).sort_values(ascending=False)
    imp = (imp / imp.sum() * 100).round(1)
    w("### Gain importance, boosted ALL model on all data (%)\n")
    w("| feature | gain % |\n|---|---:|")
    for k_, v in imp.items():
        if v > 0:
            w(f"| {k_} | {v} |")
    w("")
    rep = "\n".join(L)
    print(rep)
    if a.out:
        with open(a.out, "w") as f:
            f.write(rep + "\n")


if __name__ == "__main__":
    main()
