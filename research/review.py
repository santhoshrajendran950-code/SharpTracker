#!/usr/bin/env python3
"""
Weekly model-health review of the LIVE pick ledger (~/.nightly-line/picks.jsonl).

Every graded pick, cut every way the alert/pick rules could be wrong:
score band, entry-price band, market type, league, hold band, wager band,
consensus, trader override, trusted size step, sizing version, tier, week.
Flat ROI (1u per pick at the recorded book price) with a game-clustered 90%
bootstrap CI, sized ROI, CLV vs the Polymarket close and beat-close rate.
Progress toward the KPI: signal CLV (clv_entry) >= 0 over 200 graded v4 picks (s5+s6).

    python3 research/review.py                 # print
    python3 research/review.py --days 28       # window
    python3 research/review.py --write         # also results/live/<today>.md

Read-only. Never touches production. Stdlib only (host python is 3.9).
The thresholds here mirror production (check.py / units.py / picks.py); if
those move, move these — the cut labels say which rule each one tests.
"""
import json, os, random, re, sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

LIVE = os.path.expanduser("~/.nightly-line")
PICKS = os.path.join(LIVE, "picks.jsonl")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "results", "live")
BOOT, SEED = 2000, 7
TARGET_PICKS = 200          # the KPI horizon (docs/alert-rules.md §KPI)


def load(days=None):
    rows = [json.loads(l) for l in open(PICKS) if l.strip()]
    rows = [p for p in rows if p.get("result") in ("won", "lost")]
    if days:
        cut = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        rows = [p for p in rows if (p.get("date") or "") >= cut]
    return rows


# ---- grading ---------------------------------------------------------------
def flat(p):
    """P/L of 1u at the recorded book price; None if a win is unpriceable."""
    if p["result"] == "lost":
        return -1.0
    o = p.get("odds")
    if not o:
        return None
    return o / 100.0 if o > 0 else 100.0 / -o


def sized(p):
    u = p.get("units") or 0
    f = flat(p)
    return None if f is None else u * f


def roi_ci(prs, n=BOOT):
    """Game-clustered bootstrap of flat ROI: resample GAMES, not legs."""
    games = defaultdict(list)
    for p, f in prs:
        games[p.get("slug") or p.get("game")].append(f)
    keys = list(games)
    if len(keys) < 5:
        return None
    rng = random.Random(SEED)
    outs = []
    for _ in range(n):
        s = [games[k] for k in rng.choices(keys, k=len(keys))]
        vals = [v for g in s for v in g]
        outs.append(100.0 * sum(vals) / len(vals))
    outs.sort()
    return outs[int(0.05 * n)], outs[int(0.95 * n)]


# ---- cut keys (mirror production thresholds) --------------------------------
def band_conv(p):            # check.py v4 gates: alert 65 / pick 70 / core 80
    c = p.get("conv") or 0
    return "<70" if c < 70 else "70-79" if c < 80 else "80-89" if c < 90 else "90+"


def band_price(p):           # picks.py window is 0.55-0.643 (-122..-180)
    e = p.get("sharp_entry")
    if e is None: return "?"
    if e < 0.50:  return "dog <+100"
    if e < 0.55:  return "pickem +100..-122"
    if e < 0.60:  return "-122..-150"
    if e < 0.643: return "-150..-180"
    return "shorter than -180"


def market_type(p):
    m = (p.get("market") or "") + " " + (p.get("pick") or "")
    if re.search(r"\b(1H|1Q|2H|1st|inning|half|quarter|period)\b", m, re.I): return "period"
    if re.search(r"O/U|Over|Under|total", m, re.I): return "total"
    if re.search(r"Spread|[+-]\d+(\.5)?\b", p.get("market") or "") or re.search(r"[+-]\d+(\.5)?$", p.get("pick") or ""): return "spread"
    return "moneyline"


def band_hold(p):            # v4 hold points: 5%+ / 3-5 / 2-3 / <2
    h = p.get("hold") or 0
    return "<2%" if h < 2 else "2-3%" if h < 3 else "3-5%" if h < 5 else "5%+"


def band_wager(p):           # v4 wager points: $100K+ / 25K / 10K / 5K / <5K
    s = p.get("stake") or 0
    return "<$5K" if s < 5e3 else "$5-10K" if s < 1e4 else "$10-25K" if s < 2.5e4 else "$25-100K" if s < 1e5 else "$100K+"


def band_vs_typical(p):      # s1's old ladder input — kept to keep refuting it
    v = p.get("vs_typical")
    if v is None: return "?"
    return "<1x" if v < 1 else "1-2x" if v < 2 else "2-5x" if v < 5 else "5-15x" if v < 15 else "15x+"


def consensus(p):
    c = p.get("consensus") or 1
    return "1" if c <= 1 else "2" if c == 2 else "3+"


def week_of(p):
    d = date.fromisoformat((p.get("date") or "1970-01-01")[:10])
    return (d - timedelta(days=d.weekday())).isoformat()


CUTS = [
    ("score band (v4; pick>=70, core>=80)",      band_conv),
    ("sharp entry price (window -122..-180)",    band_price),
    ("market type (totals sized x0.75)",         market_type),
    ("league (NFL sized x1.5)",                  lambda p: p.get("league") or "?"),
    ("bettor hold (v4 points)",                  band_hold),
    ("bettor wager (v4 points)",                 band_wager),
    ("consensus (no longer sizes)",              consensus),
    ("trader price override",                    lambda p: "override" if p.get("price_override") else "default window"),
    ("trusted size step (+1u)",                  lambda p: "trusted" if p.get("size_trusted") else "not trusted"),
    ("size vs trader's typical (s1 refuted)",    band_vs_typical),
    ("line-move tier (EXP-030)",                 lambda p: p.get("move_tier") or "pre-EXP-030"),
    ("tier",                                     lambda p: p.get("tier") or "?"),
    ("sizing version",                           lambda p: p.get("sizing") or "s1"),
    ("book",                                     lambda p: p.get("book") or "?"),
    ("week",                                     week_of),
]


def line(label, prs):
    if not prs: return None
    w = sum(1 for p, _ in prs if p["result"] == "won")
    n = len(prs)
    fl = 100.0 * sum(f for _, f in prs) / n
    ci = roi_ci(prs)
    cis = f"[{ci[0]:+.0f}, {ci[1]:+.0f}]" if ci else "   —    "
    risk = sum(p.get("units") or 0 for p, _ in prs)
    net = sum(s for s in (sized(p) for p, _ in prs) if s is not None)
    sz = f"{100*net/risk:+5.0f}%" if risk else "    —"
    clv = [p["clv"] for p, _ in prs if p.get("clv") is not None]
    clvs = f"{sum(clv)/len(clv):+5.2f}  {sum(1 for c in clv if c>0):>3}/{len(clv):<3}" if clv else "    —        "
    games = len({p.get("slug") or p.get("game") for p, _ in prs})
    return (f"| {label:<22} | {n:>3} | {w:>3}-{n-w:<3} | {w/n:4.0%} | {fl:+6.1f}% | {cis:>12} "
            f"| {sz} | {clvs} | {games:>3} |")


HEAD = ("| cut                    |   n |   W-L   | hit  | flat ROI |   90% CI     "
        "| s-ROI | CLV   beat   | games |\n"
        "|---|---:|---:|---:|---:|---:|---:|---|---:|")


def report(days=None):
    rows = load(days)
    prs = [(p, flat(p)) for p in rows]
    unpriced = sum(1 for _, f in prs if f is None)
    prs = [(p, f) for p, f in prs if f is not None]
    span = f"last {days}d" if days else "all time"
    out = [f"# Live pick review — {date.today().isoformat()} — {span}", ""]
    if not prs:
        out.append("No graded picks in this window."); return "\n".join(out)
    out.append(f"{len(rows)} graded picks, {len({p.get('slug') for p in rows})} games"
               + (f", {unpriced} won without a price (excluded)" if unpriced else "") + ".")
    out.append("Flat ROI = 1u per pick at the recorded book price; CI resamples games. "
               "s-ROI = at published units. CLV in pts vs the Polymarket close.")
    out.append("")
    # KPI — 2026-10-14: v4 picks are s5 (to 10-07) and s6 (flat 1u, from 10-07);
    # the KPI metric is the SIGNAL CLV (clv_entry: close vs the sharp's entry),
    # per the 2026-10-07 window evaluation. Book CLV (clv) is shown beside it.
    kpi = [(p, f) for p, f in prs if (p.get("sizing") or "") in ("s5", "s6")]
    sclv = [p["clv_entry"] for p, _ in kpi if p.get("clv_entry") is not None]
    bclv = [p["clv"] for p, _ in kpi if p.get("clv") is not None]
    out.append("## KPI — signal CLV (clv_entry) >= 0 over 200 graded v4 picks (s5+s6)")
    if sclv:
        m = sum(sclv) / len(sclv)
        sd = (sum((c - m) ** 2 for c in sclv) / max(1, len(sclv) - 1)) ** 0.5
        se = sd / len(sclv) ** 0.5
        bm = f"{sum(bclv)/len(bclv):+.2f}" if bclv else "—"
        out.append(f"- {len(kpi)} / {TARGET_PICKS} v4 picks graded ({100*len(kpi)/TARGET_PICKS:.0f}%). "
                   f"Signal CLV {m:+.2f} pts (±{1.645*se:.2f} at 90%), beat entry {sum(1 for c in sclv if c>0)}/{len(sclv)}; "
                   f"book CLV {bm}.")
        out.append(f"- v4 record: {line('v4 (s5+s6)', kpi)}")
    else:
        out.append(f"- {len(kpi)} / {TARGET_PICKS} v4 picks graded; none carries clv_entry yet.")
    out.append("")
    out.append("## Everything"); out.append(HEAD); out.append(line("ALL", prs)); out.append("")
    for title, fn in CUTS:
        groups = defaultdict(list)
        for p, f in prs: groups[fn(p)].append((p, f))
        out.append(f"## by {title}"); out.append(HEAD)
        for k in sorted(groups, key=lambda k: (-len(groups[k]) if title.startswith(("league", "book")) else 0, k)):
            out.append(line(k, groups[k]))
        out.append("")
    # drawdown / streak
    bal = peak = dd = 0.0; run = worst = 0
    for p, _ in sorted(prs, key=lambda t: t[0].get("start") or ""):
        s = sized(p) or 0; bal += s; peak = max(peak, bal); dd = max(dd, peak - bal)
        run = run + 1 if p["result"] == "lost" else 0; worst = max(worst, run)
    out.append(f"Sized: max drawdown {dd:.1f}u, longest losing run {worst}.")
    out.append("")
    out.append("_A cut with fewer than ~40 games or a CI that includes 0 is noise. "
               "'Nothing survives' is a legitimate result._")
    return "\n".join(x for x in out if x is not None)


if __name__ == "__main__":
    days = None; write = False
    a = sys.argv[1:]
    if "--days" in a: days = int(a[a.index("--days") + 1])
    if "--write" in a: write = True
    txt = report(days)
    print(txt)
    if write:
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, f"{date.today().isoformat()}.md")
        open(path, "w").write(txt + "\n")
        print(f"\nwrote {path}", file=sys.stderr)
