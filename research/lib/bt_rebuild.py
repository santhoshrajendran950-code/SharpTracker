#!/usr/bin/env python3
"""
Stage 3 — rebuild each wallet's positions as they stood at T = kickoff - 60m.

This is the point of the whole exercise: Polymarket trades are public, so a
wallet's book at any past moment can be reconstructed from /activity and scored
against a result that is already known. That turns "wait two months for the
forward ledger" into "grade several hundred settled games today".

Net position per asset at T, from the row types that move shares:

    TRADE BUY    shares += size, cost += usdcSize
    TRADE SELL   shares -= size, cost reduced pro-rata (average-cost basis)
    SPLIT        shares += size   (mint both sides from USDC)
    MERGE        shares -= size   (burn both sides back to USDC)
    CONVERSION   shares -= size   (neg-risk conversion out of this asset)

MAKER_REBATE, TAKER_REBATE, YIELD, REWARD and REFERRAL_REWARD are wallet-level
income and touch no market — a REWARD row carries outcomeIndex 999 and an empty
title. REDEEM is post-settlement and therefore always after T. All are ignored.

Measured on a 12-wallet, 6-day sample: 3,227 TRADE rows to 383 REDEEM and 56
income rows, with zero SPLIT/MERGE/CONVERSION. Those three are implemented
anyway, but any position whose shares depend on them is stamped
`basis_uncertain` — their USDC leg is a whole-transaction figure that cannot be
attributed to one outcome, so the cost basis (and therefore the stake and the
entry price) is not trustworthy. The report counts them rather than hiding them.

Truncation guard: net shares going negative means the position was opened before
the activity pull started. Those are flagged `truncated` and dropped, never
silently treated as a small position.
"""
import json, os, sys, threading, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import btlib as B
chk = B.chk

LOOKBACK_DAYS = 21     # before the window's earliest kickoff, to catch early entries
CHUNK_DAYS = 3         # /activity offset caps at 5000; page in small windows
PAGE, OFFSET_CAP = 500, 5000
T_MINUTES = 60         # primary snapshot: kickoff - 60m; override with argv[1]
                       # (the task's kickoff-3h sensitivity check is `180`)
SHARE_TYPES = {"TRADE", "SPLIT", "MERGE", "CONVERSION"}
IGNORE_TYPES = {"MAKER_REBATE", "TAKER_REBATE", "YIELD", "REWARD",
                "REFERRAL_REWARD", "REDEEM"}

_fail = []
_lk = threading.Lock()


def page_window(w, a, b):
    """One [a,b) activity window, paged. Returns (rows, filled, failed)."""
    rows = []
    for off in range(0, OFFSET_CAP + PAGE, PAGE):
        got = B.get(f"https://data-api.polymarket.com/activity?user={w}"
                    f"&start={a}&end={b}&limit={PAGE}&offset={off}")
        if got is None:
            return rows, False, True
        rows.extend(got)
        if len(got) < PAGE:
            return rows, False, False
        if off + PAGE > OFFSET_CAP:
            return rows, True, False          # hit the cap with more to come
    return rows, True, False


def chunks(lo, hi, days=CHUNK_DAYS):
    """The window as CHUNK_DAYS slices.

    Starting from one 51-day window and letting it split down to size looked
    equivalent and was not: every level that fills costs a full 11-request
    paging sweep before it splits, so the heaviest wallets — exactly the ones
    worth reading — cost ~180 serial requests each and a smoke test of 8 of them
    ran over ten minutes. Seeding at 3 days makes the common case one request.
    """
    out, a = [], lo
    step = days * 86400
    while a < hi:
        out.append((a, min(a + step, hi)))
        a += step
    return out


def pull_chunk(w, a, b):
    """One chunk, halving it if it fills the 5000-offset cap."""
    rows, failed, stack = [], False, [(a, b)]
    while stack:
        x, y = stack.pop()
        got, filled, fail = page_window(w, x, y)
        failed = failed or fail
        if filled and y - x > 3600:
            mid = x + (y - x) // 2            # too dense — halve it and retry
            stack.append((x, mid))
            stack.append((mid, y))
            continue
        if filled:
            failed = True                     # <1h window still capped: give up loudly
        rows.extend(got)
    return rows, failed


def has_activity(w, lo, hi):
    """One request: does this wallet do ANYTHING in the window?

    Most of the pool is selected on a PRIOR record, so a large share of it is
    dormant now. Probing first turns a 17-request history pull into a single
    request for every wallet that never shows up. None means the probe FAILED —
    which is not the same as 'dormant' and must not be treated as such.
    """
    r = B.get(f"https://data-api.polymarket.com/activity?user={w}"
              f"&start={lo}&end={hi}&limit=1&offset=0")
    if r is None:
        return None
    return bool(r)


def slug_index(slate):
    """eventSlug -> slate slug. check.py matches `es == s or es.startswith(s+'-')`;
    some slugs omit the year, so a trailing-segment trim reproduces that match
    without scanning 5,000 slugs per row."""
    exact = set(slate)

    def resolve(es):
        if not es:
            return None
        if es in exact:
            return es
        parts = es.split("-")
        for n in range(len(parts) - 1, 1, -1):
            cand = "-".join(parts[:n])
            if cand in exact:
                return cand
        return None
    return resolve


def _new_state():
    return defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "buy_sh": 0.0,
                                "buy_cost": 0.0, "uncertain": False,
                                "neg": False, "n": 0, "meta": None})


def typical_at(rows_sorted, checkpoints):
    """Median position size at each checkpoint, in ONE pass.

    Recomputing the whole book per event was O(events x rows) with a sort inside
    the inner loop — on a wallet with 50 slate games and 10k rows that is the
    entire runtime. Checkpoints are walked in time order against a single
    running state instead.
    """
    out, st, i = {}, _new_state(), 0
    for t in sorted(checkpoints):
        while i < len(rows_sorted) and (rows_sorted[i].get("timestamp") or 0) <= t:
            _apply(st, rows_sorted[i])
            i += 1
        sizes = sorted(v["cost"] for v in st.values()
                       if v["shares"] > 1e-6 and v["cost"] > 0)
        out[t] = (sizes[len(sizes) // 2] if sizes else 0.0, len(sizes))
    return out


def _apply(st, r):
    """Apply one activity row to the running book. Share-moving types only."""
    typ = r.get("type")
    if typ in IGNORE_TYPES or typ not in SHARE_TYPES:
        return
    a = r.get("asset")
    if not a:
        return
    size = float(r.get("size") or 0)
    usdc = abs(float(r.get("usdcSize") or 0))
    s = st[a]
    s["n"] += 1
    if s["meta"] is None:
        s["meta"] = {"eventSlug": r.get("eventSlug"), "title": r.get("title"),
                     "outcome": r.get("outcome"), "outcomeIndex": r.get("outcomeIndex")}
    if typ == "TRADE":
        if (r.get("side") or "").upper() == "BUY":
            s["shares"] += size
            s["cost"] += usdc
            s["buy_sh"] += size
            s["buy_cost"] += usdc
        else:
            if s["shares"] <= 0:
                s["neg"] = True               # sold something we never saw bought
                return
            cut = min(size, s["shares"])
            s["cost"] -= s["cost"] * (cut / s["shares"])
            s["shares"] -= cut
            if size > cut + 1e-9:
                s["neg"] = True
    elif typ == "SPLIT":
        s["shares"] += size
        s["uncertain"] = True                 # USDC leg spans every outcome
    else:                                     # MERGE / CONVERSION
        if s["shares"] <= 0:
            s["neg"] = True
            return
        cut = min(size, s["shares"])
        s["cost"] -= s["cost"] * (cut / s["shares"])
        s["shares"] -= cut
        s["uncertain"] = True
        if size > cut + 1e-9:
            s["neg"] = True


def positions_at(rows, t_ts):
    """Net position per asset as of t_ts, average-cost basis."""
    st = _new_state()
    for r in sorted(rows, key=lambda x: x.get("timestamp") or 0):
        ts = r.get("timestamp") or 0
        if ts > t_ts:
            break
        _apply(st, r)
    return st


def main():
    global T_MINUTES
    t0 = time.time()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        T_MINUTES = int(args[0])
    limit = None
    if "--limit" in sys.argv:                 # smoke-test a handful of wallets
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    suffix = "" if T_MINUTES == 60 else f"_t{T_MINUTES}"
    if limit:
        suffix += f"_n{limit}"
    sl = B.load("bt_slate.json")
    slate = sl["slate"]
    pool = B.load("bt_pool.json")["pool"]
    if limit:
        # active wallets first, so a smoke test actually exercises the parser
        pool = sorted(pool, key=lambda p: -(p["month_pnl"] or 0))[:limit]
    resolve = slug_index(slate)

    kicks = [v["kickoff_ts"] for v in slate.values()]
    lo = min(kicks) - LOOKBACK_DAYS * 86400
    hi = max(kicks) + 3600
    print(f"REBUILD · {len(pool)} wallets · {len(slate)} events · "
          f"activity {datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d} .. "
          f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d} "
          f"(window + {LOOKBACK_DAYS}d lookback)", flush=True)

    # kickoff -> the slate events starting then; positions are snapshotted per event
    by_slug = {s: v for s, v in slate.items()}
    out, meta = [], {"wallets_ok": 0, "wallets_failed": 0, "rows": 0,
                     "truncated": 0, "uncertain": 0, "no_activity": 0}

    # Phase 1 — one probe request per wallet. Most of the pool was selected on a
    # PRIOR record, so much of it is dormant now; probing turns a 17-request
    # history pull into one request for every wallet that never trades.
    print(f"probing {len(pool)} wallets for any activity in the window...", flush=True)
    probe = {}
    for p, act in zip(pool, B.pmap(lambda p: has_activity(p["wallet"], lo, hi),
                                   pool, label="probe", every=100)):
        probe[p["wallet"]] = act
    active = [p for p in pool if probe[p["wallet"]]]
    probe_failed = [p["wallet"] for p in pool if probe[p["wallet"]] is None]
    print(f"  {len(active)} active · {len(pool)-len(active)-len(probe_failed)} dormant"
          + (f" · !! {len(probe_failed)} probes FAILED (treated as failures, "
             f"not as dormant)" if probe_failed else ""), flush=True)
    _fail.extend(probe_failed)

    # Phase 2 — flat (wallet, chunk) task list. Bounding concurrency over CHUNKS
    # rather than over wallets keeps all 6 slots busy: bounding over wallets left
    # each worker walking 17 chunks serially while the rate budget sat idle.
    tasks = [(p["wallet"], a, b) for p in active for a, b in chunks(lo, hi)]
    print(f"pulling {len(tasks)} activity chunks ({CHUNK_DAYS}d each) "
          f"for {len(active)} wallets...", flush=True)
    by_wallet = defaultdict(list)
    chunk_failed = set()

    def pull(t):
        w, a, b = t
        rows, failed = pull_chunk(w, a, b)
        return w, rows, failed

    for w, rows, failed in B.pmap(pull, tasks, label="chunks", every=500):
        if failed:
            chunk_failed.add(w)
        by_wallet[w].extend(rows)
    if chunk_failed:
        print(f"  !! {len(chunk_failed)} wallets had a FAILED chunk — dropped "
              f"entirely rather than scored on a partial history", flush=True)
        _fail.extend(chunk_failed)

    def one(p):
        w = p["wallet"]
        if w in chunk_failed:
            return None
        rows = by_wallet.get(w) or []
        if not rows:
            return []
        # only rows that touch a slate event matter from here on
        keep = defaultdict(list)
        for r in rows:
            s = resolve(r.get("eventSlug"))
            if s:
                keep[s].append(r)
        if not keep:
            return []
        # the wallet's own typical size at each T, across every position it held
        # then — not just this game's. Median, so one whale bet is not "normal".
        rows_sorted = sorted(rows, key=lambda x: x.get("timestamp") or 0)
        checkpoints = {by_slug[s]["kickoff_ts"] - T_MINUTES * 60 for s in keep}
        typ_at = typical_at(rows_sorted, checkpoints)

        res = []
        for s, rs in keep.items():
            ev = by_slug[s]
            t_ts = ev["kickoff_ts"] - T_MINUTES * 60
            st = positions_at(rs, t_ts)
            typical, n_open = typ_at[t_ts]
            for asset, v in st.items():
                if v["shares"] <= 1e-6 or v["cost"] <= 0:
                    continue
                m = v["meta"] or {}
                res.append({
                    "wallet": w, "user": p["user"], "rank": p["rank"],
                    "in_sample": p["in_sample"],
                    "prior_pnl": p["prior_pnl"], "prior_hold": p["prior_hold"],
                    "month_pnl": p["month_pnl"],
                    "slug": s, "league": ev["league"], "game": ev["title"],
                    "kickoff_ts": ev["kickoff_ts"], "t_ts": t_ts,
                    "asset": asset, "market": m.get("title"), "side": m.get("outcome"),
                    "shares": v["shares"], "stake": v["cost"],
                    "entry": (v["buy_cost"] / v["buy_sh"]) if v["buy_sh"] > 0 else None,
                    "typical": typical, "n_open": n_open,
                    "n_rows": v["n"],
                    "basis_uncertain": v["uncertain"], "truncated": v["neg"],
                })
        return res

    # local work only now — the network phase is already done
    results = [one(p) for p in active]
    meta["no_activity"] += len(pool) - len(active) - len(probe_failed)
    for r in results:
        if r is None:
            meta["wallets_failed"] += 1
            continue
        meta["wallets_ok"] += 1
        if not r:
            meta["no_activity"] += 1
        for x in r:
            meta["rows"] += 1
            if x["truncated"]:
                meta["truncated"] += 1
            if x["basis_uncertain"]:
                meta["uncertain"] += 1
            out.append(x)

    print(f"\nwallets {meta['wallets_ok']}/{len(active)} rebuilt "
          f"(of {len(pool)} pooled, {len(active)} active), "
          f"{meta['wallets_failed']} FAILED · {meta['no_activity']} had no slate activity", flush=True)
    if meta["wallets_failed"]:
        print(f"!! {meta['wallets_failed']} wallets could not be rebuilt — "
              f"results are PARTIAL, not 'those wallets had no positions'", flush=True)
    print(f"{meta['rows']} raw positions at T=kickoff-{T_MINUTES}m · "
          f"{meta['truncated']} truncated (dropped downstream) · "
          f"{meta['uncertain']} basis-uncertain (SPLIT/MERGE)", flush=True)

    stats = B.report_stats("rebuild")
    B.save(f"bt_rebuild_meta{suffix}.json", {
        "built": datetime.now(timezone.utc).isoformat(),
        "t_minutes": T_MINUTES, "lookback_days": LOOKBACK_DAYS,
        "meta": meta, "failed_wallets": _fail, "requests": stats,
    })
    B.save_jsonl(f"bt_positions{suffix}.jsonl", out)
    print(f"done in {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
