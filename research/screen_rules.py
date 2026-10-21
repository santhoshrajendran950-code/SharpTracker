#!/usr/bin/env python3
"""
EXP-033 rule screen — hypothesis GENERATION on the 2026 football season.

    python3 screen_rules.py              # full screen, both sports
    python3 screen_rules.py --sport NFL

Reads screen_rows.jsonl (screen_features.py) and screens pre-registered
candidate rules for which sharp-followed positions should count as plays.

THIS IS NOT A VALIDATION. The output is a ranked shortlist to carry into the
year-long nl-research window. The protocol it follows, fixed before any result
was looked at:

  * NFL and NCAAF analysed separately, never pooled.
  * Universe = frozen-v4-scored positions with stake >= $2,500 (the scanner's
    own floor) and not a hedger's two-sided book. Everything below that floor
    is not a candidate play in the first place.
  * Split by TIME, not at random: earliest 60% of slates per sport is
    exploration, most recent 40% is holdout. The top 3 rules are chosen on
    exploration alone and the holdout is then reported ONCE.
  * Six pre-registered dimensions, 3 levels each. Singles first, then 2-way
    combinations across different dimensions. No dimension is added mid-run.
  * Any rule reported needs >= 30 graded positions; 30-100 is flagged
    underpowered.
  * Every rule carries n, ROI, CLV (mean and % positive), win rate and a
    game-clustered bootstrap CI on ROI.
  * Two baselines: the unfiltered universe, and the frozen v4 production filter.

Grading matches the frozen pipeline exactly so the numbers are comparable to
every earlier window: flat 1u at the T-60 price + 1.5pt vig, settled by `won`.
CLV is reported both ways — Polymarket close (every row) and sportsbook close
(only where linearchive.py had the game).
"""
import json, os, random, statistics as st, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = os.path.join(HERE, "screen_rows.jsonl")
VIG = 0.015
MIN_N = 30                # never report a rule thinner than this
MIN_GAMES = 10            # ...and never on fewer independent games than this
UNDERPOWERED = 100        # flag anything below this
BOOT = 2000
EXPLORE_FRAC = 0.60

# ---------------------------------------------------------------- dimensions
# Pre-registered: six dimensions, three levels each. Levels are written as
# (label, predicate) and nothing is added to this table after the first run.
DIMS = {
    "trail": [("trail>=2", lambda r: r["trail"] >= 2),
              ("trail>=3", lambda r: r["trail"] >= 3),
              ("trail>=4", lambda r: r["trail"] >= 4)],
    "quality": [("prior>=50K", lambda r: r["quality"] >= 50_000),
                ("prior>=150K", lambda r: r["quality"] >= 150_000),
                ("prior>=250K", lambda r: r["quality"] >= 250_000)],
    "depth": [("depth>=25", lambda r: (r["depth"] or 0) >= 25),
              ("depth>=50", lambda r: (r["depth"] or 0) >= 50),
              ("depth>=100", lambda r: (r["depth"] or 0) >= 100)],
    # price bands on the T-60 price. -122..-180 is the live rule; the variants
    # are one wider and one narrower, as registered.
    "price": [("px -122..-180", lambda r: 0.549 <= r["price"] <= 0.643),
              ("px -110..-250 wide", lambda r: 0.524 <= r["price"] <= 0.714),
              ("px -130..-165 narrow", lambda r: 0.565 <= r["price"] <= 0.623)],
    "relstake": [("stake>=2x own", lambda r: (r["relstake"] or 0) >= 2),
                 ("stake>=5x own", lambda r: (r["relstake"] or 0) >= 5),
                 ("stake>=10x own", lambda r: (r["relstake"] or 0) >= 10)],
    # lead = hours between the wallet's first buy and kickoff. Two "early"
    # levels and one "late money" level.
    "lead": [("lead>=6h", lambda r: r["lead_h"] is not None and r["lead_h"] >= 6),
             ("lead>=24h", lambda r: r["lead_h"] is not None and r["lead_h"] >= 24),
             ("lead<3h late", lambda r: r["lead_h"] is not None and r["lead_h"] < 3)],
}


# ---------------------------------------------------------------- grading
def q_of(r):
    return min(r["now"] + VIG, 0.985)


def pl(r):
    q = q_of(r)
    return (1 - q) / q if r["won"] else -1.0


def roi(rs):
    return 100.0 * sum(pl(r) for r in rs) / len(rs) if rs else None


def boot_ci(rs, lo=2.5, hi=97.5):
    """Game-clustered bootstrap on ROI. Positions in one game share an outcome,
    so resampling positions would understate the interval badly."""
    if len(rs) < 2:
        return None
    g = defaultdict(list)
    for r in rs:
        g[r["slug"]].append(r)
    ks = list(g)
    if len(ks) < 2:
        return None
    rnd = random.Random(1234)
    out = []
    for _ in range(BOOT):
        samp = [x for k in rnd.choices(ks, k=len(ks)) for x in g[k]]
        out.append(roi(samp))
    out.sort()
    return out[int(len(out) * lo / 100)], out[int(len(out) * hi / 100)]


def stats(rs):
    pm = [r["clv_pm"] * 100 for r in rs if r.get("clv_pm") is not None]
    bk = [r["clv_book"] for r in rs if r.get("clv_book") is not None]
    ci = boot_ci(rs)
    return {
        "n": len(rs), "games": len({r["slug"] for r in rs}),
        "wallets": len({r["wallet"] for r in rs}),
        "wins": sum(1 for r in rs if r["won"]),
        "winrate": 100.0 * sum(1 for r in rs if r["won"]) / len(rs) if rs else None,
        "roi": roi(rs), "ci": ci,
        "clv_pm": st.mean(pm) if pm else None,
        "clv_pm_pos": 100.0 * sum(1 for x in pm if x > 0) / len(pm) if pm else None,
        "clv_book": st.mean(bk) if bk else None,
        "clv_book_pos": 100.0 * sum(1 for x in bk if x > 0) / len(bk) if bk else None,
        "clv_book_n": len(bk),
    }


# ---------------------------------------------------------------- rules
def rules():
    """Every configuration evaluated: 18 singles + all 2-way pairs across
    different dimensions (3x3 each). Returns [(label, predicate, kind)]."""
    out = []
    names = list(DIMS)
    for d in names:
        for lab, fn in DIMS[d]:
            out.append((lab, fn, "single"))
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for la, fa in DIMS[a]:
                for lb, fb in DIMS[b]:
                    out.append((f"{la} + {lb}",
                                (lambda fa=fa, fb=fb: lambda r: fa(r) and fb(r))(),
                                "pair"))
    return out


def slate_split(rows):
    """Earliest 60% of distinct slate dates = exploration. Split on dates, not
    positions, so a single big slate cannot straddle the boundary."""
    dates = sorted({r["date"] for r in rows})
    cut = max(1, int(round(len(dates) * EXPLORE_FRAC)))
    ex = set(dates[:cut])
    return ([r for r in rows if r["date"] in ex],
            [r for r in rows if r["date"] not in ex],
            dates[:cut], dates[cut:])


def composite(cands):
    """Rank with ROI and CLV weighted equally, as registered. Both are
    z-scored across the candidate rules on the exploration split, so the two
    scales (percent vs points) cannot dominate each other by units alone.
    Polymarket CLV is used for ranking because every row has it."""
    rois = [c["ex"]["roi"] for c in cands]
    clvs = [c["ex"]["clv_pm"] for c in cands]
    def z(v, xs):
        m, s = st.mean(xs), (st.pstdev(xs) or 1.0)
        return (v - m) / s
    for c in cands:
        c["score"] = (z(c["ex"]["roi"], rois) + z(c["ex"]["clv_pm"], clvs)) / 2
    return sorted(cands, key=lambda c: -c["score"])


def null_best(ex, cands, reps=400):
    """How good would the BEST of these rules look if no rule had any edge?

    The protocol asks how much of any apparent edge could be a
    multiple-comparisons artifact. Shuffling outcomes would answer a different
    question, because favourites really do win more often, so instead this
    simulates a market that is exactly efficient: every position settles from
    its own T-60 price, which is the price the screen also bets at. Any ROI that
    appears is then selection alone.

    Correlation is kept, because it is what makes selection dangerous here:
    positions on one lean in one game (a team's ML and its spread) settle
    together, and the opposing side settles against them. Each (game, lean)
    draws one uniform; a token on the reference side wins at u <= p, a token on
    the other side wins at u > 1 - p, which is exact complementarity for a
    two-way market.
    """
    groups = defaultdict(list)
    for r in ex:
        grp = "total" if "O/U" in (r["market"] or "") else "side"
        groups[(r["slug"], grp)].append(r)
    plan = []
    for (slug, grp), rs in groups.items():
        ref = sorted({r["side"] for r in rs})[0]
        plan.append((rs, ref))
    rnd = random.Random(99)
    best_roi, best_score = [], []
    for _ in range(reps):
        for rs, ref in plan:
            u = rnd.random()
            for r in rs:
                p = r["now"]
                r["_won"] = (u <= p) if r["side"] == ref else (u > 1 - p)
        rois = []
        for c in cands:
            hit = [r for r in ex if c["fn"](r)]
            if len(hit) < MIN_N or len({r["slug"] for r in hit}) < MIN_GAMES:
                continue
            q = [min(r["now"] + VIG, 0.985) for r in hit]
            v = 100.0 * sum((1 - qq) / qq if r["_won"] else -1.0
                            for r, qq in zip(hit, q)) / len(hit)
            rois.append(v)
        if rois:
            best_roi.append(max(rois))
    for r in ex:
        r.pop("_won", None)
    best_roi.sort()
    def pct(p):
        return best_roi[int(len(best_roi) * p)] if best_roi else None
    return {"reps": len(best_roi), "p50": pct(.50), "p90": pct(.90),
            "p95": pct(.95), "max": best_roi[-1] if best_roi else None}


def fmt(s):
    ci = f"[{s['ci'][0]:+.0f},{s['ci'][1]:+.0f}]" if s["ci"] else "n/a"
    bk = (f"{s['clv_book']:+.2f} ({s['clv_book_pos']:.0f}% +, n={s['clv_book_n']})"
          if s["clv_book"] is not None else "—")
    return (f"n={s['n']:4d} g={s['games']:3d} {s['wins']:3d}-{s['n']-s['wins']:<3d} "
            f"win {s['winrate']:4.1f}%  ROI {s['roi']:+7.1f}% CI {ci:>14}  "
            f"CLVpm {s['clv_pm']:+5.2f} ({s['clv_pm_pos']:4.1f}% +)  CLVbook {bk}")


def run_sport(rows, sport, out):
    p = out.append
    ex, ho, exd, hod = slate_split(rows)
    p(f"\n{'='*118}\n{sport} · {len(rows)} candidate positions · "
      f"{len({r['slug'] for r in rows})} games · {len({r['date'] for r in rows})} slates")
    p(f"  exploration: {len(exd)} slates {exd[0]}..{exd[-1]} · {len(ex)} positions")
    p(f"  holdout    : {len(hod)} slates {hod[0]}..{hod[-1]} · {len(ho)} positions")
    if not ex or not ho:
        p("  !! one side of the split is empty — no screen possible")
        return None

    p(f"\n-- baselines")
    base = {}
    for lab, rs_ex, rs_ho in (
            ("(a) every candidate, unfiltered", ex, ho),
            ("(b) frozen v4 production filter", [r for r in ex if r["qualifies"]],
             [r for r in ho if r["qualifies"]])):
        base[lab] = {"ex": stats(rs_ex) if rs_ex else None,
                     "ho": stats(rs_ho) if rs_ho else None}
        p(f"  {lab:34} EX {fmt(base[lab]['ex']) if base[lab]['ex'] else 'empty'}")
        p(f"  {'':34} HO {fmt(base[lab]['ho']) if base[lab]['ho'] else 'empty'}")

    allr = rules()
    cands, dropped = [], defaultdict(int)
    for lab, fn, kind in allr:
        hit = [r for r in ex if fn(r)]
        if len(hit) < MIN_N:
            dropped["positions"] += 1
            continue
        if len({r["slug"] for r in hit}) < MIN_GAMES:
            dropped["games"] += 1
            continue
        cands.append({"label": lab, "kind": kind, "fn": fn, "ex": stats(hit)})
    p(f"\n-- configurations: {len(allr)} evaluated "
      f"({sum(1 for x in allr if x[2]=='single')} single, {sum(1 for x in allr if x[2]=='pair')} pair) · "
      f"{len(cands)} reportable on exploration · dropped {dropped['positions']} under "
      f"{MIN_N} positions, {dropped['games']} under {MIN_GAMES} independent games")
    if dropped["games"]:
        p(f"  the games floor is a DEVIATION from the pre-registered protocol, which set only a "
          f"30-position floor. It was added because positions are not independent observations: "
          f"on this split `trail>=3` had 42 NFL positions in ONE game, so the position floor "
          f"passed a rule that is a single coin flip. Games are the clustering unit the CIs use.")

    nb = null_best(ex, cands)
    p(f"\n-- selection check: best-of-{len(cands)} ROI on exploration when NO rule has an edge")
    p(f"  efficient-market simulation, {nb['reps']} reps: median best {nb['p50']:+.1f}%, "
      f"90th {nb['p90']:+.1f}%, 95th {nb['p95']:+.1f}%, worst case {nb['max']:+.1f}%")
    p(f"  any exploration ROI at or below the 90th percentile above is indistinguishable from "
      f"picking the luckiest of {len(cands)} rules")

    ranked = composite(cands)
    p(f"\n-- exploration ranking (ROI and CLV weighted equally, z-scored; top 12 of {len(ranked)})")
    for c in ranked[:12]:
        p(f"  {c['score']:+5.2f} {c['label']:38} {fmt(c['ex'])}")

    top3 = ranked[:3]
    p(f"\n-- HOLDOUT, reported once, for the 3 rules picked on exploration only")
    for c in top3:
        hit = [r for r in ho if c["fn"](r)]
        c["ho"] = stats(hit) if len(hit) >= 2 else None
        flag = ""
        if c["ho"] and c["ho"]["n"] < UNDERPOWERED:
            flag = "  << UNDERPOWERED"
        if c["ho"] and c["ho"]["n"] < MIN_N:
            flag = "  << BELOW THE 30-POSITION FLOOR, not reportable"
        p(f"  {c['label']:38} EX {fmt(c['ex'])}")
        p(f"  {'':38} HO {(fmt(c['ho']) + flag) if c['ho'] else 'too few positions'}")
        for blab, bk in (("(a) unfiltered", "(a) every candidate, unfiltered"),
                         ("(b) v4 filter", "(b) frozen v4 production filter")):
            b = base[bk]["ho"]
            if c["ho"] and b:
                p(f"  {'':38}    vs {blab}: ROI {c['ho']['roi'] - b['roi']:+.1f} pts, "
                  f"CLVpm {c['ho']['clv_pm'] - b['clv_pm']:+.2f} pts on the holdout")
    return {"sport": sport, "explore_slates": exd, "holdout_slates": hod,
            "baselines": base, "n_configs": len(allr), "n_cands": len(cands),
            "null_best": nb,
            "top3": [{"label": c["label"], "kind": c["kind"], "score": c["score"],
                      "ex": c["ex"], "ho": c.get("ho")} for c in ranked[:3]],
            "ranked": [{"label": c["label"], "score": c["score"], "ex": c["ex"]}
                       for c in ranked]}


def main(a):
    rows = [json.loads(l) for l in open(ROWS)]
    universe = [r for r in rows if r["stake"] >= 2500]
    out = []
    out.append("EXP-033 · candidate-rule screen · 2026 football season")
    out.append(f"source {ROWS} · {len(rows)} scored rows · "
               f"{len(universe)} at or above the $2,500 scanner floor")
    out.append(f"grading: flat 1u at the T-60 price + {VIG*100:.1f}pt vig, settled outcome; "
               f"CLV in percentage points; CIs are {BOOT}-rep game-clustered bootstrap, 95%")
    # ---- FIRST STEP, before any rule is looked at: is there enough here at all?
    out.append(f"\n{'='*118}\nCENSUS (the protocol's first step)")
    out.append(f"  {'sport':6} {'slates':>6} {'games':>6} {'cand pos':>9} {'qualifying':>10} "
               f"{'med pos/slate':>13} {'wallets':>7}  dates")
    census = {}
    for sport in ("NFL", "NCAAF"):
        rs = [r for r in universe if r["league"] == sport]
        if not rs:
            out.append(f"  {sport:6} none")
            continue
        per = defaultdict(int)
        for r in rs:
            per[r["date"]] += 1
        dates = sorted(per)
        dow = defaultdict(int)
        for d in dates:
            import datetime as _dt
            dow[_dt.date.fromisoformat(d).strftime("%a")] += 1
        census[sport] = {
            "slates": len(dates), "games": len({r["slug"] for r in rs}),
            "candidates": len(rs), "qualifying": sum(1 for r in rs if r["qualifies"]),
            "median_per_slate": st.median(per.values()),
            "wallets": len({r["wallet"] for r in rs}),
            "dates": dates, "dow": dict(dow),
        }
        c = census[sport]
        out.append(f"  {sport:6} {c['slates']:6d} {c['games']:6d} {c['candidates']:9d} "
                   f"{c['qualifying']:10d} {c['median_per_slate']:13.0f} {c['wallets']:7d}  "
                   f"{dates[0]}..{dates[-1]}")
        out.append(f"  {'':6} slate days: " + ", ".join(f"{k} {v}" for k, v in
                   sorted(c["dow"].items(), key=lambda kv: -kv[1])))
        if c["candidates"] < 150:
            out.append(f"  {'':6} !! under ~150 candidate positions — every result for "
                       f"{sport} below is DIRECTIONAL ONLY")
        if c["qualifying"] < 150:
            out.append(f"  {'':6} !! only {c['qualifying']} positions clear the frozen v4 filter "
                       f"(<150) — baseline (b) for {sport} is DIRECTIONAL ONLY, and so is any "
                       f"comparison against it")
    res = {"census": census}
    want = a[a.index("--sport") + 1] if "--sport" in a else None
    for sport in ("NFL", "NCAAF"):
        if want and sport != want:
            continue
        rs = [r for r in universe if r["league"] == sport]
        if not rs:
            out.append(f"\n{sport}: no candidate positions")
            continue
        res[sport] = run_sport(rs, sport, out)
    txt = "\n".join(out)
    print(txt)
    open(os.path.join(HERE, "screen_report.txt"), "w").write(txt + "\n")
    json.dump(res, open(os.path.join(HERE, "screen_results.json"), "w"), indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
