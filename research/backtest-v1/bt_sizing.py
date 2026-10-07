#!/usr/bin/env python3
"""
Stage 6 — is there a unit-sizing formula that works OUT OF SAMPLE?

    python3 bt_sizing.py

The trap this is built to avoid: try twenty sizing rules on one 30-day window,
keep the best, and call it a strategy. With ~550 qualifying games the best of
twenty random rules shows +10% by luck alone. So:

  * CHRONOLOGICAL SPLIT. Everything is chosen on TRAIN (the first 60% of games
    by kickoff). TEST (the last 40%) is touched exactly once, at the end, with
    every knob already frozen. Nothing printed under TEST was used to pick
    anything.
  * A BETTABLE PRICE. Graded at the price 60 minutes before kickoff — the moment
    the row was scored — plus VIG, because Santhosh bets books and exchanges
    that charge, not Polymarket's mid. bt_report grades at the sharp's entry,
    which nobody following can get.
  * NO LOOK-AHEAD FEATURES. `drift`, `close`, `clv` are measured after T;
    `month_pnl` and `rank` are read today and contain the test window. None of
    them is an input. hold/pnl are the PRIOR values (bt_pool), typical/n_open
    are as-of-T (bt_rebuild).
  * FEW KNOBS. The model is a logistic regression with the market price as an
    offset — it can only learn DEVIATIONS from the price, which is the entire
    question. Its one hyper-parameter (L2 strength) is set by blocked CV on
    train. Sizing is quarter-Kelly on the model's edge, fixed in advance, so
    there is no threshold to tune.
  * GAME-CLUSTERED INTERVALS, as everywhere else in this project.

"Nothing survives" is a legitimate output and the script says so in words.
"""
import json, math, random, re, sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, "/Users/santhosh/.nightly-line")
import units as U

VIG = 0.015                 # probability points paid over the T-60 price
TRAIN_FRAC = 0.60
LADDER = [0.5, 1, 2, 3, 4, 5]
KELLY = 0.25                # quarter-Kelly; 1u = 1% of bankroll
BOOT = 3000
random.seed(20260921)
np.random.seed(20260921)


# ------------------------------------------------------------------ data
def mkt(r):
    m = r.get("market") or ""
    return "total" if re.search(r"O/U|Total", m, re.I) else "spread" if "Spread" in m else "ml"


def lean(r):
    side = (r.get("side") or "").strip().lower()
    return ("total", side) if mkt(r) == "total" else ("side", side)


def load():
    rows = [json.loads(l) for l in open("bt_scored.jsonl")]
    rows = [r for r in rows if r.get("won") is not None and 0 < (r.get("now") or 0) < 1]
    # cross-wallet consensus, the same definition check.mark_consensus uses
    # live: distinct wallets with a real stake leaning the same way in a game
    by = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if (r.get("stake") or 0) >= 2500:
            g, t = lean(r)
            by[(r["slug"], g)][t].add(r["wallet"])
    for r in rows:
        g, t = lean(r)
        sides = by.get((r["slug"], g), {})
        mine = sides.get(t, set())
        r["consensus"] = max(len(mine), 1)
        others = set().union(*[w for k, w in sides.items() if k != t]) if len(sides) > 1 else set()
        r["opposed"] = len(others - mine)
        r["q"] = min(r["now"] + VIG, 0.985)          # the price actually paid
    rows.sort(key=lambda r: r["kickoff_ts"])
    games = sorted({r["slug"]: r["kickoff_ts"] for r in rows}.items(), key=lambda kv: kv[1])
    cut = games[int(len(games) * TRAIN_FRAC)][1]
    return [r for r in rows if r["kickoff_ts"] < cut], [r for r in rows if r["kickoff_ts"] >= cut]


# --------------------------------------------------------------- grading
def roi(rs, w):
    risk = sum(w(r) for r in rs)
    if risk <= 0:
        return None
    return (sum(w(r) / r["q"] for r in rs if r["won"]) - risk) / risk * 100


def ci(rs, w):
    g = defaultdict(list)
    for r in rs:
        if w(r) > 0:
            g[r["slug"]].append(r)
    ks = list(g)
    if len(ks) < 20:
        return None
    out = []
    for _ in range(BOOT):
        s = [x for k in random.choices(ks, k=len(ks)) for x in g[k]]
        v = roi(s, w)
        if v is not None:
            out.append(v)
    out.sort()
    return out[int(.025 * len(out))], out[int(.975 * len(out))]


def report(name, rs, w):
    bets = [r for r in rs if w(r) > 0]
    if not bets:
        print(f"  {name:44} no bets")
        return None
    v, c = roi(bets, w), ci(bets, w)
    units = sum(w(r) for r in bets)
    clv = np.mean([r["clv"] for r in bets if r.get("clv") is not None]) * 100
    tag = ""
    if c:
        tag = "  <-- excludes zero, POSITIVE" if c[0] > 0 else "  (excludes zero, negative)" if c[1] < 0 else ""
    print(f"  {name:44} bets {len(bets):5} games {len({r['slug'] for r in bets}):5} "
          f"units {units:7.0f}  ROI {v:+6.1f}%  "
          f"{'CI [%+.1f, %+.1f]' % c if c else 'CI n/a':22} CLV {clv:+.2f}{tag}")
    return v, c


# ----------------------------------------------------------------- model
FEATS = ["log_stake", "log_vt", "vt_trusted", "prior_hold", "log_prior_pnl", "fresh",
         "moved", "log_n_open", "consensus2", "consensus3", "opposed", "stack2",
         "p_c", "p_c2", "is_total", "is_spread", "lg_soccer", "lg_tennis", "lg_mlb", "lg_fb"]


def feats(r):
    vt = r.get("vs_typical")
    trusted = (r.get("n_open") or 0) >= U.TRUST_POSITIONS and (r.get("typical") or 0) >= U.TRUST_TYPICAL
    p = r["now"]
    lg = r["league"]
    return [
        math.log10(max(r.get("stake") or 1, 1)),
        math.log10(min(max(vt, 0.1), 100)) if (vt and trusted) else 0.0,
        1.0 if (vt and trusted) else 0.0,
        min(r.get("prior_hold") or 0, 15),
        math.log10(max(r.get("prior_pnl") or 1, 1)),
        1.0 if r.get("fresh") else 0.0,
        (p - (r.get("entry") or p)),                 # has the price moved toward them by T
        math.log10(max(r.get("n_open") or 1, 1)),
        1.0 if r["consensus"] == 2 else 0.0,
        1.0 if r["consensus"] >= 3 else 0.0,
        min(r["opposed"], 3),
        1.0 if (r.get("stack") or 1) >= 2 else 0.0,
        p - 0.5, (p - 0.5) ** 2,
        1.0 if mkt(r) == "total" else 0.0, 1.0 if mkt(r) == "spread" else 0.0,
        1.0 if lg == "SOCCER" else 0.0, 1.0 if lg in ("ATP", "WTA", "TENNIS") else 0.0,
        1.0 if lg == "MLB" else 0.0, 1.0 if lg in ("NFL", "NCAAF") else 0.0,
    ]


def design(rs, mu=None, sd=None):
    X = np.array([feats(r) for r in rs], float)
    if mu is None:
        mu, sd = X.mean(0), X.std(0)
        sd[sd == 0] = 1
    X = (X - mu) / sd
    off = np.array([math.log(r["now"] / (1 - r["now"])) for r in rs])
    y = np.array([1.0 if r["won"] else 0.0 for r in rs])
    return X, off, y, mu, sd


def fit(X, off, y, lam, iters=60):
    """L2 logistic with a fixed offset (the market's own log-odds) and a free
    intercept. Newton steps; ~5k rows x 20 features converges in a handful."""
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    b = np.zeros(k + 1)
    R = np.eye(k + 1) * lam
    R[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(off + Xb @ b)))
        g = Xb.T @ (p - y) + R @ b
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + R
        step = np.linalg.solve(H, g)
        b -= step
        if np.abs(step).max() < 1e-8:
            break
    return b


def predict(b, X, off):
    return 1 / (1 + np.exp(-(off + np.hstack([np.ones((len(X), 1)), X]) @ b)))


def logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


def kelly_units(p, q):
    f = (p - q) / (1 - q)
    if f <= 0:
        return 0.0
    u = KELLY * f * 100
    if u < 0.5:
        return 0.0
    return min(LADDER, key=lambda s: abs(s - min(u, 5)))


# ------------------------------------------------------------------ main
def main():
    train, test = load()
    print(f"TRAIN {len(train)} positions / {len({r['slug'] for r in train})} games   "
          f"TEST {len(test)} positions / {len({r['slug'] for r in test})} games   "
          f"(graded at T-60 price + {VIG*100:.1f}pt vig)\n")

    flat = lambda r: 1.0
    q = lambda r: 1.0 if r.get("qualifies") else 0.0
    s1 = lambda r: U.recommend({**r, "consensus": 1, "user": None})[0] if r.get("qualifies") else 0.0
    s2 = lambda r: U.recommend({**r, "user": None})[0] if r.get("qualifies") else 0.0

    # ---- 1. the model, tuned on train only
    Xtr, otr, ytr, mu, sd = design(train)
    k = 5
    folds = np.array_split(np.arange(len(train)), k)       # blocked: time-ordered
    best = None
    for lam in (1, 10, 30, 100, 300, 1000, 3000):
        ll = []
        for i in range(1, k):                              # expanding window
            tr = np.concatenate(folds[:i]); va = folds[i]
            b = fit(Xtr[tr], otr[tr], ytr[tr], lam)
            ll.append(logloss(predict(b, Xtr[va], otr[va]), ytr[va]))
        m = float(np.mean(ll))
        if best is None or m < best[1]:
            best = (lam, m)
    base_ll = float(np.mean([logloss(1 / (1 + np.exp(-otr[folds[i]])), ytr[folds[i]]) for i in range(1, k)]))
    lam = best[0]
    print(f"MODEL  L2={lam} chosen by blocked CV on train.  CV log-loss {best[1]:.5f}  "
          f"vs the market price alone {base_ll:.5f}  "
          f"({'model beats the price' if best[1] < base_ll else 'MODEL DOES NOT BEAT THE PRICE'} in CV)")
    b = fit(Xtr, otr, ytr, lam)
    order = np.argsort(-np.abs(b[1:]))[:8]
    print("       largest standardized effects: " + ", ".join(f"{FEATS[i]} {b[1+i]:+.3f}" for i in order))
    for rs, (X, off) in ((train, (Xtr, otr)),):
        for r, p in zip(rs, predict(b, X, off)):
            r["p_hat"] = float(p)
    Xte, ote, yte, _, _ = design(test, mu, sd)
    pte = predict(b, Xte, ote)
    for r, p in zip(test, pte):
        r["p_hat"] = float(p)
    print(f"       TEST log-loss: model {logloss(pte, yte):.5f}  vs price alone "
          f"{logloss(1 / (1 + np.exp(-ote)), yte):.5f}\n")
    kel = lambda r: kelly_units(r["p_hat"], r["q"])

    # ---- 2. simple rules, the best one picked on TRAIN
    rules = {
        "qualifies + fresh": lambda r: r.get("qualifies") and r.get("fresh"),
        "qualifies + price .45-.67": lambda r: r.get("qualifies") and .45 <= r["now"] < .67,
        "qualifies + no totals": lambda r: r.get("qualifies") and mkt(r) != "total",
        "qualifies + no long dogs (<.33)": lambda r: r.get("qualifies") and r["now"] >= .33,
        "qualifies + trusted size >= 5x": lambda r: r.get("qualifies") and (r.get("vs_typical") or 0) >= 5
            and (r.get("n_open") or 0) >= U.TRUST_POSITIONS and (r.get("typical") or 0) >= U.TRUST_TYPICAL,
        "consensus >= 2 (stake>=2.5K)": lambda r: r["consensus"] >= 2 and (r.get("stake") or 0) >= 2500,
        "consensus >= 3 (stake>=2.5K)": lambda r: r["consensus"] >= 3 and (r.get("stake") or 0) >= 2500,
        "qualifies + fresh + .45-.67 + no totals": lambda r: r.get("qualifies") and r.get("fresh")
            and .45 <= r["now"] < .67 and mkt(r) != "total",
    }
    tr_scores = {n: roi([r for r in train if f(r)], flat) for n, f in rules.items()}
    tr_best = max((n for n in rules if tr_scores[n] is not None and
                   len([r for r in train if rules[n](r)]) >= 60), key=lambda n: tr_scores[n])

    # ---- 3. wallet persistence: does "who" carry from train to test?
    def wallet_roi(rs):
        g = defaultdict(list)
        for r in rs:
            g[r["wallet"]].append(r)
        return {w: (roi(v, flat), len(v)) for w, v in g.items()}
    wtr, wte = wallet_roi(train), wallet_roi(test)
    both = [w for w in wtr if w in wte and wtr[w][1] >= 40 and wte[w][1] >= 25]
    a = np.array([wtr[w][0] for w in both]); c = np.array([wte[w][0] for w in both])
    rk = lambda v: np.argsort(np.argsort(v))
    rho = float(np.corrcoef(rk(a), rk(c))[0, 1]) if len(both) > 3 else float("nan")
    top = {w for w in both if wtr[w][0] >= np.percentile(a, 75)}
    bot = {w for w in both if wtr[w][0] <= np.percentile(a, 25)}

    print("=" * 140)
    print("TRAIN (in-sample — shown only so the drop to TEST is visible)")
    report("flat, every position", train, flat)
    report("flat, qualifying only", train, q)
    report("s1 ladder (size vs typical)", train, s1)
    report("quarter-Kelly on the model", train, kel)
    for n, f in rules.items():
        report(("* " if n == tr_best else "  ") + n, train, lambda r, f=f: 1.0 if f(r) else 0.0)

    print("\n" + "=" * 140)
    print("TEST (out of sample — every choice above was frozen before this was computed)")
    res = {}
    res["flat all"] = report("flat, every position", test, flat)
    res["flat q"] = report("flat, qualifying only", test, q)
    res["s1"] = report("s1 ladder (size vs typical)", test, s1)
    res["s2"] = report("s2 ladder (+ consensus steps)", test, s2)
    res["kelly"] = report("quarter-Kelly on the model", test, kel)
    res["kelly-q"] = report("quarter-Kelly, qualifying rows only", test, lambda r: kel(r) if r.get("qualifies") else 0.0)
    print(f"  -- simple rules; '*' = the one train picked ({tr_best}); the rest are shown for honesty, not for choosing")
    for n, f in rules.items():
        res[n] = report(("* " if n == tr_best else "  ") + n, test, lambda r, f=f: 1.0 if f(r) else 0.0)
    print(f"  -- wallets: {len(both)} with enough bets in both halves; train-vs-test ROI rank correlation {rho:+.2f} "
          f"({'persistent' if rho > 0.3 else 'NOT persistent'})")
    res["top wallets"] = report("follow train's top-quartile wallets", test, lambda r: 1.0 if r["wallet"] in top else 0.0)
    res["bottom wallets"] = report("follow train's bottom-quartile wallets", test, lambda r: 1.0 if r["wallet"] in bot else 0.0)

    winners = [n for n, v in res.items() if v and v[1] and v[1][0] > 0]
    print("\n" + "=" * 140)
    if winners:
        print("SURVIVED OUT OF SAMPLE (interval excludes zero, positive): " + "; ".join(winners))
        print(f"  {len(res)} strategies were graded on TEST, so ~{len(res)*0.025:.1f} would clear that bar by luck alone.")
    else:
        print("NOTHING SURVIVED. No sizing formula or filter has a positive out-of-sample interval at a bettable price.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
