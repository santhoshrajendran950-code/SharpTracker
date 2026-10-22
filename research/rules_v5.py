#!/usr/bin/env python3
"""Candidate rules for the frozen v5 pick rule, measured on the research corpus.

    python3 research/rules_v5.py            # on the host (~/nl-research windows)

Santhosh, 2026-10-22:
  R1  CONSENSUS, NO OPPOSITION. >=2 bettors with hold > 2.5% and lifetime P/L
      > $100k each have more than 1x their own career median on a side, and no
      bettor clearing the SAME three bars is on the other side of that market.
  R2  TRAIL THE BEST. Every pregame side taken by a bettor with hold > 5% and
      lifetime P/L > $250k.
Proposed for him to pick from (not his rules yet):
  R3  SPECIALIST. A bettor whose own record IN THIS LEAGUE is >= 30 earlier
      graded wagers at > +5% flat ROI (expanding, no look-ahead), lifetime
      P/L > $250k, betting > 1x their career median.
  R4  LATE SIZE-UP. A v4-gate sharp puts > 2x their career median on a side in
      the last 3 hours before kickoff, with no v4 sharp on the other side.

Every rule is printed beside the live EXP-034 gate as the benchmark. One play
per (slug, market, side), graded at the FIRST qualifying bettor's entry plus
1.5 pts of vig (xrule.flat) -- the optimistic fill; signal CLV is the honest
column. "hold" and "pnl" on a research row are PRIOR values (bt_score.py: the
wallet's record before this wager), so nothing looks ahead.

Six rules and their variants are tested at once, so one of them clearing zero
by luck is expected. Read the OUT OF SAMPLE block: each rule is scored on the
later 35% of the corpus by time, and nothing was fitted on that slice.
"""
import os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xrule as X

LATE_SECS = 3 * 3600
SPEC_MIN_N = 30
SPEC_MIN_ROI = 0.05


def pregame(r):
    return (not r.get("decided") and not r.get("two_sided")
            and (r.get("t_ts") or 0) < (r.get("kickoff_ts") or 0))


def side_key(r):
    return (r["slug"], r.get("market"), r.get("side"))


def play(rs):
    rs = sorted(rs, key=lambda x: x.get("t_ts") or 0)
    lead = rs[0]
    f = X.flat(lead)
    if f is None:
        return None
    return {"slug": lead["slug"], "flat": f, "won": bool(lead.get("won")),
            "clv_entry": lead.get("clv_entry"), "t_ts": lead.get("t_ts") or 0,
            "league": lead.get("league"), "n": len(rs)}


def fire(rows, sharp, need=1, veto=None):
    """Sides with >= `need` distinct wallets passing `sharp`, minus any market
    where a wallet passing `veto` sits on another side. veto=None: no veto."""
    on, opp, sides = defaultdict(list), defaultdict(set), defaultdict(set)
    for r in rows:
        if not pregame(r):
            continue
        k = side_key(r)
        sides[k[:2]].add(k[2])
        if sharp(r):
            on[k].append(r)
        if veto and veto(r):
            opp[k].add(r["wallet"])
    out = []
    for k, rs in on.items():
        if len({r["wallet"] for r in rs}) < need:
            continue
        if veto and any(opp.get(k[:2] + (s,)) for s in sides[k[:2]] if s != k[2]):
            continue
        p = play(rs)
        if p:
            out.append(p)
    return out


def add_league_record(rows):
    """_lg_n / _lg_roi: the wallet's graded wagers in this league strictly
    BEFORE this one, and their flat ROI."""
    by = defaultdict(list)
    for r in rows:
        by[(r["wallet"], r.get("league"))].append(r)
    for rs in by.values():
        rs.sort(key=lambda x: x.get("t_ts") or 0)
        n, tot = 0, 0.0
        for r in rs:
            r["_lg_n"], r["_lg_roi"] = n, (tot / n if n else None)
            f = X.flat(r)
            if f is not None and r.get("won") is not None:
                n, tot = n + 1, tot + f
    return rows


def rel(r):
    return r.get("_rel") or 0


# --- the sharps each rule counts -------------------------------------------
def r1(r):
    return (r.get("hold") or -99) > 2.5 and (r.get("pnl") or 0) > 100_000 and rel(r) > 1.0


def r1_v4(r):
    return bool(r.get("qualifies")) and r1(r)


def r2(r):
    return (r.get("hold") or -99) > 5.0 and (r.get("pnl") or 0) > 250_000


def r2_sized(r):
    return r2(r) and rel(r) > 1.0


def r3(r):
    return ((r.get("_lg_n") or 0) >= SPEC_MIN_N and (r.get("_lg_roi") or -1) > SPEC_MIN_ROI
            and (r.get("pnl") or 0) > 250_000 and rel(r) > 1.0)


def r4(r):
    return (bool(r.get("qualifies")) and rel(r) > 2.0
            and 0 < (r.get("kickoff_ts") or 0) - (r.get("t_ts") or 0) <= LATE_SECS)


def v4(r):
    return bool(r.get("qualifies"))


def exp034(r):
    return bool(r.get("qualifies")) and (r.get("hold") or -99) > X.HOLD_MIN and rel(r) > X.MULT


RULES = [
    ("EXP-034 live gate (benchmark)",            dict(sharp=exp034, need=2, veto=v4)),
    ("R1 as stated: 2+, symmetric veto",         dict(sharp=r1, need=2, veto=r1)),
    ("R1, veto = any v4 sharp (EXP-027)",        dict(sharp=r1, need=2, veto=v4)),
    ("R1, sharps must also pass the v4 gate",    dict(sharp=r1_v4, need=2, veto=r1_v4)),
    ("R1 with 3+ bettors",                       dict(sharp=r1, need=3, veto=r1)),
    ("R2 trail hold>5% P/L>$250k",               dict(sharp=r2, need=1)),
    ("R2, sized >1x median",                     dict(sharp=r2_sized, need=1)),
    ("R2, sized, no v4 sharp opposing",          dict(sharp=r2_sized, need=1, veto=v4)),
    ("R3 league specialist (proposed)",          dict(sharp=r3, need=1, veto=v4)),
    ("R4 late size-up (proposed)",               dict(sharp=r4, need=1, veto=v4)),
]


if __name__ == "__main__":
    rows = add_league_record(X.add_career(X.load()))
    print(f"corpus: {len(rows):,} rows\n")
    fired = {name: fire(rows, **kw) for name, kw in RULES}
    for name, ps in fired.items():
        X.report(name, ps)

    allt = sorted(p["t_ts"] for ps in fired.values() for p in ps)
    cut = allt[int(len(allt) * .65)] if allt else 0
    print(f"\nOUT OF SAMPLE: later 35% by time (from t_ts {cut})")
    for name, ps in fired.items():
        X.report("  " + name, [p for p in ps if p["t_ts"] >= cut])

    print("\nBY LEAGUE (rules with >= 40 plays)")
    for name, ps in fired.items():
        if len(ps) < 40:
            continue
        by = defaultdict(list)
        for p in ps:
            by[p.get("league")].append(p)
        print(f"  {name}")
        for lg, lp in sorted(by.items(), key=lambda kv: -len(kv[1])):
            if len(lp) >= 15:
                X.report(f"    {lg}", lp)
