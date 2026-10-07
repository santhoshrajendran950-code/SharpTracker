#!/usr/bin/env python3
"""
Stage 3 — each wallet's record BEFORE the window, from its own activity.

    python3 hist_prior.py 2025-10 [--days 90] [--min-usd 10000]

The live system gates wallets on the leaderboard's lifetime hold and P/L. The
leaderboard cannot be read as of a past date, and reading it today would
select wallets on what they did AFTER the window — the survivorship trap the
production backtest documents. So the prior is rebuilt from /activity over the
PRIOR_DAYS before the window start:

    pnl   = Σ REDEEM usdc  −  Σ TRADE BUY usdc  +  Σ TRADE SELL usdc  (+ MERGE − SPLIT)
    vol   = Σ TRADE usdc
    hold  = pnl / vol

That is realised P/L on positions that settled inside the prior period, not the
leaderboard's mark-to-market figure; it is the closest honest analogue. Only
wallets that traded >= MIN_USD in the window are looked up (wallets.json), and
activity is pulled in 3-day chunks because /activity's offset caps at 5000.

Writes windows/<ym>/prior.json: wallet -> {pnl, vol, hold, n_trades, days_active}
Resumable; concurrency 2.
"""
import json, os, sys, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import btlib as B

ACT = "https://data-api.polymarket.com/activity?user={w}&start={a}&end={b}&limit=500&offset={off}"
CHUNK_DAYS = 3


def activity(w, a, b):
    """All rows in [a, b). Returns (rows, failed)."""
    rows, failed = [], False
    cur = a
    while cur < b:
        nxt = min(cur + CHUNK_DAYS * 86400, b)
        off = 0
        while True:
            got = B.get(ACT.format(w=w, a=cur, b=nxt, off=off))
            if got is None:
                failed = True
                break
            rows += got
            if len(got) < 500:
                break
            off += 500
            if off >= 5000:
                failed = True                # capped: this wallet is too busy for 3-day chunks
                break
        cur = nxt
    return rows, failed


def summarise(rows):
    pnl = vol = 0.0
    n = 0
    days, traded, settled, won = set(), set(), set(), set()
    for r in rows:
        t = (r.get("type") or "").upper()
        usd = float(r.get("usdcSize") or 0)
        side = (r.get("side") or "").upper()
        cid = r.get("conditionId") or r.get("asset")
        if t == "TRADE":
            n += 1
            vol += usd
            pnl += usd if side == "SELL" else -usd
            if r.get("timestamp"):
                days.add(int(r["timestamp"]) // 86400)
            if cid:
                traded.add(cid)
        elif t == "REDEEM":
            pnl += usd
            if cid:
                settled.add(cid)          # a REDEEM row exists for losers too (usdc 0)
                if usd > 0:
                    won.add(cid)
        elif t == "MERGE":
            pnl += usd
        elif t == "SPLIT":
            pnl -= usd
    return {"pnl": pnl, "vol": vol, "hold": (pnl / vol * 100) if vol else 0.0,
            "n_trades": n, "days_active": len(days), "n_markets": len(traded),
            "n_settled": len(settled), "n_won": len(won)}


def main():
    a = sys.argv[1:]
    ym = a[0]
    days = int(a[a.index("--days") + 1]) if "--days" in a else 90
    min_usd = float(a[a.index("--min-usd") + 1]) if "--min-usd" in a else 10_000
    d = os.path.join(HERE, "windows", *ym.split("/"))   # "2025-10" (football) or "mlb/2025-10"
    slate = json.load(open(os.path.join(d, "slate.json")))
    wallets = json.load(open(os.path.join(d, "wallets.json")))
    start = datetime.fromisoformat(slate["start"]).replace(tzinfo=timezone.utc)
    a_ts, b_ts = int((start - timedelta(days=days)).timestamp()), int(start.timestamp())
    ppath = os.path.join(d, "prior.json")
    prior = json.load(open(ppath)) if os.path.exists(ppath) else {}
    todo = [w for w, x in wallets.items() if x["usd"] >= min_usd and w not in prior]
    print(f"{ym}: {len(todo)} wallets traded >= ${min_usd:,.0f} in the window and need a prior "
          f"({len(prior)} done) · prior = {days}d before {slate['start']} · ~{days // CHUNK_DAYS} calls each", flush=True)
    t0 = time.time()
    n = 0

    def one(w):
        rows, failed = activity(w, a_ts, b_ts)
        s = summarise(rows)
        s["partial"] = failed
        return w, s
    for w, s in B.pmap(one, todo, concurrency=2, label="wallets", every=25):
        prior[w] = s
        n += 1
        if n % 50 == 0:
            tmp = ppath + ".tmp"
            json.dump(prior, open(tmp, "w"))
            os.replace(tmp, ppath)
    tmp = ppath + ".tmp"
    json.dump(prior, open(tmp, "w"))
    os.replace(tmp, ppath)
    ok = [s for s in prior.values() if not s.get("partial")]
    gate = sum(1 for s in ok if s["hold"] >= 1.5 and s["pnl"] >= 250_000)
    gate_lo = sum(1 for s in ok if s["hold"] >= 1.5 and s["pnl"] >= 50_000)
    print(f"{len(prior)} priors · {sum(1 for s in prior.values() if s.get('partial'))} partial · "
          f"clear the production gate (1.5% / $250K): {gate} · at $50K: {gate_lo} · "
          f"{B.STATS['ok']} ok / {B.STATS['fail']} failed · {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
