#!/usr/bin/env python3
"""
EXP-030 live check: grade every signal that carries a line-move tier
(signals.jsonl, `move_tier` in toward/flat/away, logged from 2026-10-08) against
gamma's settled markets and report flat ROI at the sharp's entry + VIG per tier,
with a game-clustered 90% CI. Read-only: uses production/resolve.outcome_for with
a local cache and never writes to ~/.nightly-line.
    python3 research/linemove_live.py            # print
    python3 research/linemove_live.py --write    # also results/live/linemove-<date>.md
"""
import json, os, random, sys
from collections import defaultdict
from datetime import date
LIVE = os.path.expanduser("~/.nightly-line")
sys.path.insert(0, LIVE)
import resolve                      # outcome_for(slug, market, side, cache)
VIG = 0.015
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def pl(entry, won):
    p = min(0.97, entry + VIG)
    return (1 - p) / p if won else -1.0

def roi(rs): return 100 * sum(r["pl"] for r in rs) / len(rs) if rs else float("nan")

def ci(rs, B=2000):
    g = defaultdict(list)
    for r in rs: g[r["slug"]].append(r)
    ks = list(g); out = []
    random.seed(7)
    for _ in range(B):
        s = [x for k in random.choices(ks, k=len(ks)) for x in g[k]]
        out.append(roi(s))
    out.sort(); return out[int(.05 * B)], out[int(.95 * B)]

def main():
    rows = [json.loads(l) for l in open(os.path.join(LIVE, "signals.jsonl"))]
    rows = [r for r in rows if r.get("move_tier") in ("toward", "flat", "away")
            and 0 < (r.get("entry") or 0) < 1]
    # one row per (slug, market, side, user): the ledger re-logs on stake growth
    seen = {}
    for r in rows:
        seen[(r["slug"], r["market"], r["side"], r["user"])] = r
    rows = list(seen.values())
    cache = {}
    graded = []
    for r in rows:
        won = resolve.outcome_for(r["slug"], r["market"], r["side"], cache)
        if won is None: continue
        r["won"] = won; r["pl"] = pl(r["entry"], won); graded.append(r)
    out = [f"# EXP-030 live — {date.today().isoformat()}", "",
           f"{len(rows)} distinct signals with a move tier, {len(graded)} settled "
           f"({len({r['slug'] for r in graded})} games). Decision bar: 300 settled signals.", "",
           "| tier | n | games | hit | flat ROI @entry+vig | 90% CI |", "|---|---:|---:|---:|---:|---|"]
    for tier in ("toward", "flat", "away", "ALL"):
        rs = graded if tier == "ALL" else [r for r in graded if r["move_tier"] == tier]
        if not rs: out.append(f"| {tier} | 0 | | | | |"); continue
        w = sum(1 for r in rs if r["won"]); lo, hi = ci(rs)
        out.append(f"| {tier} | {len(rs)} | {len({r['slug'] for r in rs})} | {w/len(rs):.0%} | {roi(rs):+.1f}% | [{lo:+.0f}, {hi:+.0f}] |")
    # qualifying-only view (what could become a pick): entry inside the window
    out += ["", "In the pick window (entry 0.55–0.643, i.e. −122..−180):", "",
            "| tier | n | games | hit | flat ROI | 90% CI |", "|---|---:|---:|---:|---:|---|"]
    for tier in ("toward", "flat", "away"):
        rs = [r for r in graded if r["move_tier"] == tier and 0.55 <= r["entry"] <= 0.643]
        if len(rs) < 5: out.append(f"| {tier} | {len(rs)} | | | | |"); continue
        w = sum(1 for r in rs if r["won"]); lo, hi = ci(rs)
        out.append(f"| {tier} | {len(rs)} | {len({r['slug'] for r in rs})} | {w/len(rs):.0%} | {roi(rs):+.1f}% | [{lo:+.0f}, {hi:+.0f}] |")
    txt = "\n".join(out); print(txt)
    if "--write" in sys.argv:
        p = os.path.join(REPO, "results", "live", f"linemove-{date.today().isoformat()}.md")
        open(p, "w").write(txt + "\n"); print("wrote", p)

if __name__ == "__main__":
    main()
