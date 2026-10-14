#!/usr/bin/env python3
"""
EXP-029 pre-registered test (LEDGER 2026-10-07): does a trader's rolling signal
CLV license their in-window positions?

Data: every settled research row (~/nl-research/windows/**/bt_scored.jsonl),
chronological by t_ts. Per trader, rolling signal CLV = mean of (close − entry)
over their last 100 settled signals, licensed once n ≥ 60. Tiers = quartiles of
all licensed (trader, time) values. In-window = T-60 price (`now`) inside the
pick window 0.55–0.643 and the row qualifies. Top-tier rows are graded only when
a follower could have got within 1 pt of the sharp's entry (now ≤ entry + 0.01),
at `now`; the pool is every qualifying in-window row at `now`.
Pass: top-tier flat ROI interval excludes the pool mean in ≥ 3 sports with
n ≥ 40 games each, and bottom-tier ≤ pool. Read-only; stdlib.
    python3 research/clv_license.py [--write]
"""
import glob, json, os, random, sys
from collections import defaultdict, deque
from datetime import date
RES = os.path.expanduser("~/nl-research/windows")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIG, LO, HI, WIN, MIN_N = 0.015, 0.55, 0.643, 100, 60

def pl(px, won):
    p = min(0.97, px + VIG); return (1 - p) / p if won else -1.0
def roi(rs): return 100 * sum(r["pl"] for r in rs) / len(rs) if rs else float("nan")
def ci(rs, B=1500):
    g = defaultdict(list)
    for r in rs: g[r["slug"]].append(r)
    ks = list(g); out = []; random.seed(7)
    for _ in range(B):
        s = [x for k in random.choices(ks, k=len(ks)) for x in g[k]]; out.append(roi(s))
    out.sort(); return out[int(.05 * B)], out[int(.95 * B)]
def games(rs): return len({r["slug"] for r in rs})

def main():
    rows = []
    for f in glob.glob(os.path.join(RES, "**", "bt_scored.jsonl"), recursive=True):
        for l in open(f):
            r = json.loads(l)
            if r.get("won") is None or not r.get("user"): continue
            e, c, n = r.get("entry"), r.get("close"), r.get("now")
            if not e or not c or not n or not (0 < e < 1) or not (0 < n < 1): continue
            rows.append(dict(user=r["user"], t=r.get("t_ts") or r.get("kickoff_ts") or 0, entry=e, now=n,
                             sclv=(c - e) * 100, won=bool(r["won"]), league=r.get("league") or "?",
                             slug=r["slug"], q=bool(r.get("qualifies"))))
    rows.sort(key=lambda r: r["t"])
    hist = defaultdict(lambda: deque(maxlen=WIN))
    # rolling license: computed from signals settled BEFORE this row's time.
    # bt rows settle at kickoff+; approximate "before" by t order within trader.
    for r in rows:
        h = hist[r["user"]]
        r["lic"] = (sum(h) / len(h)) if len(h) >= MIN_N else None
        h.append(r["sclv"])
    lic_rows = [r for r in rows if r["lic"] is not None]
    vals = sorted(r["lic"] for r in lic_rows)
    q1, q3 = vals[len(vals) // 4], vals[3 * len(vals) // 4]
    for r in lic_rows:
        r["tier"] = "top" if r["lic"] >= q3 else "bottom" if r["lic"] <= q1 else "mid"
    pool = [r for r in rows if r["q"] and LO <= r["now"] <= HI]
    for r in pool: r["pl"] = pl(r["now"], r["won"])
    top = [r for r in pool if r.get("tier") == "top" and r["now"] <= r["entry"] + 0.01]
    bot = [r for r in pool if r.get("tier") == "bottom"]
    unl = [r for r in pool if r.get("lic") is None]
    out = [f"# EXP-029 — CLV license test on research windows — {date.today().isoformat()}", "",
           f"{len(rows):,} settled rows, {len(lic_rows):,} with a license (n ≥ {MIN_N} prior signals); "
           f"quartile cutoffs: bottom ≤ {q1:+.2f}, top ≥ {q3:+.2f} pts. Pool = qualifying rows with T-60 price in "
           f"{LO}–{HI}: {len(pool):,} rows / {games(pool):,} games.", "",
           "| sport | pool n/games | pool ROI [90%] | top n/games | top ROI [90%] | top sCLV | bottom n/games | bottom ROI [90%] | verdict |",
           "|---|---:|---|---:|---|---:|---:|---|---|"]
    passes = 0
    sports = sorted({r["league"] for r in pool}, key=lambda s: -len([r for r in pool if r["league"] == s]))
    for sp in ["ALL"] + sports:
        P = pool if sp == "ALL" else [r for r in pool if r["league"] == sp]
        T = [r for r in top if sp == "ALL" or r["league"] == sp]
        Bm = [r for r in bot if sp == "ALL" or r["league"] == sp]
        if games(P) < 40: continue
        plo, phi = ci(P); pm = roi(P)
        if games(T) >= 40:
            tlo, thi = ci(T); tcl = sum(r["sclv"] for r in T) / len(T)
            v = "PASS" if (tlo > pm) else "fail"
            if sp != "ALL" and tlo > pm: passes += 1
            tcell = f"{roi(T):+.1f}% [{tlo:+.0f}, {thi:+.0f}]"; tn = f"{len(T)}/{games(T)}"; tc = f"{tcl:+.2f}"
        else:
            v = "n<40"; tcell = "—"; tn = f"{len(T)}/{games(T)}"; tc = "—"
        if games(Bm) >= 40:
            blo, bhi = ci(Bm); bcell = f"{roi(Bm):+.1f}% [{blo:+.0f}, {bhi:+.0f}]"
            if sp != "ALL" and roi(Bm) > pm: v += ", bottom>pool"
        else: bcell = "—"
        out.append(f"| {sp} | {len(P)}/{games(P)} | {pm:+.1f}% [{plo:+.0f}, {phi:+.0f}] | {tn} | {tcell} | {tc} | {len(Bm)}/{games(Bm)} | {bcell} | {v} |")
    out += ["", f"Unlicensed (fewer than {MIN_N} prior signals) in pool: {len(unl)} rows, ROI {roi(unl):+.1f}%.",
            f"Sports where the top tier's interval excludes the pool mean (n ≥ 40 games): **{passes}** (pass needs ≥ 3)."]
    txt = "\n".join(out); print(txt)
    if "--write" in sys.argv:
        p = os.path.join(REPO, "results", f"exp029-clv-license-{date.today().isoformat()}.md")
        open(p, "w").write(txt + "\n"); print("wrote", p)

if __name__ == "__main__":
    main()
