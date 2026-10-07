#!/usr/bin/env python3
"""
Can a conviction score be made predictive of ROI, out of sample?

Uses every scored research row (99 sport-months). Every feature is something
known at T-60 (no close, no CLV, no month P/L, no leaderboard rank).
Chronological split: TRAIN = windows up to 2026-03, TEST = 2026-04 onward.

  1. univariate: bin each feature on train, grade the same bins on test —
     does the ordering survive?
  2. fitted additive score (logistic regression on won with the market price
     as an offset, so the model must beat the price, not reproduce it)
  3. frozen v4 on the same rows, for comparison
  4. the only persistent trader trait (prior signal CLV) as a feature

    python3 research/score_v5.py            # prints the report
Read-only. numpy only.
"""
import glob, json, os, re, sys, random
from collections import defaultdict
import numpy as np

RES = os.path.expanduser("~/nl-research/windows")
VIG = 0.015
SPLIT = "2026-04"
MINH, MINP, MINS = 1.5, 50_000, 2_500       # research gate (cut A, minus volume)
rng = np.random.default_rng(7)


def load():
    rows = []
    for f in glob.glob(os.path.join(RES, "**", "bt_scored.jsonl"), recursive=True):
        parts = f.split(os.sep)
        ym = parts[-2]; sport = parts[-3] if re.match(r"^20\d\d-\d\d$", ym) else None
        if sport == "windows": sport = "football"
        if not re.match(r"^20\d\d-\d\d$", ym):
            continue
        for l in open(f):
            r = json.loads(l)
            if r.get("won") is None or r.get("decided") or r.get("two_sided"): continue
            px = r.get("now")
            if not px or not (0.02 < px < 0.98): continue
            if (r.get("prior_hold") or -9) < MINH or (r.get("prior_pnl") or 0) < MINP or (r.get("stake") or 0) < MINS: continue
            r["_ym"] = ym; r["_sport"] = sport or "football"
            rows.append(r)
    return rows


def consensus(rows):
    """distinct pool wallets leaning the same way on the game, per window."""
    lean = defaultdict(set)
    for r in rows:
        side = (r.get("side") or "").lower(); mkt = r.get("market") or ""
        grp = "total" if side in ("over", "under") or re.search(r"O/U|Total", mkt, re.I) else "side"
        tok = side if grp == "total" else side
        lean[(r["_ym"], r["_sport"], r["slug"], grp, tok)].add(r["wallet"])
    for r in rows:
        side = (r.get("side") or "").lower(); mkt = r.get("market") or ""
        grp = "total" if side in ("over", "under") or re.search(r"O/U|Total", mkt, re.I) else "side"
        r["_cons"] = len(lean[(r["_ym"], r["_sport"], r["slug"], grp, side)])


def prior_clv(rows):
    """each wallet's mean signal CLV over positions that KICKED OFF before this
    window started (point-in-time), and the count."""
    first = defaultdict(lambda: 10**12)
    for r in rows: first[(r["_sport"], r["_ym"])] = min(first[(r["_sport"], r["_ym"])], r["kickoff_ts"])
    byw = defaultdict(list)
    for r in rows:
        if r.get("clv_entry") is not None: byw[r["wallet"]].append((r["kickoff_ts"], r["clv_entry"]))
    for w in byw: byw[w].sort()
    for r in rows:
        t0 = first[(r["_sport"], r["_ym"])]
        hist = byw.get(r["wallet"], [])
        vals = [c for t, c in hist if t < t0]
        r["_pclv_n"] = len(vals); r["_pclv"] = (100 * sum(vals) / len(vals)) if len(vals) >= 20 else None


def grade(px, won):
    q = min(.99, px + VIG); return (1 - q) / q if won else -1.0


def roi(rs): return 100 * np.mean([grade(r["now"], r["won"]) for r in rs]) if rs else float("nan")


def ci(rs, n=400):
    g = defaultdict(list)
    for r in rs: g[(r["_ym"], r["slug"])].append(grade(r["now"], r["won"]))
    ks = list(g)
    if len(ks) < 8: return None
    out = []
    for _ in range(n):
        s = [g[ks[i]] for i in rng.integers(0, len(ks), len(ks))]
        v = [x for grp in s for x in grp]; out.append(100 * np.mean(v))
    out.sort(); return out[int(.05 * n)], out[int(.95 * n)]


def fmt(rs):
    if not rs: return "—"
    c = ci(rs); w = sum(1 for r in rs if r["won"])
    return f"n={len(rs):5} {w/len(rs):4.0%} ROI {roi(rs):+6.1f}%" + (f" [{c[0]:+.0f},{c[1]:+.0f}]" if c else "        ")


FEATS = {
    "prior_hold":  lambda r: r["prior_hold"],
    "prior_pnl":   lambda r: r["prior_pnl"],
    "stake":       lambda r: r["stake"],
    "vs_typical":  lambda r: r.get("vs_typical") if r.get("typical") and r.get("n_open", 0) >= 10 else None,
    "n_open":      lambda r: r.get("n_open"),
    "fresh":       lambda r: 1.0 if r.get("fresh") else 0.0,
    "stack":       lambda r: float(r.get("stack") or 1),
    "consensus":   lambda r: float(r["_cons"]),
    "drift_toward": lambda r: (r["now"] - r["entry"]) * 100,     # market moved toward the bet since entry
    "prior_clv":   lambda r: r["_pclv"],
    "v4_conv":     lambda r: r["conv"],
}


def univariate(train, test, label):
    print(f"\n## Univariate — {label}: bins set on TRAIN, graded on TEST")
    print("feature        | bin                 | train ROI | test ROI (n)              | monotone?")
    for name, fn in FEATS.items():
        tr = [(fn(r), r) for r in train]; tr = [(v, r) for v, r in tr if v is not None]
        te = [(fn(r), r) for r in test]; te = [(v, r) for v, r in te if v is not None]
        if len(tr) < 500 or len(te) < 500: continue
        vals = np.array([v for v, _ in tr])
        if name in ("fresh",): edges = [0.5]
        elif name in ("stack", "consensus"): edges = [1.5, 2.5]
        else: edges = list(np.quantile(vals, [.2, .4, .6, .8]))
        def b(v): return sum(1 for e in edges if v > e)
        trb = defaultdict(list); teb = defaultdict(list)
        for v, r in tr: trb[b(v)].append(r)
        for v, r in te: teb[b(v)].append(r)
        order_tr = [roi(trb[k]) for k in sorted(trb)]; order_te = [roi(teb[k]) for k in sorted(teb)]
        mono_tr = all(x <= y for x, y in zip(order_tr, order_tr[1:])) or all(x >= y for x, y in zip(order_tr, order_tr[1:]))
        same_dir = (order_tr[-1] - order_tr[0]) * (order_te[-1] - order_te[0]) > 0
        for k in sorted(trb):
            lo = edges[k - 1] if k > 0 else None; hi = edges[k] if k < len(edges) else None
            lab = (f"{lo:.3g}" if lo is not None else "-inf") + " .. " + (f"{hi:.3g}" if hi is not None else "inf")
            print(f"{name:14} | {lab:19} | {roi(trb[k]):+8.1f}% | {roi(teb[k]):+7.1f}% ({len(teb[k]):5}) |"
                  + (f" train {'mono' if mono_tr else 'not mono'}, test {'same direction' if same_dir else 'FLIPS'}" if k == 0 else ""))


def design(rows):
    X = []
    for r in rows:
        vt = r.get("vs_typical") if r.get("typical") and r.get("n_open", 0) >= 10 else 1.0
        X.append([np.log1p(max(r["prior_hold"], 0)), np.log10(max(r["prior_pnl"], 1)), np.log10(max(r["stake"], 1)),
                  np.log1p(max(vt or 1.0, 0.01)), np.log1p(r.get("n_open") or 0), 1.0 if r.get("fresh") else 0.0,
                  min(float(r.get("stack") or 1), 4) - 1, min(float(r["_cons"]), 5) - 1,
                  (r["now"] - r["entry"]) * 10, (r["_pclv"] if r["_pclv"] is not None else 0.0) / 2, 1.0 if r["_pclv"] is None else 0.0])
    return np.array(X)


NAMES = ["log hold", "log prior P/L", "log stake", "log vs_typical", "log n_open", "fresh", "stack", "consensus", "drift toward", "prior CLV", "no CLV hist"]


def fit(X, y, off, l2=1.0, iters=60):
    """logistic regression with offset (IRLS), ridge-penalised."""
    n, k = X.shape; mu = X.mean(0); sd = X.std(0) + 1e-9; Z = (X - mu) / sd
    w = np.zeros(k); b = 0.0
    for _ in range(iters):
        eta = off + Z @ w + b; p = 1 / (1 + np.exp(-eta)); W = p * (1 - p) + 1e-9
        g = Z.T @ (y - p) - l2 * w; H = (Z * W[:, None]).T @ Z + l2 * np.eye(k)
        w += np.linalg.solve(H, g); b += (y - p).sum() / W.sum()
    return w, b, mu, sd


def score(X, w, b, mu, sd, off):
    return 1 / (1 + np.exp(-(off + ((X - mu) / sd) @ w + b)))


def main():
    rows = load(); consensus(rows); prior_clv(rows)
    train = [r for r in rows if r["_ym"] < SPLIT]; test = [r for r in rows if r["_ym"] >= SPLIT]
    print(f"{len(rows)} gated rows · train {len(train)} ({len({(r['_sport'],r['_ym']) for r in train})} windows, <{SPLIT}) · test {len(test)} ({len({(r['_sport'],r['_ym']) for r in test})} windows)")
    print("test by sport:", {s: sum(1 for r in test if r['_sport'] == s) for s in sorted({r['_sport'] for r in test})})
    print(f"pool: train {fmt(train)} · test {fmt(test)}")
    win = lambda r: .55 <= r["now"] <= .643
    print(f"window −122..−180: train {fmt([r for r in train if win(r)])} · test {fmt([r for r in test if win(r)])}")

    univariate(train, test, "all gated rows")
    univariate([r for r in train if win(r)], [r for r in test if win(r)], "inside −122..−180")

    # fitted score
    print("\n## Fitted additive score (logistic on won, market price as offset, ridge)")
    for label, sel in (("all rows", lambda r: True), ("inside window", win)):
        tr = [r for r in train if sel(r)]; te = [r for r in test if sel(r)]
        Xtr, Xte = design(tr), design(te)
        ytr = np.array([1.0 if r["won"] else 0.0 for r in tr])
        otr = np.array([np.log(r["now"] / (1 - r["now"])) for r in tr]); ote = np.array([np.log(r["now"] / (1 - r["now"])) for r in te])
        w, b, mu, sd = fit(Xtr, ytr, otr)
        print(f"\n### {label} — coefficients (per 1 sd):")
        print("  " + ", ".join(f"{n} {c:+.3f}" for n, c in zip(NAMES, w)))
        for nm, rs, X, off in (("TRAIN", tr, Xtr, otr), ("TEST", te, Xte, ote)):
            p = score(X, w, b, mu, sd, off); edge = p - np.array([r["now"] for r in rs])
            qs = np.quantile(edge, [.2, .4, .6, .8])
            print(f"  {nm}: predicted edge (p̂ − price) quintiles")
            for k in range(5):
                lo = -1 if k == 0 else qs[k - 1]; hi = 1 if k == 4 else qs[k]
                sub = [r for r, e in zip(rs, edge) if lo < e <= hi]
                print(f"    Q{k+1} edge {np.mean([e for e in edge if lo < e <= hi]):+.3f}: {fmt(sub)}")
            top = [r for r, e in zip(rs, edge) if e > 0.03]
            print(f"    edge > +3 pts: {fmt(top)}")
            print(f"    by sport, edge > +3 pts: " + " · ".join(f"{s} {roi([r for r in top if r['_sport']==s]):+.0f}%({sum(1 for r in top if r['_sport']==s)})" for s in sorted({r['_sport'] for r in top})))

    # v4 for comparison on test, inside window
    print("\n## Frozen v4 on TEST inside the window, by band")
    te = [r for r in test if win(r)]
    for lo, hi in ((0, 65), (65, 70), (70, 80), (80, 90), (90, 101)):
        print(f"  v4 {lo:3}-{hi-1:3}: {fmt([r for r in te if lo <= r['conv'] < hi])}")
    print("\n## Prior signal CLV alone on TEST inside the window (point-in-time, n≥20 history)")
    for lo, hi, lab in ((-99, -1, "< -1"), (-1, 0, "-1..0"), (0, 1, "0..+1"), (1, 99, "> +1")):
        print(f"  prior CLV {lab:6}: {fmt([r for r in te if r['_pclv'] is not None and lo <= r['_pclv'] < hi])}")
    print(f"  no history      : {fmt([r for r in te if r['_pclv'] is None])}")
    clv_model(train, test)



def clv_model(rows_train, rows_test):
    """Section 5 — can we predict LINE MOVEMENT (signal CLV) instead of ROI?
    Linear ridge on clv_entry (pts). Same features, same split. Reports test
    quintiles: mean CLV, ROI at T-60 (+vig), and ROI at the sharp's ENTRY
    price (+vig) — the upper bound for a follower who gets the entry."""
    print("\n## 5. Predicting the line move (signal CLV) instead of the outcome")
    tr = [r for r in rows_train if r.get("clv_entry") is not None]; te = [r for r in rows_test if r.get("clv_entry") is not None]
    Xtr, Xte = design(tr), design(te)
    ytr = np.array([100 * r["clv_entry"] for r in tr])
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9; Z = (Xtr - mu) / sd
    w = np.linalg.solve(Z.T @ Z + 5.0 * np.eye(Z.shape[1]), Z.T @ (ytr - ytr.mean()))
    print("  coefficients (pts of CLV per 1 sd): " + ", ".join(f"{n} {c:+.2f}" for n, c in zip(NAMES, w)))
    for nm, rs, X in (("TRAIN", tr, Xtr), ("TEST", te, Xte)):
        pred = ((X - mu) / sd) @ w
        qs = np.quantile(pred, [.2, .4, .6, .8])
        print(f"  {nm}: by predicted-CLV quintile — actual CLV · ROI@T-60 · ROI@entry (upper bound)")
        for k in range(5):
            lo = -99 if k == 0 else qs[k - 1]; hi = 99 if k == 4 else qs[k]
            sub = [r for r, p in zip(rs, pred) if lo < p <= hi]
            aclv = 100 * np.mean([r["clv_entry"] for r in sub])
            r_entry = 100 * np.mean([grade(r["entry"], r["won"]) for r in sub])
            c = ci(sub)
            print(f"    Q{k+1}: CLV {aclv:+5.2f} · ROI@T-60 {roi(sub):+5.1f}% {('[%+.0f,%+.0f]' % c) if c else ''} · ROI@entry {r_entry:+5.1f}%  (n={len(sub)})")
    # inside the window, test only
    tew = [r for r in te if .55 <= r["now"] <= .643]; Xw = design(tew); pred = ((Xw - mu) / sd) @ w
    qs = np.quantile(pred, [.5, .8])
    for lab, sel in (("bottom half", lambda p: p <= qs[0]), ("top 20%", lambda p: p > qs[1])):
        sub = [r for r, p in zip(tew, pred) if sel(p)]
        print(f"  TEST inside window, {lab}: CLV {100*np.mean([r['clv_entry'] for r in sub]):+.2f} · ROI@T-60 {roi(sub):+.1f}% · ROI@entry {100*np.mean([grade(r['entry'], r['won']) for r in sub]):+.1f}% (n={len(sub)})")
    # trader prior CLV alone, test, all rows
    print("  TEST, trader prior CLV alone (point-in-time): actual CLV")
    for lo, hi, lab in ((-99, 0, "< 0"), (0, 1, "0..+1"), (1, 99, "> +1")):
        sub = [r for r in te if r["_pclv"] is not None and lo <= r["_pclv"] < hi]
        if sub: print(f"    prior CLV {lab:6}: CLV {100*np.mean([r['clv_entry'] for r in sub]):+.2f} · ROI@T-60 {roi(sub):+.1f}% · ROI@entry {100*np.mean([grade(r['entry'], r['won']) for r in sub]):+.1f}% (n={len(sub)})")


if __name__ == "__main__":
    main()
