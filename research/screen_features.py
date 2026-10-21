#!/usr/bin/env python3
"""
Feature builder for the 2026 football rule screen (EXP-033).

    python3 screen_features.py 2026-09 2026-10 > /dev/null   # writes screen_rows.jsonl

Reads each window's frozen-scorer output and stamps the six PRE-REGISTERED
candidate dimensions onto every scored position. It adds no other features: the
screen is only allowed to look at these, and keeping the builder separate from
the screen makes that auditable.

  1 trail      distinct pooled sharps leaning the same way in that game
  2 quality    the wallet's 90-day prior realised P/L (gate at 50K/150K/250K)
  3 depth      the wallet's resolved positions in that prior 90 days
  4 price      the T-60 bettable price (the -122..-180 window and variants)
  5 relstake   stake / that wallet's own median stake (vs_typical)
  6 lead       hours between the wallet's first buy on the token and kickoff

Grading inputs carried through unchanged from the frozen scorer: `won`
(settlement), `now` (T-60 price -> bettable price + vig), `clv` (Polymarket
close minus T-60, in probability). Book-close CLV is joined separately from
bookclose.py if that window has it.

Nothing here filters. Every scored row comes out, so the screen can define its
own universe and the counts in the write-up are reproducible from one file.
"""
import json, os, re, sys
from collections import defaultdict
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                  # py3.9 without tzdata
    ET = None


def slate_date(kickoff_ts):
    """The slate a game belongs to, as US Eastern calendar date.

    NOT the slug's date: Polymarket slugs carry the UTC date, so a Sunday-night
    8:20pm ET kickoff is slugged Monday and a Monday-night game is slugged
    Tuesday. Splitting on that would scatter one NFL Sunday across three
    'slates' and put part of a slate on each side of the train/holdout
    boundary, which is exactly the leak the time split exists to prevent.
    """
    t = datetime.fromtimestamp(kickoff_ts, timezone.utc)
    return (t.astimezone(ET) if ET else t.astimezone()).date().isoformat()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import check as CK                                    # frozen v4, for _lean only

MIN_STAKE = 2500.0        # the scanner's own floor; a sharp below it is not "on" a side
SHARP_PNL = 250_000.0     # production quality bar, for counting who is "a sharp" on a side
SHARP_HOLD = 1.5
OUT = os.path.join(HERE, "screen_rows.jsonl")


def window_rows(ym):
    d = os.path.join(HERE, "windows", ym)
    rows = [json.loads(l) for l in open(os.path.join(d, "bt_scored.jsonl"))]
    prior = json.load(open(os.path.join(d, "prior.json")))
    book = {}
    bp = os.path.join(d, "bookclose.json")
    if os.path.exists(bp):
        book = json.load(open(bp))

    # --- dimension 6: first buy per (wallet, token), from the market-first trade
    # pull. hist_discover keeps fills >= $2,500, which is the same floor the
    # scanner uses, so a position's opening fill is in here unless it was built
    # out of sub-$2.5K clips.
    first = {}
    tp = os.path.join(d, "trades.jsonl")
    if os.path.exists(tp):
        for l in open(tp):
            t = json.loads(l)
            if t.get("side") != "BUY":
                continue
            k = (t["proxyWallet"], t["asset"])
            ts = t["timestamp"]
            if k not in first or ts < first[k]:
                first[k] = ts

    # --- dimension 1: consensus on the lean, via the frozen _lean() so that
    # "Chiefs ML" and "Chiefs -2.5" count as one opinion, exactly as production
    # counts it.
    #
    # Counted TWICE, because the obvious definition turns out to be useless.
    # `trail_pool` counts every pooled wallet (the pool clears the research
    # gate: hold >= 1.5%, 90d P/L >= $50K, vol >= $500K). That pool is 319
    # wallets, so 79% of candidate positions already have 4+ of them on the same
    # lean and the registered levels {2,3,4+} cannot discriminate at all.
    # `trail` therefore counts only wallets clearing the PRODUCTION quality bar
    # (90d prior P/L >= $250K and hold >= 1.5%) — 36 wallets — which is what
    # production means when it says a side has N sharps on it. Both are written;
    # the screen uses `trail` and the write-up reports why.
    lean_pool = defaultdict(lambda: defaultdict(set))
    lean_sharp = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if r["stake"] < MIN_STAKE or r.get("two_sided"):
            continue
        grp, tok = CK._lean(r)
        lean_pool[(r["slug"], grp)][tok].add(r["wallet"])
        if r["prior_pnl"] >= SHARP_PNL and r["hold"] >= SHARP_HOLD:
            lean_sharp[(r["slug"], grp)][tok].add(r["wallet"])
    lean_wallets = lean_sharp

    out = []
    for r in rows:
        grp, tok = CK._lean(r)
        sides = lean_wallets.get((r["slug"], grp), {})
        same = sides.get(tok, set())
        other = set().union(*[v for k, v in sides.items() if k != tok]) or set()
        pri = prior.get(r["wallet"]) or {}
        fb = first.get((r["wallet"], r["asset"]))
        bk = book.get(f"{r['slug']}|{r['market']}|{r['side']}") or {}

        out.append({
            # identity / clustering
            "ym": ym, "slug": r["slug"], "league": r["league"], "game": r["game"],
            "date": slate_date(r["kickoff_ts"]), "wallet": r["wallet"], "user": r["user"],
            "market": r["market"], "side": r["side"], "mkt_type": mkt_type(r),
            "kickoff_ts": r["kickoff_ts"],
            # grading
            "won": r["won"], "now": r["now"], "entry": r["entry"],
            "clv_pm": r.get("clv"), "close_pm": r.get("close"),
            "clv_book": (round((bk["close_fair"] - r["now"]) * 100, 2)
                         if bk.get("close_fair") is not None else None),
            "n_books": bk.get("n_books"),
            # frozen-scorer verdict, used as the production baseline
            "conv": r["conv"], "qualifies": r["qualifies"],
            # the six dimensions
            "trail": len(same),
            "trail_pool": max(len(lean_pool.get((r["slug"], grp), {}).get(tok, set())), 1),
            "opposed": len(other - same),
            "quality": r["prior_pnl"],
            "depth": pri.get("n_settled"),
            "price": r["now"],
            "relstake": r.get("vs_typical"),
            "lead_h": (round((r["kickoff_ts"] - fb) / 3600.0, 2) if fb else None),
            # carried for reporting only, never filtered on
            "stake": r["stake"], "hold": r["hold"], "typical": r.get("typical"),
        })
    return out


def mkt_type(r):
    m = r["market"] or ""
    if re.search(r"O/U", m):
        return "total"
    if m.startswith("Spread:"):
        return "spread"
    return "ml"


def main(yms):
    rows = []
    for ym in yms:
        got = window_rows(ym)
        rows += got
        print(f"{ym}: {len(got)} scored rows", file=sys.stderr)
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    # coverage, because a dimension that is mostly null cannot carry a rule
    n = len(rows)
    def cov(k):
        c = sum(1 for r in rows if r.get(k) is not None)
        return f"{k}: {c}/{n} ({100*c/n:.0f}%)"
    print(f"wrote {OUT} ({n} rows)", file=sys.stderr)
    for k in ("quality", "depth", "price", "relstake", "lead_h", "clv_pm", "clv_book"):
        print("  " + cov(k), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["2026-09", "2026-10"]))
