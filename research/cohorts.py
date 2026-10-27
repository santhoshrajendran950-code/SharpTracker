#!/usr/bin/env python3
"""EXP-036: consensus-vs-opposition cohorts, measured on the research corpus.

    python3 research/cohorts.py [--out results/exp036-cohorts.md] [--quick]

Santhosh, 2026-10-27: "trail Kch-Temp and primm ... more conviction if there's
multiple users behind the same side with no opposing significant positions
(user profile, bet size relative to user avg) ... 3 sharps vs 1 insignificant
sharp, 2 sharps vs 1 insignificant ... different hold, P/L thresholds ... look
primarily at Sundays, Saturdays, Mondays, Thursdays, then the rest."

A "sharp" here is a wallet whose PRIOR hold and PRIOR P/L clear a profile bar
and whose stake is above MULT x its own expanding career median (xrule.py).
"Significant" opposition = a sharp clearing the same bars on another side of
the same market. "Insignificant" = any other $2.5K+ wallet on that side.

PART 1  the matrix: sharps on the side (1..4+) x what sits on the other side
        (nothing / insignificant only, <=1 / insignificant only, any number /
        a significant sharp), at every profile bar in the grid.
PART 2  fifteen named cohorts (C01..C15), each at the default bar and swept
        across the grid; the sweep's MEDIAN is the robust number, not its best.
PART 3  day of week (kickoff, US Eastern), in Santhosh's priority order.
PART 4  the multiple-testing check and the later-35% out-of-sample slice.

Every play is one (slug, market, side), graded two ways:
  follow  flat 1u at the T-60 price + 1.5 pts vig: what a follower could get.
  entry   flat 1u at the first sharp's own entry + vig: the optimistic bound.
CLV is close - T-60 price (the follower's CLV), in points. Judge on follow
ROI and CLV; the entry column is shown only so it can be ignored.

Hold and P/L on a research row are PRIOR values (bt_score.py), career medians
and CLV / league records are expanding over games that KICKED OFF before this
wager, so nothing looks ahead. Read-only; writes only the --out report.
"""
import math, os, random, statistics, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xrule as X

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                       # no tzdata: EST all year, off by 1h in summer
    ET = timezone(timedelta(hours=-5))

VIG = X.VIG
BOOT_N = 400 if "--quick" in sys.argv else 1500
OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None

# profile grid: prior hold %, prior lifetime P/L $, x own career median
HOLDS = (0.0, 1.5, 3.0, 5.0)
PNLS = (0, 50_000, 100_000, 250_000)
MULTS = (1.0, 2.0)
DEFAULT = (3.0, 100_000, 1.0)

ANCHORS = ("Kch-Temp", "primm")
DAYS = ("Sun", "Sat", "Mon", "Thu", "rest")
MIN_CLV_HIST = 30
OOS_FRAC = 0.35


# --- per-row features (expanding, no look-ahead) ----------------------------
def add_history(rows):
    """_clv_n/_clv: the wallet's mean CLV-at-entry (pts) over its earlier
    wagers whose game had already kicked off. _lg_n/_lg_roi: same for flat ROI
    in this league."""
    by = defaultdict(list)
    for r in rows:
        by[r["wallet"]].append(r)
    for rs in by.values():
        done = sorted(rs, key=lambda x: x.get("kickoff_ts") or 0)
        todo = sorted(rs, key=lambda x: x.get("t_ts") or 0)
        i, cn, cs, lg = 0, 0, 0.0, defaultdict(lambda: [0, 0.0])
        for r in todo:
            t = r.get("t_ts") or 0
            while i < len(done) and (done[i].get("kickoff_ts") or 0) < t:
                d = done[i]; i += 1
                if d.get("clv_entry") is not None:
                    cn += 1; cs += d["clv_entry"] * 100
                f = X.flat(d)
                if f is not None and d.get("won") is not None:
                    a = lg[d.get("league")]; a[0] += 1; a[1] += f
            r["_clv_n"], r["_clv"] = cn, (cs / cn if cn else None)
            a = lg[r.get("league")]
            r["_lg_n"], r["_lg_roi"] = a[0], (a[1] / a[0] if a[0] else None)
    return rows


def day(ts):
    if not ts:
        return "rest"
    d = datetime.fromtimestamp(ts, ET).strftime("%a")
    return d if d in DAYS else "rest"


def pregame(r):
    return (not r.get("decided") and not r.get("two_sided")
            and (r.get("t_ts") or 0) < (r.get("kickoff_ts") or 0)
            and r.get("now") and 0 < r["now"] < 1)


def rel(r):
    return r.get("_rel") or 0


def bar(hold, pnl, mult):
    return lambda r: ((r.get("hold") or -99) > hold and (r.get("pnl") or 0) > pnl
                      and rel(r) > mult)


# --- markets ----------------------------------------------------------------
class Market:
    __slots__ = ("slug", "market", "sides")

    def __init__(self, slug, market):
        self.slug, self.market, self.sides = slug, market, defaultdict(list)


def markets(rows):
    m = {}
    for r in rows:
        if not pregame(r):
            continue
        k = (r["slug"], r.get("market"))
        if k not in m:
            m[k] = Market(*k)
        m[k].sides[r.get("side")].append(r)
    return list(m.values())


def grade(price, won):
    q = min(.99, price + VIG)
    return (1 - q) / q if won else -1.0


def play(on, extra=None):
    """One play from the rows backing a side; the lead is the earliest sharp."""
    on = sorted(on, key=lambda x: x.get("t_ts") or 0)
    lead = on[0]
    e = lead.get("entry") or 0
    if e <= 0 or lead.get("won") is None:
        return None
    won = bool(lead["won"])
    p = {"slug": lead["slug"], "won": won, "t_ts": lead.get("t_ts") or 0,
         "follow": grade(lead["now"], won), "entry": grade(e, won),
         "clv": (lead["close"] - lead["now"]) * 100 if lead.get("close") is not None else None,
         "px": lead["now"], "league": lead.get("league"),
         "day": day(lead.get("kickoff_ts")), "n": len({r["wallet"] for r in on})}
    if extra:
        p.update(extra)
    return p


def wallets(rs):
    return {r["wallet"] for r in rs}


# --- PART 1: the matrix ------------------------------------------------------
OPP = ("none", "<=1 insig", "insig only", "sig opposed")


def matrix(mkts, sharp):
    """-> {(need, opp): [plays]}; need 1..4 (4 = 4+)."""
    out = defaultdict(list)
    for m in mkts:
        sig = {s: [r for r in rs if sharp(r)] for s, rs in m.sides.items()}
        for s, on in sig.items():
            nw = len(wallets(on))
            if not nw:
                continue
            others = [o for o in m.sides if o != s]
            sig_opp = any(sig[o] for o in others)
            insig = len(set().union(*[wallets(m.sides[o]) for o in others])) if others else 0
            p = play(on)
            if not p:
                continue
            need = min(nw, 4)
            for n in range(1, need + 1):            # cumulative: 2 means 2 or more
                if sig_opp:
                    out[(n, "sig opposed")].append(p)
                    continue
                out[(n, "insig only")].append(p)
                if insig <= 1:
                    out[(n, "<=1 insig")].append(p)
                if insig == 0:
                    out[(n, "none")].append(p)
    return out


# --- PART 2: the named cohorts ----------------------------------------------
def cohorts(mkts, sharp):
    """-> {code: [plays]}. Every cohort is built on the same `sharp` bar so the
    sweep moves them together."""
    C = defaultdict(list)
    for m in mkts:
        allside = {s: rs for s, rs in m.sides.items()}
        sig = {s: [r for r in rs if sharp(r)] for s, rs in allside.items()}
        for s, on in sig.items():
            others = [o for o in allside if o != s]
            opp_sig = [r for o in others for r in sig[o]]
            opp_all = [r for o in others for r in allside[o]]
            nw = len(wallets(on))

            # C14 runs from the bad wallet's side, before the `on` check
            bad = [r for r in allside[s] if (r.get("_clv_n") or 0) >= MIN_CLV_HIST
                   and (r.get("_clv") or 0) < -3.0 and rel(r) > 2.0]
            if bad and len(others) == 1 and not sig[s] and "draw" not in str(s).lower():
                o = others[0]
                lead = sorted(bad, key=lambda x: x.get("t_ts") or 0)[0]
                if lead.get("won") is not None and 0 < lead["now"] < 1:
                    won = not bool(lead["won"])
                    C["C14"].append({"slug": m.slug, "won": won, "t_ts": lead.get("t_ts") or 0,
                                     "follow": grade(1 - lead["now"], won), "entry": grade(1 - lead["entry"], won),
                                     "clv": (lead["now"] - lead["close"]) * 100 if lead.get("close") is not None else None,
                                     "px": 1 - lead["now"], "league": lead.get("league"),
                                     "day": day(lead.get("kickoff_ts")), "n": 1})
            # C04-C05: the anchors count at any size above their own median, bar or not
            anc = [r for r in allside[s] if r.get("user") in ANCHORS and rel(r) > 1.0]
            if anc and not opp_sig:
                a = play(anc)
                if a:
                    C["C04"].append(a)
                    if wallets(on) - wallets(anc):
                        C["C05"].append(a)
            if not nw:
                continue
            p = play(on)
            if not p:
                continue
            clean = not opp_sig
            usd_on = sum(r.get("stake") or 0 for r in on)
            usd_opp = sum(r.get("stake") or 0 for r in opp_all)
            lead = sorted(on, key=lambda x: x.get("t_ts") or 0)[0]
            ts = sorted(r.get("t_ts") or 0 for r in on)

            # C01-C03: the asks as stated
            if nw >= 2 and clean and len(wallets(opp_all)) <= 1:
                C["C01"].append(p)
            if nw >= 3 and clean and len(wallets(opp_all)) <= 1:
                C["C02"].append(p)
            if nw >= 2 and clean:
                C["C03"].append(p)
            # C06: money, not heads: sharp $ on the side >= 3x every $ against
            if clean and usd_on >= 3 * max(usd_opp, 1) and usd_on >= 25_000:
                C["C06"].append(p)
            # C07: CLV roster: 2+ sharps whose own prior CLV is positive
            good = [r for r in on if (r.get("_clv_n") or 0) >= MIN_CLV_HIST and (r.get("_clv") or -1) > 0]
            if len(wallets(good)) >= 2 and clean:
                C["C07"].append(p)
            # C08: steam confirmed: the market moved toward the side after the lead bet
            if nw >= 2 and clean and lead["now"] - lead["entry"] >= 0.02:
                C["C08"].append(p)
            # C09: value: follower gets the sharp's price or better
            if nw >= 2 and clean and lead["now"] <= lead["entry"]:
                C["C09"].append(p)
            # C10: independent arrivals (first and last sharp >= 6h apart) vs C11 herd (<= 1h)
            if nw >= 2 and clean and ts[-1] - ts[0] >= 6 * 3600:
                C["C10"].append(p)
            if nw >= 2 and clean and ts[-1] - ts[0] <= 3600:
                C["C11"].append(p)
            # C12: late money: 2+ sharps, at least one entering in the last 3h
            if nw >= 2 and clean and any(0 < (r.get("kickoff_ts") or 0) - (r.get("t_ts") or 0) <= 3 * 3600 for r in on):
                C["C12"].append(p)
            # C13: league specialists: 2+ sharps each >= 30 prior league wagers at > 0 ROI
            spec = [r for r in on if (r.get("_lg_n") or 0) >= 30 and (r.get("_lg_roi") or -1) > 0]
            if len(wallets(spec)) >= 2 and clean:
                C["C13"].append(p)
            # C15: conviction score: size and profile summed, opposition subtracted
            def w(r):
                return min(math.log2(1 + rel(r)), 3.0) * (1 + max(r.get("hold") or 0, 0) / 5)
            score = sum(w(r) for r in on) - sum(w(r) for r in opp_all)
            if score >= 6:
                C["C15"].append(dict(p, score=score))
    return C


NAMES = {
    "C01": "2+ sharps vs <=1 insignificant, no sig opp",
    "C02": "3+ sharps vs <=1 insignificant, no sig opp",
    "C03": "2+ sharps, no sig opp (insig allowed)",
    "C04": "Kch-Temp or primm > 1x median, no sig opp",
    "C05": "Kch-Temp/primm + 1 other sharp, no sig opp",
    "C06": "sharp $ >= 3x all $ against, >= $25K",
    "C07": "2+ sharps with positive prior CLV",
    "C08": "2+ sharps, steam: price moved >= 2pt their way",
    "C09": "2+ sharps, follower price <= sharp entry",
    "C10": "2+ sharps, independent (>= 6h apart)",
    "C11": "2+ sharps, herd (all within 1h)",
    "C12": "2+ sharps, one entered in last 3h",
    "C13": "2+ league specialists (30+, ROI > 0)",
    "C14": "FADE a bad-CLV wallet's 2x bet",
    "C15": "conviction score >= 6 (size x hold, net)",
}


# --- stats -------------------------------------------------------------------
def boot(plays, key="follow"):
    by = defaultdict(list)
    for p in plays:
        by[p["slug"]].append(p[key])
    keys = list(by)
    if len(keys) < 5:
        return (None, None)
    rng = random.Random(7); outs = []
    for _ in range(BOOT_N):
        s = n = 0.0
        for _ in keys:
            v = by[rng.choice(keys)]; s += sum(v); n += len(v)
        outs.append(100 * s / n)
    outs.sort()
    return (outs[int(BOOT_N * .05)], outs[int(BOOT_N * .95)])


def lower(plays, key="follow", z=1.645):
    """Game-clustered normal 90% lower bound; the fast screen for Part 4."""
    by = defaultdict(lambda: [0.0, 0])
    for p in plays:
        a = by[p["slug"]]; a[0] += p[key]; a[1] += 1
    n = len(plays)
    if len(by) < 5:
        return None
    m = sum(a[0] for a in by.values()) / n
    se = math.sqrt(sum((a[0] - a[1] * m) ** 2 for a in by.values())) / n
    return 100 * (m - z * se)


def stats(plays, ci=True):
    if not plays:
        return None
    n = len(plays)
    cl = [p["clv"] for p in plays if p["clv"] is not None]
    lo, hi = boot(plays) if ci else (None, None)
    return {"n": n, "g": len({p["slug"] for p in plays}),
            "w": sum(p["won"] for p in plays),
            "follow": 100 * sum(p["follow"] for p in plays) / n,
            "entry": 100 * sum(p["entry"] for p in plays) / n,
            "lo": lo, "hi": hi,
            "clv": statistics.mean(cl) if cl else None,
            "beat": (sum(c > 0 for c in cl) / len(cl) * 100) if cl else None,
            "px": statistics.median(p["px"] for p in plays)}


def fmt(v, f="%+.1f"):
    return "—" if v is None else f % v


def row(label, s):
    if not s:
        return f"| {label} | 0 | | | | | | | |"
    ci = f"[{fmt(s['lo'], '%+.0f')}, {fmt(s['hi'], '%+.0f')}]" if s["lo"] is not None else "—"
    return (f"| {label} | {s['n']} | {s['w']}-{s['n'] - s['w']} | **{s['follow']:+.1f}%** | {ci} "
            f"| {s['entry']:+.1f}% | {fmt(s['clv'], '%+.2f')} | {fmt(s['beat'], '%.0f%%')} | {s['px']:.2f} |")


HEAD = ("| cut | plays | W-L | follow ROI | 90% CI | entry ROI | CLV pts | beat close | med price |\n"
        "|---|---:|---:|---:|---|---:|---:|---:|---:|")


# --- main --------------------------------------------------------------------
def main():
    rows = add_history(X.add_career(X.load()))
    mk = markets(rows)
    L = []
    P = L.append
    P("# EXP-036 — consensus vs opposition cohorts\n")
    P(f"*{datetime.now():%Y-%m-%d}. `research/cohorts.py`. Corpus: {len(rows):,} scored rows, "
      f"{len(mk):,} pregame markets. Graded at the T-60 price + {VIG * 100:.1f} pts vig (follow) "
      "and at the first sharp's entry (entry, optimistic). One play per side.*\n")

    base = [play([r]) for m in mk for rs in m.sides.values() for r in rs if r.get("_rel") is not None]
    P("## Baseline\n\n" + HEAD)
    P(row("every pregame $2.5K+ position with a career median", stats([b for b in base if b], ci=False)))

    grid = [(h, p, x) for h in HOLDS for p in PNLS for x in MULTS]
    cells = []                         # (part, label, bar, plays) for the multiple-testing check

    # PART 1
    P("\n## Part 1 — sharps on the side x opposition, default bar "
      f"(hold > {DEFAULT[0]}%, P/L > ${DEFAULT[1]:,}, > {DEFAULT[2]}x median)\n\n" + HEAD)
    mats = {}
    for g in grid:
        mats[g] = matrix(mk, bar(*g))
        for k, ps in mats[g].items():
            cells.append(("matrix", f"{k[0]}+ vs {k[1]}", g, ps))
    for n in (1, 2, 3, 4):
        for o in OPP:
            P(row(f"{n}+ sharps · {o}", stats(mats[DEFAULT][(n, o)])))

    P("\n### Part 1 sweep — follow ROI (n) for each bar, opposition = '<=1 insig'\n")
    P("| hold / P/L / mult | 1+ | 2+ | 3+ | 4+ |\n|---|---|---|---|---|")
    for g in grid:
        cs = []
        for n in (1, 2, 3, 4):
            s = stats(mats[g][(n, "<=1 insig")], ci=False)
            cs.append(f"{s['follow']:+.1f}% ({s['n']})" if s else "—")
        P(f"| {g[0]}% / ${g[1] // 1000}k / {g[2]}x | " + " | ".join(cs) + " |")

    # PART 2
    P("\n## Part 2 — fifteen cohorts at the default bar\n\n" + HEAD)
    cos = {g: cohorts(mk, bar(*g)) for g in grid}
    for g, C in cos.items():
        for code, ps in C.items():
            cells.append(("cohort", code, g, ps))
    for code in sorted(NAMES):
        P(row(f"{code} {NAMES[code]}", stats(cos[DEFAULT].get(code, []))))

    P("\n### Part 2 sweep — the same cohort across all "
      f"{len(grid)} bars (cells with >= 30 plays)\n")
    P("| cohort | cells | median follow ROI | cells > 0 | best | worst | median CLV |\n|---|---:|---:|---:|---:|---:|---:|")
    for code in sorted(NAMES):
        ss = [stats(cos[g].get(code, []), ci=False) for g in grid]
        ss = [s for s in ss if s and s["n"] >= 30]
        if not ss:
            P(f"| {code} | 0 | | | | | |"); continue
        r = [s["follow"] for s in ss]
        c = [s["clv"] for s in ss if s["clv"] is not None]
        P(f"| {code} | {len(ss)} | {statistics.median(r):+.1f}% | {sum(x > 0 for x in r)} "
          f"| {max(r):+.1f}% | {min(r):+.1f}% | {fmt(statistics.median(c) if c else None, '%+.2f')} |")

    # PART 3
    P("\n## Part 3 — by kickoff day (US Eastern), Sun / Sat / Mon / Thu / rest, default bar\n")
    show = [("baseline", [b for b in base if b])] + \
           [(f"{n}+ vs <=1 insig", mats[DEFAULT][(n, "<=1 insig")]) for n in (1, 2, 3)] + \
           [(f"{c} {NAMES[c]}", cos[DEFAULT].get(c, [])) for c in sorted(NAMES)]
    P("| cut | " + " | ".join(DAYS) + " |\n|---|" + "---|" * len(DAYS))
    for label, ps in show:
        cs = []
        for d in DAYS:
            s = stats([p for p in ps if p["day"] == d], ci=False)
            cs.append(f"{s['follow']:+.1f}% ({s['n']}) · {fmt(s['clv'], '%+.2f')}" if s else "—")
        P(f"| {label} | " + " | ".join(cs) + " |")
    P("\n*Each cell: follow ROI (plays) · CLV pts.*")

    # PART 4
    allt = sorted(p["t_ts"] for b in base if b for p in [b])
    cut = allt[int(len(allt) * (1 - OOS_FRAC))] if allt else 0
    big = [c for c in cells if len(c[3]) >= 100]
    pos = []
    for part, label, g, ps in big:
        lo = lower(ps)
        if lo is not None and lo > 0:
            pos.append((part, label, g, ps))
    P(f"\n## Part 4 — is any of this more than luck?\n")
    sets = len({tuple(sorted((p["slug"], p["t_ts"]) for p in c[3])) for c in big})
    P(f"{len(cells):,} cells were measured; {len(big):,} have >= 100 plays "
      f"({sets:,} distinct play sets: many bars select the same plays). "
      f"**{len(pos)}** of those have a follow-ROI 90% interval above zero, against "
      f"**{len(big) * .05:.0f} expected by chance** (one-sided 5% each, and the cells overlap, "
      "so the real chance count is noisier than that).\n")
    if pos:
        P("| cell | bar | plays | W-L | follow ROI | 90% CI | entry ROI | CLV pts | beat close | med price |\n"
          "|---|---|---:|---:|---:|---|---:|---:|---:|---:|")
        for part, label, g, ps in sorted(pos, key=lambda c: -stats(c[3], ci=False)["follow"])[:20]:
            P(f"| {label} | {g[0]}%/${g[1] // 1000}k/{g[2]}x " + row("", stats(ps))[3:])

    P(f"\n### Out of sample: the later {OOS_FRAC:.0%} by time (t_ts >= {cut})\n")
    P("Discovery = the earlier slice. The ten best discovery cells with >= 60 plays, "
      "and how they did on the slice nothing was chosen on:\n")
    P("| cell | bar | discovery follow ROI (n) | **held-out follow ROI (n)** | held-out CLV |\n|---|---|---:|---:|---:|")
    disc = []
    for part, label, g, ps in cells:
        early = [p for p in ps if p["t_ts"] < cut]
        if len(early) >= 60:
            disc.append((stats(early, ci=False)["follow"], label, g, ps, early))
    for f, label, g, ps, early in sorted(disc, key=lambda d: -d[0])[:10]:
        late = stats([p for p in ps if p["t_ts"] >= cut], ci=False)
        P(f"| {label} | {g[0]}%/${g[1] // 1000}k/{g[2]}x | {f:+.1f}% ({len(early)}) "
          f"| **{fmt(late and late['follow'])}%** ({late['n'] if late else 0}) | {fmt(late and late['clv'], '%+.2f')} |")
    P("\n### Every cohort at the default bar, held out\n\n" + HEAD)
    for code in sorted(NAMES):
        P(row(f"{code} {NAMES[code]}", stats([p for p in cos[DEFAULT].get(code, []) if p["t_ts"] >= cut])))

    P("\n**Pre-registered bar (same as EXP-035):** a cohort is a candidate only if its held-out "
      "slice has CLV >= 0 and follow ROI at or above the EXP-034 benchmark on >= 100 plays, "
      "AND its Part 2 sweep median is above zero. The best single cell is expected to look "
      "good by chance.")

    text = "\n".join(L) + "\n"
    print(text)
    if OUT:
        with open(OUT, "w") as f:
            f.write(text)
        print(f"wrote {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
