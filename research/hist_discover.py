#!/usr/bin/env python3
"""
Stage 2 — who bet size on these games, at the time.

    python3 hist_discover.py 2025-10

Reads windows/<ym>/slate.json, walks data-api /trades for every main market
(takerOnly=false so maker fills count too; filterAmount drops dust), and
writes windows/<ym>/trades.jsonl (one row per fill >= MIN_FILL) plus
windows/<ym>/wallets.json (wallet -> fills, $ traded, first/last seen).

Resumable: markets already in trades.jsonl are skipped. Concurrency 2.
"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import btlib as B

MIN_FILL = 2500
PAGE = 500
TR = ("https://data-api.polymarket.com/trades?market={cid}&takerOnly=false"
      "&filterType=CASH&filterAmount={amt}&limit={n}&offset={off}")


def fills(cid):
    out, off = [], 0
    while True:
        got = B.get(TR.format(cid=cid, amt=MIN_FILL, n=PAGE, off=off))
        if got is None:
            return out, True
        out += got
        if len(got) < PAGE:
            return out, False
        off += PAGE
        if off >= 10000:
            return out, True


def main():
    ym = sys.argv[1]
    d = os.path.join(HERE, "windows", *ym.split("/"))   # "2025-10" (football) or "mlb/2025-10"
    slate = json.load(open(os.path.join(d, "slate.json")))
    tpath = os.path.join(d, "trades.jsonl")
    done = set()
    if os.path.exists(tpath):
        for l in open(tpath):
            try:
                done.add(json.loads(l)["_cid"])
            except (ValueError, KeyError):
                pass
    jobs = [(g, m) for g in slate["games"] for m in g["markets"] if m["conditionId"] not in done]
    print(f"{ym}: {len(jobs)} markets to read ({len(done)} already done)", flush=True)
    t0 = time.time()
    partial = 0
    with open(tpath, "a") as f:
        def one(j):
            g, m = j
            rows, part = fills(m["conditionId"])
            return g, m, rows, part
        for g, m, rows, part in B.pmap(one, jobs, concurrency=2, label="markets", every=50):
            partial += part
            if not rows:
                rows = [{}]              # sentinel so the market counts as done
            for r in rows:
                r["_cid"] = m["conditionId"]
                r["_slug"] = g["slug"]
                r["_q"] = m["question"]
                r["_league"] = g["league"]
                r["_start"] = g["start"]
                f.write(json.dumps(r) + "\n")
    wallets = {}
    for l in open(tpath):
        r = json.loads(l)
        w = r.get("proxyWallet")
        if not w:
            continue
        usd = float(r.get("size") or 0) * float(r.get("price") or 0)
        x = wallets.setdefault(w, {"name": r.get("name") or r.get("pseudonym") or "", "fills": 0, "usd": 0.0,
                                   "first": r.get("timestamp"), "last": r.get("timestamp"), "markets": set()})
        x["fills"] += 1
        x["usd"] += usd
        x["markets"].add(r["_cid"])
        ts = r.get("timestamp")
        if ts:
            x["first"] = min(x["first"] or ts, ts)
            x["last"] = max(x["last"] or ts, ts)
    for x in wallets.values():
        x["markets"] = len(x["markets"])
    json.dump(wallets, open(os.path.join(d, "wallets.json"), "w"))
    print(f"{len(wallets)} wallets with a fill >= ${MIN_FILL:,} · {sum(x['fills'] for x in wallets.values())} fills · "
          f"{partial} markets partial · {B.STATS['ok']} ok / {B.STATS['fail']} failed · {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
