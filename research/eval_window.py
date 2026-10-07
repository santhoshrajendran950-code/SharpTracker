#!/usr/bin/env python3
"""
Evaluate everything the LIVE system suggested in a date window — every signal
that reached a board or an email (ledgers/<day>.json, settled with close and
CLV) and every graded public pick (picks.jsonl) — and ask what the rules got
wrong.

    python3 research/eval_window.py --from 2026-09-01 --to 2026-10-06 [--write]

Grading: 1u flat at the sharp's entry price PLUS 1.5 pts of vig (the research
convention — what a follower could actually get), game-clustered 90% CI.
Picks are graded at the recorded book price (review.py). Read-only.
"""
import glob, json, os, random, re, sys
from collections import defaultdict, Counter
from datetime import date, timedelta

LIVE = os.path.expanduser("~/.nightly-line")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "research"))
import review as R

VIG, BOOT, SEED = 0.015, 2000, 7


def load_signals(d0, d1):
    seen = {}
    for f in sorted(glob.glob(os.path.join(LIVE, "ledgers", "*.json"))):
        for s in json.load(open(f)).get("signals", []):
            k = (s["slug"], s["market"], s["side"], s["user"])
            if k not in seen or s["logged"] < seen[k]["logged"]:
                seen[k] = s                      # the FIRST time it was suggested
    rows = [s for s in seen.values() if d0 <= (s.get("start") or "")[:10] <= d1 and s.get("won") is not None]
    for s in rows:
        p = min(0.99, s["entry"] + VIG)
        s["_flat"] = (1 - p) / p if s["won"] else -1.0
        s["_mt"] = mtype(s["market"])
    return rows


def mtype(m):
    if re.search(r"\b(1H|1Q|2H|1st|Inning|half|quarter|period)\b", m, re.I): return "period"
    if re.search(r"O/U|Over|Under|total", m, re.I): return "total"
    if re.search(r"Spread|[+-]\d+(\.5)?\b", m): return "spread"
    return "moneyline"


def ci(rows):
    g = defaultdict(list)
    for s in rows: g[s["slug"]].append(s["_flat"])
    keys = list(g)
    if len(keys) < 5: return None
    rng = random.Random(SEED); outs = []
    for _ in range(BOOT):
        v = [x for k in rng.choices(keys, k=len(keys)) for x in g[k]]
        outs.append(100 * sum(v) / len(v))
    outs.sort(); return outs[int(.05 * BOOT)], outs[int(.95 * BOOT)]


def line(label, rows, w=26):
    if not rows: return None
    n = len(rows); won = sum(1 for s in rows if s["won"])
    roi = 100 * sum(s["_flat"] for s in rows) / n
    c = ci(rows); cs = f"[{c[0]:+.0f}, {c[1]:+.0f}]" if c else "—"
    clv = [s["clv"] for s in rows if s.get("clv") is not None]
    clvs = f"{100*sum(clv)/len(clv):+.2f}" if clv else "—"
    beat = f"{sum(1 for x in clv if x > 0)}/{len(clv)}" if clv else "—"
    games = len({s["slug"] for s in rows})
    return f"| {label:<{w}} | {n:>4} | {won:>3}-{n-won:<3} | {won/n:4.0%} | {roi:+6.1f}% | {cs:>12} | {clvs:>6} | {beat:>7} | {games:>4} |"


HEAD = "| cut | n | W-L | hit | flat ROI | 90% CI | CLV pts | beat | games |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|"


def band_conv(s):
    c = s.get("conv")
    if c is None: return "no score"
    return "<65" if c < 65 else "65-69" if c < 70 else "70-79" if c < 80 else "80-89" if c < 90 else "90+"


def band_price(s):
    e = s["entry"]
    return ("dog +150 or longer" if e < .40 else "dog +100..+150" if e < .50 else "pickem -100..-122" if e < .55
            else "-122..-150" if e < .60 else "-150..-180" if e < .643 else "-180..-300" if e < .75 else "shorter than -300")


def band_hold(s):
    h = s["hold"]; return "<2%" if h < 2 else "2-3%" if h < 3 else "3-5%" if h < 5 else "5%+"


def band_stake(s):
    k = s["stake"]; return "<$5K" if k < 5e3 else "$5-10K" if k < 1e4 else "$10-25K" if k < 2.5e4 else "$25-100K" if k < 1e5 else "$100K+"


def week(s):
    d = date.fromisoformat(s["start"][:10]); return (d - timedelta(days=d.weekday())).isoformat()


def hours_before(s):
    from datetime import datetime
    try:
        a = datetime.fromisoformat(s["logged"].replace("Z", "+00:00")); b = datetime.fromisoformat(s["start"].replace("Z", "+00:00"))
        h = (b - a).total_seconds() / 3600
        return "<1h" if h < 1 else "1-6h" if h < 6 else "6-24h" if h < 24 else "1-3d" if h < 72 else "3d+"
    except Exception:
        return "?"


CUTS = [
    ("week", week), ("league", lambda s: s["league"]), ("market type", lambda s: s["_mt"]),
    ("how it was flagged (why)", lambda s: s["why"]), ("surface (source)", lambda s: str(s.get("source"))),
    ("score model", lambda s: str(s.get("model"))), ("v4 score band", band_conv),
    ("sharp entry price", band_price), ("bettor hold", band_hold), ("bettor stake", band_stake),
    ("underdog", lambda s: "dog" if s["dog"] else "favourite/pickem"),
    ("fresh", lambda s: "fresh" if s["fresh"] else "stale"),
    ("stacked (2+ legs same game)", lambda s: "stacked" if (s.get("stack") or 1) >= 2 else "single"),
    ("consensus", lambda s: str(s.get("consensus") or "?")),
    ("lead time when first flagged", hours_before),
]


def in_window(s): return .55 <= s["entry"] <= .643 and s["_mt"] != "period"


def report(d0, d1):
    rows = load_signals(d0, d1)
    v4 = [s for s in rows if s.get("model") == "v4"]
    out = [f"# Live window evaluation — {d0} → {d1} — generated {date.today().isoformat()}", ""]
    out.append(f"{len(rows)} settled suggestions on {len({s['slug'] for s in rows})} games from {len({s['user'] for s in rows})} traders "
               f"(ledgers/*.json, first time each position was shown). {len(v4)} were scored by the live model (v4). "
               f"Graded 1u flat at the sharp's entry + 1.5 pts vig. CLV = close − entry, in pts.")
    out.append("")
    out.append("## 1. Everything the system showed"); out.append(HEAD); out.append(line("ALL suggestions", rows)); out.append("")
    for title, fn in CUTS:
        g = defaultdict(list)
        for s in rows: g[fn(s)].append(s)
        out.append(f"### by {title}"); out.append(HEAD)
        for k in sorted(g, key=lambda k: (-len(g[k]) if title in ("league",) else 0, k)):
            out.append(line(k, g[k]))
        out.append("")
    # verdicts
    vc = Counter(s.get("verdict") for s in rows if not s["won"])
    out.append(f"Losses: {vc.get('bad read', 0)} 'bad read' (close ≤ entry + 1 pt: the market moved away or sat still) vs {vc.get('unlucky', 0)} 'unlucky' (close > entry + 1 pt: beat the close and lost).")
    out.append("")
    # counterfactual rules on v4 rows
    out.append("## 2. What each rule did, on the rows the live model scored (v4)")
    out.append("Each row is a filter applied to the same v4 suggestions. The live pick rule is **score ≥ 70 inside −122..−180, no period markets**.")
    out.append(HEAD)
    rules = [
        ("all v4 suggestions", lambda s: True),
        ("alert level: score >= 65", lambda s: (s.get("conv") or 0) >= 65),
        ("favourites only (entry >= .55)", lambda s: s["entry"] >= .55),
        ("underdogs only (entry < .50)", lambda s: s["entry"] < .50),
        ("window -122..-180 (any score)", in_window),
        ("LIVE PICK RULE: window + score >= 70", lambda s: in_window(s) and (s.get("conv") or 0) >= 70),
        ("  core: window + score >= 80", lambda s: in_window(s) and (s.get("conv") or 0) >= 80),
        ("  window + 70-79 only", lambda s: in_window(s) and 70 <= (s.get("conv") or 0) < 80),
        ("  window + 90+ only", lambda s: in_window(s) and (s.get("conv") or 0) >= 90),
        ("  pick rule, no totals", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and s["_mt"] != "total"),
        ("  pick rule, fresh only", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and s["fresh"]),
        ("  pick rule, hold >= 3%", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and s["hold"] >= 3),
        ("  pick rule, stake >= $25K", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and s["stake"] >= 25e3),
        ("  pick rule, consensus >= 2", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and (s.get("consensus") or 1) >= 2),
        ("  pick rule, single sharp only", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and (s.get("consensus") or 1) < 2),
        ("  pick rule, NFL+NCAAF only", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and s["league"] in ("NFL", "NCAAF")),
        ("  pick rule, non-football", lambda s: in_window(s) and (s.get("conv") or 0) >= 70 and s["league"] not in ("NFL", "NCAAF")),
        ("pickem override band .50-.55, score>=70", lambda s: .50 <= s["entry"] < .55 and (s.get("conv") or 0) >= 70 and s["_mt"] != "period"),
        ("wider window .55-.75, score >= 70", lambda s: .55 <= s["entry"] <= .75 and (s.get("conv") or 0) >= 70 and s["_mt"] != "period"),
        ("shorter than -180, score >= 70", lambda s: s["entry"] > .643 and (s.get("conv") or 0) >= 70 and s["_mt"] != "period"),
    ]
    for label, fn in rules:
        out.append(line(label, [s for s in v4 if fn(s)], 40) or f"| {label} | 0 |")
    out.append("")
    # trader table
    out.append("## 3. Traders (8+ settled suggestions)")
    out.append("| trader | n | W-L | hit | flat ROI | CLV pts | beat | in-window n | in-window W-L | in-window ROI |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    g = defaultdict(list)
    for s in rows: g[s["user"]].append(s)
    for u, rs in sorted(g.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < 8: continue
        n = len(rs); w = sum(1 for s in rs if s["won"]); roi = 100 * sum(s["_flat"] for s in rs) / n
        clv = [s["clv"] for s in rs if s.get("clv") is not None]
        iw = [s for s in rs if in_window(s)]; iwn = len(iw); iww = sum(1 for s in iw if s["won"])
        iwr = f"{100*sum(s['_flat'] for s in iw)/iwn:+.0f}%" if iwn else "—"
        out.append(f"| {u} | {n} | {w}-{n-w} | {w/n:.0%} | {roi:+.1f}% | {100*sum(clv)/len(clv):+.2f} | {sum(1 for x in clv if x>0)}/{len(clv)} | {iwn} | {iww}-{iwn-iww} | {iwr} |")
    out.append("")
    # picks in window
    picks = [p for p in R.load() if d0 <= (p.get("date") or "") <= d1]
    prs = [(p, R.flat(p)) for p in picks]; prs = [(p, f) for p, f in prs if f is not None]
    out.append("## 4. The graded public picks in this window (book price, published units)")
    out.append(R.HEAD); out.append(R.line("published picks", prs))
    for title, fn in [("price band", R.band_price), ("score band", R.band_conv), ("market type", R.market_type), ("override", lambda p: "override" if p.get("price_override") else "default window"), ("league", lambda p: p.get("league") or "?"), ("week", R.week_of)]:
        gg = defaultdict(list)
        for p, f in prs: gg[fn(p)].append((p, f))
        out.append(f"### picks by {title}"); out.append(R.HEAD)
        for k in sorted(gg): out.append(R.line(k, gg[k]))
        out.append("")
    # timeline for the pick rule on v4 rows
    out.append("## 5. Day by day — the live pick rule applied to every v4 suggestion (flat 1u)")
    out.append("| day | n | W-L | day units | cumulative |\n|---|---:|---:|---:|---:|")
    pr = sorted([s for s in v4 if in_window(s) and (s.get("conv") or 0) >= 70], key=lambda s: s["start"])
    byday = defaultdict(list)
    for s in pr: byday[s["start"][:10]].append(s)
    cum = 0
    for d in sorted(byday):
        rs = byday[d]; u = sum(s["_flat"] for s in rs); cum += u
        out.append(f"| {d} | {len(rs)} | {sum(1 for s in rs if s['won'])}-{sum(1 for s in rs if not s['won'])} | {u:+.1f} | {cum:+.1f} |")
    out.append("")
    out.append("_Cuts under ~40 games or with a CI through zero are noise._")
    return "\n".join(x for x in out if x is not None)


if __name__ == "__main__":
    a = sys.argv[1:]
    d0 = a[a.index("--from") + 1] if "--from" in a else "2026-09-01"
    d1 = a[a.index("--to") + 1] if "--to" in a else date.today().isoformat()
    txt = report(d0, d1); print(txt)
    if "--write" in a:
        p = os.path.join(REPO, "results", "live", f"eval-{d0}_{d1}.md"); open(p, "w").write(txt + "\n"); print(f"\nwrote {p}", file=sys.stderr)
