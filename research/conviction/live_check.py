#!/usr/bin/env python3
"""EXP-039 step 4: conviction v6 on the LIVE signal ledger (independent of the
research corpus: different months, real posting times).

    python3 research/conviction/live_check.py [--live ~/.nightly-line] [--out FILE]

Reads ~/.nightly-line/ledgers/*.json (settled signals, frozen nightly) and
wallethist.json. Read-only.

A live play is one (slug, market, side) with a gated sharp signal logged
before kickoff. Signals are walked in logging order; the play is "posted" at
the first log where the v6 bet gates pass (bets) or at its first log (score
bands). At that moment:
  n_sized  distinct users on the side with hold > 3% and stake > 2x their
           wallethist median (trusted medians only)
  n_opp    distinct users already logged on another side of the market, or the
           row's own `opposed` count, whichever is larger
  move     the row's `move` (linemove_v5): its drift term is ~1.04 x (price at
           posting - entry) in points, the other terms add < 0.2 pts
Grades: entry = the sharp's entry + 1.5 vig (optimistic); follow = entry +
move/104 + 1.5 vig, only where `move` was logged (from 2026-10-08).

Caveats, recorded: wallethist medians are as of today (a little look-ahead);
anonymised users (0x...) cannot be matched to a median and count as unsized;
the lead's big-bet record is not known live, so that +/-5 is never applied.
"""
import argparse, glob, json, os, random, sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score as S

VIG = 0.015


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def grade(p, won):
    q = min(0.99, p + VIG)
    return (1 - q) / q if won else -1.0


def load(live):
    rows = []
    for f in sorted(glob.glob(os.path.join(live, "ledgers", "*.json"))):
        rows += json.load(open(f)).get("signals") or []
    seen, out = set(), []
    for r in rows:
        k = (r["slug"], r["market"], r["side"], r["user"], r["logged"])
        if k in seen:
            continue
        seen.add(k)
        if r.get("won") is None or not (0 < (r.get("entry") or 0) < 1):
            continue
        if not r.get("start") or ts(r["logged"]) >= ts(r["start"]):
            continue
        out.append(r)
    return out


def medians(live):
    sys.path.insert(0, live)
    try:
        import wallethist as W
        st = W.load(os.path.join(live, "wallethist.json"))
        return lambda user, stake: W.rel(user, stake, st)
    except Exception as e:                    # the repo copy works too
        print("wallethist unavailable:", e, file=sys.stderr)
        return lambda user, stake: (None, None, 0, False)


def plays(rows, rel):
    by_mkt = defaultdict(list)
    for r in rows:
        by_mkt[(r["slug"], r["market"])].append(r)
    out = []
    for (slug, mkt), rs in by_mkt.items():
        rs.sort(key=lambda r: r["logged"])
        sides = {r["side"] for r in rs}
        for side in sides:
            on, opp_users, first, posted = {}, set(), None, None
            for r in rs:
                if r["side"] != side:
                    opp_users.add(r["user"]); continue
                vs, med, n, ok = rel(r["user"], r.get("stake"))
                sized = bool(ok and vs and vs > 2 and (r.get("hold") or 0) > 3)
                on[r["user"]] = on.get(r["user"], False) or sized
                st = {"n_sized": sum(on.values()), "n_sharps": max(len(on), int(r.get("consensus") or 0)),
                      "n_opp": max(len(opp_users), int(r.get("opposed") or 0)),
                      "move": r.get("move"), "big_roi": None}
                conv, bet, why = S.score(st)
                snap = dict(st, conv=conv, bet=bet, row=r)
                first = first or snap
                if bet and posted is None:
                    posted = snap
            final = snap if on else None
            for kind, snap in (("first", first), ("final", final), ("bet", posted)):
                if snap is None:
                    continue
                r = snap["row"]; won = bool(r["won"])
                mv = snap["move"]
                out.append({"kind": kind, "slug": slug, "side": side, "n_sharps": snap["n_sharps"], "league": r.get("league"), "won": won,
                            "conv": snap["conv"], "bet": snap["bet"], "n_sized": snap["n_sized"],
                            "n_opp": snap["n_opp"], "move": mv, "conv_v4": r.get("conv"),
                            "entry_pl": grade(r["entry"], won),
                            "follow_pl": grade(r["entry"] + mv / 104.0, won) if mv is not None else None,
                            "clv": (r["close"] - r["entry"]) * 100 if r.get("close") is not None else None,
                            "day": r["logged"][:10]})
    return out


def ci(rs, key, B=2000):
    g = defaultdict(list)
    for r in rs:
        g[r["slug"]].append(r[key])
    ks = list(g)
    if len(ks) < 5:
        return float("nan"), float("nan")
    rnd = random.Random(7); res = []
    for _ in range(B):
        s = [x for k in rnd.choices(ks, k=len(ks)) for x in g[k]]
        res.append(sum(s) / len(s) * 100)
    res.sort()
    return res[int(.05 * B)], res[int(.95 * B)]


def line(name, rs):
    if not rs:
        return f"| {name} | 0 | | | | | |"
    w = sum(r["won"] for r in rs)
    e = sum(r["entry_pl"] for r in rs) / len(rs) * 100
    lo, hi = ci(rs, "entry_pl")
    fr = [r for r in rs if r["follow_pl"] is not None]
    f = (f"{sum(r['follow_pl'] for r in fr) / len(fr) * 100:+.1f}% ({len(fr)})") if fr else "—"
    cl = [r["clv"] for r in rs if r["clv"] is not None]
    return (f"| {name} | {len(rs)} / {len({r['slug'] for r in rs})} | {w}-{len(rs) - w} | "
            f"**{e:+.1f}%** [{lo:+.1f}, {hi:+.1f}] | {f} | {sum(cl) / len(cl) if cl else float('nan'):+.2f} |")


HDR = ("| cut | plays / games | W-L | entry ROI [90% CI] | follow ROI (n with move) | signal CLV pts |\n"
       "|---|---:|---|---|---|---:|")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default="~/.nightly-line")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    live = os.path.expanduser(a.live)
    rows = load(live)
    P = plays(rows, medians(live))
    first = [p for p in P if p["kind"] == "first"]
    final = [p for p in P if p["kind"] == "final"]
    bets = [p for p in P if p["kind"] == "bet"]
    days = sorted({p["day"] for p in first})
    L = [f"Live ledger {days[0]} to {days[-1]}: {len(rows):,} settled pre-kickoff signal logs, "
         f"{len(first):,} plays on {len({p['slug'] for p in first}):,} games.\n", HDR,
         line("every play", first)]
    for a_, b_ in ((0, 40), (40, 55), (55, 70), (70, 85), (85, 101)):
        L.append(line(f"v6 {a_}-{min(b_, 100)} (last log before kickoff)", [p for p in final if a_ <= p["conv"] < b_]))
    L.append(line("**BET (v6 gates pass, at the log they first pass)**", bets))
    bet_keys = {(b["slug"], b["side"]) for b in bets}
    L.append(line("no bet", [p for p in final if (p["slug"], p["side"]) not in bet_keys]))
    L.append(line("contested (gated sharp opposite)", [p for p in first if p["n_opp"] > 0]))
    L.append(line("uncontested", [p for p in first if p["n_opp"] == 0]))
    L.append(line("line >= 2 pts away at first log", [p for p in first if p["move"] is not None and p["move"] <= -2]))
    L.append(line("line > -2 at first log", [p for p in first if p["move"] is not None and p["move"] > -2]))
    L.append(line(">= 2 sized sharps (any opposition / move)", [p for p in final if p["n_sized"] >= 2]))
    L.append(line(">= 2 sharps on the side, uncontested", [p for p in final if p["n_sharps"] >= 2 and p["n_opp"] == 0]))
    L.append(line(">= 2 sharps on the side, contested", [p for p in final if p["n_sharps"] >= 2 and p["n_opp"] > 0]))
    L.append(line("v4 conv >= 70", [p for p in first if (p["conv_v4"] or 0) >= 70]))
    rep = "\n".join(L)
    print(rep)
    if a.out:
        open(a.out, "w").write(rep + "\n")


if __name__ == "__main__":
    main()
