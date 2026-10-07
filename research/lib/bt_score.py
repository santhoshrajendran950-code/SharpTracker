#!/usr/bin/env python3
"""
Stage 4 — price each rebuilt position at T, score it with check.py's own
scoring, and settle it against the real result.

Scoring is IMPORTED, never reimplemented: chk.conviction, chk.passes,
chk.sides_agree and chk.two_sided are the exact functions the live board runs,
so a backtest result and a live signal cannot drift apart. The frozen config and
chk.MODEL_VERSION are stamped onto every row.

Settlement comes from the CLOB token id, not from title matching. Each slate
market carries `tokens` alongside `outcomes` and `outcomePrices`, so a position's
asset id indexes straight into the settled price vector. resolve.py has to
fuzzy-match position titles against market questions because the positions API
does not hand back token ids; here we have them, and exact beats fuzzy.

Prices come from one prices-history call per token covering [T-30m, kickoff+5m]
at fidelity=1 (verified: fidelity is in MINUTES and yields ~60s spacing). That
single call serves all three price reads:
    price at T   — what the position looked like when it was scored
    close        — last trade before kickoff, for CLV
    worst drift  — the furthest the market moved AGAINST the pick before kickoff

CLV is reported two ways because they answer different questions:
    clv     = close - price_at_T   (the spec: did the market move our way after
              the moment we would have acted?)
    clv_entry = close - entry      (comparable to clv.py's lifetime numbers)
"""
import json, sys, threading, time
from collections import defaultdict
from datetime import datetime, timezone

import btlib as B
chk = B.chk

PRE_MIN, POST_MIN = 30, 5      # history window around [T, kickoff]
HIST = ("https://clob.polymarket.com/prices-history"
        "?market={t}&startTs={a}&endTs={b}&fidelity=1")

CONFIG = {
    "model": chk.MODEL_VERSION,
    "min_hold": chk.MIN_HOLD, "min_pnl": chk.MIN_PNL, "min_stake": chk.MIN_STAKE,
    "min_entry": chk.MIN_ENTRY, "max_entry": chk.MAX_ENTRY, "min_score": chk.MIN_SCORE,
    "dog_price": chk.DOG_PRICE, "fresh_drift": chk.FRESH_DRIFT,
    "stack_min": chk.STACK_MIN, "decided_px": chk.DECIDED_PX, "chalk_px": chk.CHALK_PX,
    "mult_stack": 1.15, "mult_dog": 1.45, "mult_fresh": 1.10,
}

_lk = threading.Lock()


def token_map(slate):
    """asset id -> (slug, question, side, settled_price, market_settled)."""
    m = {}
    for slug, ev in slate.items():
        for mk in ev["markets"]:
            pr = mk["prices"]
            ok = bool(pr) and len(pr) == len(mk["outcomes"]) and max(pr) >= 0.9
            for i, tok in enumerate(mk["tokens"]):
                m[str(tok)] = {
                    "slug": slug, "question": mk["question"],
                    "side": mk["outcomes"][i] if i < len(mk["outcomes"]) else None,
                    "settled": pr[i] if ok and i < len(pr) else None,
                    "market_settled": ok,
                }
    return m


def price_reads(tok, t_ts, kick_ts):
    """(price_at_T, close, worst_adverse_drift, n_points) for one token."""
    a, b = t_ts - PRE_MIN * 60, kick_ts + POST_MIN * 60
    d = B.get(HIST.format(t=tok, a=a, b=b))
    h = d.get("history") if isinstance(d, dict) else (d if isinstance(d, list) else None)
    if not h:
        return None, None, None, 0
    pts = sorted(((int(p["t"]), float(p["p"])) for p in h if "t" in p and "p" in p))
    if not pts:
        return None, None, None, 0
    at_t = next((p for t, p in reversed(pts) if t <= t_ts), None)
    if at_t is None:
        at_t = pts[0][1]                       # nothing before T: first available
    close = next((p for t, p in reversed(pts) if t <= kick_ts), None)
    between = [p for t, p in pts if t_ts <= t <= kick_ts]
    # adverse = the market moving DOWN on the side actually held
    drift = (at_t - min(between)) if between else None
    return at_t, close, drift, len(pts)


def main():
    t0 = time.time()
    src = sys.argv[1] if len(sys.argv) > 1 else "bt_positions.jsonl"
    dst = sys.argv[2] if len(sys.argv) > 2 else "bt_scored.jsonl"
    slate = B.load("bt_slate.json")["slate"]
    pos = B.load_jsonl(src)
    tm = token_map(slate)
    print(f"SCORE · {len(pos)} positions · {len(tm)} known tokens", flush=True)

    drop = {"truncated": 0, "unknown_token": 0, "unsettled": 0, "no_entry": 0,
            "no_price": 0, "basis_uncertain": 0, "no_prior": 0}
    live, insample = [], []
    for p in pos:
        if p["truncated"]:
            drop["truncated"] += 1
            continue
        if p["basis_uncertain"]:
            drop["basis_uncertain"] += 1      # cost basis not attributable
            continue
        t = tm.get(str(p["asset"]))
        if not t:
            drop["unknown_token"] += 1
            continue
        if not t["market_settled"] or t["settled"] is None:
            drop["unsettled"] += 1
            continue
        e = p["entry"]
        if not e or not (0 < e < 1):
            drop["no_entry"] += 1
            continue
        if p.get("prior_hold") is None or p.get("prior_pnl") is None:
            # Discovery-slice wallets (MONTH/PNL only) have NO out-of-sample
            # prior by construction — that is what in_sample flags. They cannot
            # be scored on a prior record, and feeding None into check.py's
            # sub_scores divides by it. Counted, then set aside for the
            # separate in-sample block the report labels as such.
            drop["no_prior"] += 1
            insample.append(p)
            continue
        p["market"] = t["question"] or p.get("market")
        p["side"] = t["side"] or p.get("side")
        p["won"] = t["settled"] >= 0.9
        live.append(p)

    print(f"  {len(live)} priceable out-of-sample · {len(insample)} in-sample "
          f"(discovery slice, no prior record) · dropped {drop}", flush=True)

    # one history call per (token, event) — tokens repeat across wallets
    need = {}
    for p in live:
        need.setdefault((str(p["asset"]), p["t_ts"], p["kickoff_ts"]), None)
    print(f"  pricing {len(need)} distinct tokens...", flush=True)
    fails = []

    def one(k):
        tok, t_ts, kick = k
        r = price_reads(tok, t_ts, kick)
        if r[0] is None:
            with _lk:
                fails.append(tok)
        return k, r

    px = {}
    for k, r in B.pmap(one, list(need), label="tokens", every=200):
        px[k] = r

    out = []
    for p in live:
        at_t, close, drift, n = px[(str(p["asset"]), p["t_ts"], p["kickoff_ts"])]
        if at_t is None:
            drop["no_price"] += 1
            continue
        e = p["entry"]
        p.update({
            "now": at_t, "close": close, "n_px": n,
            "clv": (close - at_t) if close is not None else None,
            "clv_entry": (close - e) if close is not None else None,
            "drift": drift,
            "hold": p["prior_hold"], "pnl": p["prior_pnl"],
            "dog": 0 < e < chk.DOG_PRICE,
            "decided": at_t >= chk.DECIDED_PX or at_t <= (1 - chk.DECIDED_PX),
            "chalk": e >= chk.CHALK_PX,
            "fresh": abs(at_t - e) / e <= chk.FRESH_DRIFT,
        })
        out.append(p)

    # stack / hedge / two-sided, exactly as check.py groups them
    groups = defaultdict(list)
    for h in out:
        groups[(h["wallet"], h["slug"])].append(h)
    for mem in groups.values():
        anyd = any(m["dog"] for m in mem)
        tot = sum(m["stake"] for m in mem)
        agree = chk.sides_agree(mem)
        book = chk.two_sided(mem)
        for m in mem:
            m["stack"] = len({x["market"] for x in mem}) if agree else 1
            m["stack_stake"] = tot if agree else m["stake"]
            m["stack_dog"] = anyd if agree else m["dog"]
            m["hedged"] = not agree
            m["two_sided"] = book

    for h in out:
        h["conv"] = chk.conviction(h)         # live scoring, imported
        h["qualifies"] = chk.passes(h)        # live gate, imported
        h["vs_typical"] = (h["stake"] / h["typical"]) if h["typical"] else None
        h["model"] = chk.MODEL_VERSION

    q = [h for h in out if h["qualifies"]]
    games = len({(h["slug"]) for h in q})
    indep = len({(h["wallet"], h["slug"]) for h in q})
    print(f"\n{len(out)} scored · {len(q)} qualify · {games} distinct games · "
          f"{indep} independent wallet-game outcomes", flush=True)
    if fails:
        print(f"!! {len(fails)} tokens had NO price history — those positions are "
              f"dropped, which is missing data and not a null result", flush=True)

    stats = B.report_stats("score")
    B.save("bt_score_meta.json", {
        "built": datetime.now(timezone.utc).isoformat(), "config": CONFIG,
        "src": src, "n_in": len(pos), "n_scored": len(out), "n_qualify": len(q),
        "games": games, "independent": indep, "dropped": drop,
        "price_failures": len(fails), "in_sample_setaside": len(insample),
        "requests": stats,
    })
    B.save_jsonl(dst, out)
    print(f"done in {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
