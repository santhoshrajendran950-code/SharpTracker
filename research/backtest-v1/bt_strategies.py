#!/usr/bin/env python3
"""
Stage 7 — several families of strategy, side by side, on one window.

    python3 bt_strategies.py                 # the Aug16-Sep15 window (T-60 priced)
    python3 bt_strategies.py win2            # a rebuilt window in ./win2 (entry priced)

Four families, each graded at the same bettable price (T-60 or entry, + VIG):

  SIZING     the same qualifying plays under flat 1u, s1, s2, s3, s4 — units.py
             replayed with its version switches, not reimplemented
  SELECTION  which flags to act on, flat 1u
  WALLETS    ignore the score, follow N wallets chosen on their PRIOR record
  TIMING     what this data can say: fresh vs stale, and whether the price had
             already moved toward the sharp by T. A true earlier-snapshot test
             needs bt_rebuild at T=180, which this window does not have.

Two readings of every family:

  DESCRIPTIVE  the whole window, game-clustered 95% CI. Fast, and the best row
               in a table of thirty is partly luck — hypothesis generation.
  OUT OF SAMPLE  the family's best row is chosen on the first 60% of games and
               reported on the last 40%, once. What survives that is real.

For sizing, drawdown and the longest losing run are printed too, because two
rules with the same ROI can feel completely different to bet through.
"""
import json, math, os, random, re, sys
from collections import defaultdict

sys.path.insert(0, "/Users/santhosh/.nightly-line")
import units as U

VIG = 0.015
TRAIN_FRAC = 0.60
BOOT = 2000
MIN_BETS = 30
random.seed(20260922)


# ------------------------------------------------------------------ data
def mkt(r):
    m = r.get("market") or ""
    return "total" if re.search(r"O/U|Total", m, re.I) else "spread" if "Spread" in m else "ml"


def lean(r):
    side = (r.get("side") or "").strip().lower()
    return ("total", side) if mkt(r) == "total" else ("side", side)


def load(d):
    rows = [json.loads(l) for l in open(os.path.join(d, "bt_scored.jsonl"))]
    rows = [r for r in rows if r.get("won") is not None and 0 < (r.get("now") or 0) < 1]
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
        r["q"] = min(r["now"] + VIG, 0.985)
        r["trusted_base"] = ((r.get("n_open") or 0) >= U.TRUST_POSITIONS
                             and (r.get("typical") or 0) >= U.TRUST_TYPICAL)
    rows.sort(key=lambda r: (r["kickoff_ts"], r["slug"]))
    basis = "entry" if any(r.get("price_basis") == "entry" for r in rows) else "T-60"
    return rows, basis


def split(rows):
    games = sorted({r["slug"]: r["kickoff_ts"] for r in rows}.items(), key=lambda kv: kv[1])
    cut = games[int(len(games) * TRAIN_FRAC)][1]
    return [r for r in rows if r["kickoff_ts"] < cut], [r for r in rows if r["kickoff_ts"] >= cut]


# --------------------------------------------------------------- grading
def pl(r, u):
    return u * (1 / r["q"] - 1) if r["won"] else -u


def roi(bets):
    risk = sum(u for _, u in bets)
    return (sum(pl(r, u) for r, u in bets) / risk * 100) if risk > 0 else None


def ci(bets):
    g = defaultdict(list)
    for r, u in bets:
        g[r["slug"]].append((r, u))
    ks = list(g)
    if len(ks) < 15:
        return None
    out = []
    for _ in range(BOOT):
        v = roi([x for k in random.choices(ks, k=len(ks)) for x in g[k]])
        if v is not None:
            out.append(v)
    out.sort()
    return out[int(.025 * len(out))], out[int(.975 * len(out))]


def path_stats(bets):
    """Max drawdown (units) and longest losing run, in kickoff order."""
    bets = sorted(bets, key=lambda t: (t[0]["kickoff_ts"], t[0]["slug"]))
    bal = peak = dd = 0.0
    run = worst = 0
    for r, u in bets:
        bal += pl(r, u)
        peak = max(peak, bal)
        dd = max(dd, peak - bal)
        run = run + 1 if not r["won"] else 0
        worst = max(worst, run)
    return dd, worst


def row(name, bets, path=False):
    bets = [(r, u) for r, u in bets if u > 0]
    if len(bets) < MIN_BETS:
        print(f"  {name:40} bets {len(bets):5}  (under {MIN_BETS}, not scored)")
        return None
    v, c = roi(bets), ci(bets)
    games = len({r["slug"] for r, _ in bets})
    units = sum(u for _, u in bets)
    net = sum(pl(r, u) for r, u in bets)
    tag = ""
    if c:
        tag = "  POSITIVE" if c[0] > 0 else "  negative" if c[1] < 0 else ""
    extra = ""
    if path:
        dd, run = path_stats(bets)
        extra = f"  maxDD {dd:6.1f}u  worst run {run:2}"
    print(f"  {name:40} bets {len(bets):5} games {games:5} risked {units:7.0f}u "
          f"net {net:+8.1f}u  ROI {v:+6.1f}%  {'CI [%+.1f, %+.1f]' % c if c else 'CI n/a':20}{extra}{tag}")
    return v, c, len(bets)


# ------------------------------------------------------------- families
def sizing_family():
    """name -> (rows) -> [(row, units)]"""
    def with_version(cfg):
        def f(rows):
            saved = {k: getattr(U, k) for k in cfg if not k.startswith('_')}
            for k, v in saved.items():
                setattr(U, k, cfg[k])
            try:
                out = []
                for r in rows:
                    if not r.get("qualifies"):
                        continue
                    h = dict(r)
                    if not cfg.get("_consensus", True):
                        h["consensus"] = 1
                    out.append((r, U.recommend(h)[0]))
                return out
            finally:
                for k, v in saved.items():
                    setattr(U, k, v)
        return f
    base = dict(TIERS=False, USE_FORM=False, CONSENSUS_STEPS=[], SPORT_MULT={},
                TOTAL_MULT=1.0, LONGDOG_MULT=1.0, _consensus=False)
    return {
        "flat 1u (control)": lambda rows: [(r, 1.0) for r in rows if r.get("qualifies")],
        "s1  size-vs-typical ladder": with_version(base),
        "s2  + consensus steps": with_version({**base, "CONSENSUS_STEPS": [(4, 2), (3, 1)], "_consensus": True}),
        "s3  + his sheet (NFL/total/longdog)": with_version({**base, "CONSENSUS_STEPS": [(3, 1)], "_consensus": True,
                                                            "SPORT_MULT": {"NFL": 2.0}, "TOTAL_MULT": 0.75, "LONGDOG_MULT": 0.6}),
        "s4  core/rest tiers (live)": with_version({"_consensus": True}),
    }


def selection_family():
    Q = lambda r: bool(r.get("qualifies"))
    tv = lambda r: r.get("vs_typical") if r["trusted_base"] else None
    rules = {
        "all qualifying (baseline)": Q,
        "qualifying, conv >= 80": lambda r: Q(r) and r["conv"] >= 80,
        "qualifying, price .45-.67": lambda r: Q(r) and .45 <= r["now"] < .67,
        "qualifying, +150 or shorter (s4 gate)": lambda r: Q(r) and r["now"] >= .40,
        "qualifying, favourites only (>.5)": lambda r: Q(r) and r["now"] > .5,
        "qualifying, dogs only (<.5)": lambda r: Q(r) and r["now"] < .5,
        "qualifying, moneylines": lambda r: Q(r) and mkt(r) == "ml",
        "qualifying, spreads": lambda r: Q(r) and mkt(r) == "spread",
        "qualifying, totals": lambda r: Q(r) and mkt(r) == "total",
        "qualifying, no totals": lambda r: Q(r) and mkt(r) != "total",
        "qualifying, hold >= 3%": lambda r: Q(r) and r["hold"] >= 3,
        "qualifying, hold >= 5%": lambda r: Q(r) and r["hold"] >= 5,
        "qualifying, stake >= $10K": lambda r: Q(r) and r["stake"] >= 1e4,
        "qualifying, stake >= $25K": lambda r: Q(r) and r["stake"] >= 2.5e4,
        "qualifying, 2x-5x their median": lambda r: Q(r) and tv(r) is not None and 2 <= tv(r) < 5,
        "qualifying, 5x+ their median": lambda r: Q(r) and tv(r) is not None and tv(r) >= 5,
        "qualifying, 15x+ their median": lambda r: Q(r) and tv(r) is not None and tv(r) >= 15,
        "qualifying, consensus >= 2": lambda r: Q(r) and r["consensus"] >= 2,
        "qualifying, consensus >= 3": lambda r: Q(r) and r["consensus"] >= 3,
        "qualifying, unopposed": lambda r: Q(r) and r["opposed"] == 0,
        "qualifying, stacked (x2+)": lambda r: Q(r) and (r.get("stack") or 1) >= 2,
        "s4 CORE definition": lambda r: Q(r) and U.is_core(dict(r, user=None)),
        "his sheet: short price, no total, ML/spread": lambda r: Q(r) and r["now"] >= .40 and mkt(r) != "total",
    }
    for lg in ("MLB", "SOCCER", "ATP", "WTA", "NCAAF", "NFL"):
        rules[f"qualifying, {lg}"] = (lambda lg: lambda r: Q(r) and r["league"] == lg)(lg)
    return {n: (lambda f: lambda rows: [(r, 1.0) for r in rows if f(r)])(f) for n, f in rules.items()}


def wallet_family(rows):
    """Wallets ranked on PRIOR record only (bt_pool subtracted the window)."""
    prior = {}
    for r in rows:
        prior[r["wallet"]] = (r.get("prior_hold") or 0, r.get("prior_pnl") or 0)
    by_hold = sorted(prior, key=lambda w: -prior[w][0])
    by_pnl = sorted(prior, key=lambda w: -prior[w][1])
    big = lambda r: (r.get("stake") or 0) >= 2500 and not r.get("two_sided") and not r.get("decided")
    fam = {}
    for n in (5, 10, 25):
        top = set(by_hold[:n])
        fam[f"top {n} wallets by prior hold, $2.5K+"] = (lambda top: lambda rows: [(r, 1.0) for r in rows if big(r) and r["wallet"] in top])(top)
    for n in (5, 10, 25):
        top = set(by_pnl[:n])
        fam[f"top {n} wallets by prior P/L, $2.5K+"] = (lambda top: lambda rows: [(r, 1.0) for r in rows if big(r) and r["wallet"] in top])(top)
    fam["every gated wallet, $2.5K+, no score"] = lambda rows: [(r, 1.0) for r in rows if big(r) and r["hold"] >= 1.5 and r["pnl"] >= 250000]
    fam["hold >= 5% wallets, $2.5K+, no score"] = lambda rows: [(r, 1.0) for r in rows if big(r) and r["hold"] >= 5]
    names = {"Soarin22", "Kch-Temp"}
    fam["trusted.json names, $2.5K+"] = lambda rows: [(r, 1.0) for r in rows if big(r) and str(r.get("user")) in names]
    return fam


def timing_family(basis):
    Q = lambda r: bool(r.get("qualifies"))
    fam = {"qualifying, fresh (price within 10% of entry)": lambda rows: [(r, 1.0) for r in rows if Q(r) and r.get("fresh")],
           "qualifying, stale": lambda rows: [(r, 1.0) for r in rows if Q(r) and not r.get("fresh")]}
    if basis == "T-60":
        mv = lambda r: r["now"] - r["entry"]
        fam["qualifying, market moved TOWARD them by T (>+2pt)"] = lambda rows: [(r, 1.0) for r in rows if Q(r) and mv(r) > .02]
        fam["qualifying, market moved AGAINST them by T (<-2pt)"] = lambda rows: [(r, 1.0) for r in rows if Q(r) and mv(r) < -.02]
        fam["qualifying, price unchanged (+-2pt)"] = lambda rows: [(r, 1.0) for r in rows if Q(r) and abs(mv(r)) <= .02]
        fam["at the sharp's ENTRY price instead (upper bound)"] = lambda rows: [(dict(r, q=min(r["entry"] + VIG, .985)), 1.0) for r in rows if Q(r)]
    return fam


# ------------------------------------------------------------------ main
def main():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    rows, basis = load(d)
    train, test = split(rows)
    print(f"WINDOW {os.path.basename(d) or 'main'} · {len(rows)} positions · {len({r['slug'] for r in rows})} games · "
          f"{len({r['wallet'] for r in rows})} wallets · graded at {basis} price + {VIG*100:.1f}pt vig\n"
          f"TRAIN {len({r['slug'] for r in train})} games / TEST {len({r['slug'] for r in test})} games")
    families = [("SIZING", sizing_family(), True), ("SELECTION", selection_family(), False),
                ("WALLETS", wallet_family(rows), False), ("TIMING", timing_family(basis), False)]
    verdict = []
    for fam_name, fam, path in families:
        print("\n" + "=" * 150 + f"\n{fam_name} — DESCRIPTIVE, whole window")
        tr = {}
        for n, f in fam.items():
            row(n, f(rows), path)
            v = roi([(r, u) for r, u in f(train) if u > 0])
            if v is not None and len([1 for r, u in f(train) if u > 0]) >= MIN_BETS:
                tr[n] = v
        if not tr:
            continue
        best = max(tr, key=tr.get)
        print(f"\n{fam_name} — OUT OF SAMPLE: train picked '{best}' ({tr[best]:+.1f}% on train); its TEST result:")
        res = row(best, fam[best](test), path)
        base_name = next((n for n in fam if "control" in n or "baseline" in n), None)
        if base_name and base_name != best:
            row(f"(same test games, {base_name})", fam[base_name](test), path)
        verdict.append((fam_name, best, tr[best], res))
    print("\n" + "=" * 150 + "\nVERDICT")
    for fam_name, best, trv, res in verdict:
        if not res:
            print(f"  {fam_name:10} {best}: too few test bets")
            continue
        v, c, n = res
        st = ("SURVIVED" if c and c[0] > 0 else "did not survive" if c else "n/a")
        print(f"  {fam_name:10} {best:44} train {trv:+6.1f}%  ->  test {v:+6.1f}% {('[%+.1f, %+.1f]' % c) if c else ''}  {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
