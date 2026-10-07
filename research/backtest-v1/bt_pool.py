#!/usr/bin/env python3
"""
Stage 1 — the out-of-sample candidate pool.

The part most likely to go wrong, so it gets the most care.

Today's ALL/PNL ranking already contains the P/L each wallet earned DURING the
test window. Selecting on it and then scoring inside that window is circular:
you would be picking wallets because they won the games you are about to grade
them on. So every wallet's PRIOR record is reconstructed as

    prior = ALL(today) - MONTH(today)

and the MIN_HOLD / MIN_PNL bars are applied to that, not to the lifetime row.
MONTH is the trailing-30-day slice, which is exactly the test window.

Two biases survive and both push results UPWARD. They are written into the
output so the report cannot forget them:

  * Survivorship. The ranking is read today, so a wallet that blew up during the
    window and fell out of the top ranks is missing. Pulling to DEPTH=1500 (vs
    the live POOL_SIZE=690) recovers most of the ones that merely slipped, but
    not the ones that fell past 1500.
  * The MONTH/PNL discovery slice is chosen ON test-window P/L by definition.
    Those wallets are kept but flagged in_sample=True, and the report scores
    them in a separate, clearly labelled block.

Hold is computed from the LEADERBOARD's own vol on both sides of the
subtraction, never from summed activity — many high-volume wallets never sell,
they buy both sides and MERGE, so a self-computed hold reads up to ~2x lower
than the leaderboard's and would silently re-rank the entire pool.
"""
import sys, time
from datetime import datetime, timezone

import btlib as B
chk = B.chk

DEPTH = 1500          # vs live POOL_SIZE=690 — see survivorship note above
MONTH_DEPTH = 500     # mirrors live MONTH_POOL
PAGE = 50
LB = "https://data-api.polymarket.com/v1/leaderboard?category=SPORTS"


def slice_all(depth):
    rows, seen = [], set()
    for off in range(0, depth, PAGE):
        got = B.get(f"{LB}&timePeriod=ALL&orderBy=PNL&limit={PAGE}&offset={off}")
        if not got:
            print(f"  !! ALL slice stopped at offset {off}", flush=True)
            break
        for t in got:
            w = t.get("proxyWallet")
            if w and w not in seen:
                seen.add(w)
                rows.append(t)
        if len(got) < PAGE:
            break
    return rows


def slice_month(depth):
    out = {}
    for off in range(0, depth, PAGE):
        got = B.get(f"{LB}&timePeriod=MONTH&orderBy=PNL&limit={PAGE}&offset={off}")
        if not got:
            print(f"  !! MONTH slice stopped at offset {off}", flush=True)
            break
        for i, t in enumerate(got):
            w = t.get("proxyWallet")
            if w:
                out[w] = {"pnl": t.get("pnl") or 0.0, "vol": t.get("vol") or 0.0,
                          "month_rank": off + i + 1}
        if len(got) < PAGE:
            break
    return out


def main():
    t0 = time.time()
    now = datetime.now(timezone.utc)
    print(f"POOL · out-of-sample · {now:%Y-%m-%d %H:%M}Z", flush=True)

    print(f"ALL/PNL to depth {DEPTH}...", flush=True)
    allrows = slice_all(DEPTH)
    print(f"  {len(allrows)} wallets", flush=True)

    print(f"MONTH/PNL to depth {MONTH_DEPTH} (the in-sample discovery slice)...", flush=True)
    month = slice_month(MONTH_DEPTH)
    print(f"  {len(month)} wallets", flush=True)

    # Wallets in the ALL slice need their own MONTH row: the top-500 MONTH slice
    # only covers the best month-performers, and most of the ALL slice is not in
    # it. Without this lookup their prior record would wrongly equal lifetime.
    need = [t["proxyWallet"] for t in allrows if t["proxyWallet"] not in month]
    print(f"MONTH row for {len(need)} ALL-slice wallets not in the top-{MONTH_DEPTH}...", flush=True)
    miss = []

    def one(w):
        r = B.get(f"{LB}&timePeriod=MONTH&orderBy=PNL&limit=1&user={w}")
        if r is None:
            miss.append(w)
            return (w, None)
        if not r:
            return (w, {"pnl": 0.0, "vol": 0.0, "month_rank": None})
        return (w, {"pnl": r[0].get("pnl") or 0.0, "vol": r[0].get("vol") or 0.0,
                    "month_rank": None})

    for w, m in B.pmap(one, need, label="month rows", every=100):
        if m is not None:
            month.setdefault(w, m)

    if miss:
        print(f"  !! {len(miss)} MONTH lookups FAILED — those wallets are dropped, "
              f"not silently treated as zero-month", flush=True)

    pool, dropped = [], {"no_month": 0, "hold": 0, "pnl": 0, "neg_vol": 0}
    for t in allrows:
        w = t["proxyWallet"]
        m = month.get(w)
        if m is None:
            dropped["no_month"] += 1
            continue
        a_pnl, a_vol = t.get("pnl") or 0.0, t.get("vol") or 0.0
        p_pnl, p_vol = a_pnl - m["pnl"], a_vol - m["vol"]
        if p_vol <= 0:
            # everything they ever did was inside the window: no prior record at
            # all, so there is nothing out-of-sample to select on.
            dropped["neg_vol"] += 1
            continue
        p_hold = p_pnl / p_vol * 100
        if p_pnl < chk.MIN_PNL:
            dropped["pnl"] += 1
            continue
        if p_hold < chk.MIN_HOLD:
            dropped["hold"] += 1
            continue
        pool.append({
            "wallet": w, "user": chk.short(t.get("userName"), w),
            "rank": int(t.get("rank") or 0),
            "prior_pnl": p_pnl, "prior_vol": p_vol, "prior_hold": p_hold,
            "all_pnl": a_pnl, "all_vol": a_vol,
            "month_pnl": m["pnl"], "month_vol": m["vol"],
            "in_sample": False,
        })

    # The discovery slice: wallets that exist only in MONTH/PNL. They are chosen
    # on test-window P/L, so they have no out-of-sample prior by construction.
    seen = {t["proxyWallet"] for t in allrows}
    n_disc = 0
    for w, m in month.items():
        if w in seen or m.get("month_rank") is None:
            continue
        pool.append({
            "wallet": w, "user": w[:10] + "…", "rank": 0,
            "prior_pnl": None, "prior_vol": None, "prior_hold": None,
            "all_pnl": None, "all_vol": None,
            "month_pnl": m["pnl"], "month_vol": m["vol"],
            "in_sample": True,
        })
        n_disc += 1

    oos = [p for p in pool if not p["in_sample"]]
    print(f"\nALL slice {len(allrows)} -> {len(oos)} clear the PRIOR bars "
          f"({chk.MIN_HOLD}% hold, ${chk.MIN_PNL:,} P/L)", flush=True)
    print(f"  dropped: {dropped['pnl']} prior P/L, {dropped['hold']} prior hold, "
          f"{dropped['neg_vol']} no prior volume, {dropped['no_month']} lookup failed", flush=True)
    print(f"  + {n_disc} MONTH-only discovery wallets, flagged in_sample", flush=True)

    stats = B.report_stats("pool")
    B.save("bt_pool.json", {
        "built": now.isoformat(), "model": chk.MODEL_VERSION,
        "depth": DEPTH, "month_depth": MONTH_DEPTH,
        "bars": {"min_hold": chk.MIN_HOLD, "min_pnl": chk.MIN_PNL},
        "biases": ["survivorship: ranking read today, wallets that fell past "
                   f"depth {DEPTH} during the window are missing; biases UP",
                   "MONTH/PNL slice selected on test-window P/L; in_sample=True"],
        "counts": {"all_slice": len(allrows), "oos": len(oos), "discovery": n_disc,
                   "dropped": dropped, "month_lookup_failed": len(miss)},
        "requests": stats, "pool": pool,
    })
    print(f"done in {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
