#!/usr/bin/env python3
"""
Market-first sweep: who is holding size on TODAY's games, from ANY wallet.

    python3 holders.py mlb tennis            # upcoming games today, these tags
    python3 holders.py mlb --min 25000       # only positions worth >= $25K

The board is wallet-first: it walks ~1,000 tracked wallets and asks what they
hold. That misses any bettor outside the pool. This walks the other way — every
main market on today's slate, its top holders (data-api /holders), then each
holder's own leaderboard record — and reports the ones the pool does not know.

A holder's $ is shares x current price, i.e. position VALUE now, not what they
paid; the entry is unknown from this endpoint. Records come from the per-user
leaderboard lookup (ALL, SPORTS), which works for unranked wallets too.
"""
import json, os, re, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))
import btlib as B
import check as chk
import bigboard as BB

GAMMA = ("https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=false"
         "&limit=100&offset={off}&start_date_min={lo}&start_date_max={hi}")
HOLD = "https://data-api.polymarket.com/holders?market={cid}&limit=500"
LB = ("https://data-api.polymarket.com/v1/leaderboard?category=SPORTS"
      "&timePeriod=ALL&limit=1&user={w}")
TAGS = {"mlb": "mlb", "tennis": "tennis", "atp": "tennis", "wta": "tennis",
        "nfl": "nfl", "cfb": "cfb", "ncaaf": "cfb"}


def plist(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return []
    return x or []


def events(tag, lo, hi):
    out, off = [], 0
    while True:
        got = B.get(GAMMA.format(tag=tag, off=off, lo=lo, hi=hi)) or []
        out += got
        if len(got) < 100:
            return out
        off += 100


MINOR = re.compile(r"^(M15|M25|W15|W35|W50|W75|W100|ITF|Challenger|UTR)\b", re.I)


def main_markets(ev):
    """Moneyline, full-game spread, full-game total. Nothing else — an MLB event
    carries 27 markets and the props were 90% of the first run's 3,900 calls."""
    title = (ev.get("title") or "").strip()
    for m in ev.get("markets", []):
        q = (m.get("question") or "").strip()
        if m.get("closed"):
            continue
        if q == title or re.match(r"^Spread: .+\([+-][\d.]+\)$", q) \
                or re.match(rf"^{re.escape(title)}: O/U [\d.]+$", q):
            yield q, m


def holders_for(m):
    cid = m.get("conditionId")
    d = B.get(HOLD.format(cid=cid))
    names, toks = plist(m.get("outcomes")), plist(m.get("clobTokenIds"))
    prices = [float(p) for p in plist(m.get("outcomePrices"))] or [0.5] * len(names)
    out = []
    for blk in (d or []):
        tok = str(blk.get("token"))
        if tok not in [str(t) for t in toks]:
            continue
        i = [str(t) for t in toks].index(tok)
        for h in blk.get("holders", []):
            usd = float(h.get("amount") or 0) * prices[i]
            out.append((h.get("proxyWallet"), h.get("name") or h.get("pseudonym") or "", names[i], prices[i], usd))
    return out


def record(w, cache):
    if w in cache:
        return cache[w]
    r = B.get(LB.format(w=w)) or []
    r = r[0] if r else {}
    vol, pnl = float(r.get("vol") or 0), float(r.get("pnl") or 0)
    cache[w] = (pnl, vol, (pnl / vol * 100) if vol else 0.0, r.get("rank"))
    return cache[w]


def main():
    a = sys.argv[1:]
    min_usd = float(a[a.index("--min") + 1]) if "--min" in a else 10_000
    tags = sorted({TAGS[x] for x in a if x in TAGS}) or ["mlb", "tennis", "nfl", "cfb"]
    now = datetime.now(timezone.utc)
    lo, hi = (now - timedelta(hours=6)).strftime("%Y-%m-%d"), (now + timedelta(hours=30)).strftime("%Y-%m-%d")
    evs = []
    for t in tags:
        evs += events(t, lo, hi)
    # gamma's date filter returns the prop/inning SUB-events but not the main
    # game event (its startDate is set days earlier). Derive the main slug from
    # each sub-event and fetch it directly.
    bases = {re.sub(r"-(player-props|inning-\d+-winner|first-five-winner)$", "", e["slug"])
             for e in evs if re.search(r"-(player-props|inning-\d+-winner|first-five-winner)$", e["slug"])}
    have = {e["slug"] for e in evs}
    for b in sorted(bases - have):
        got = B.get(f"https://gamma-api.polymarket.com/events?slug={b}") or []
        evs += got
    seen, todays = set(), []
    for ev in evs:
        try:
            st = datetime.fromisoformat((ev.get("startTime") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if ev["slug"] in seen or st <= now - timedelta(hours=4) or st > now + timedelta(hours=20):
            continue
        if "player-props" in ev["slug"] or MINOR.match(ev.get("title") or ""):
            continue
        seen.add(ev["slug"])
        todays.append((st, ev))
    todays.sort(key=lambda t: t[0])
    print(f"HOLDERS · {now.astimezone():%a %-I:%M %p %Z} · {len(todays)} upcoming games ({', '.join(tags)}) · positions >= ${min_usd:,.0f}", flush=True)

    jobs = [(st, ev, q, m) for st, ev in todays for q, m in main_markets(ev)]
    B.CONCURRENCY = 2            # /holders throttles hard; the first run lost 95% of calls at 6
    print(f"  {len(jobs)} markets to read...", flush=True)
    rows = []
    def one(j):
        st, ev, q, m = j
        return [(st, ev, q) + h for h in holders_for(m)]
    for chunk in B.pmap(one, jobs, concurrency=2, label="markets", every=40):
        rows += [r for r in chunk if r[-1] >= min_usd]
    wallets = sorted({r[3] for r in rows})
    print(f"  {len(rows)} positions >= ${min_usd:,.0f} from {len(wallets)} wallets · looking up records...", flush=True)
    cache = {}
    for _ in B.pmap(lambda w: record(w, cache), wallets, concurrency=2, label="wallets", every=50):
        pass
    pool = set()
    try:
        for k, v in json.load(open(os.path.join(HERE, "check-snapshot.json"))).get("positions", {}).items():
            pool.add(str(k).split("|")[0])
            if isinstance(v, dict):
                pool.add(str(v.get("wallet")))
                pool.add(str(v.get("user")))
    except Exception:
        pass

    by_game = defaultdict(list)
    for r in rows:
        by_game[(r[0], r[1]["slug"], r[1]["title"])].append(r)
    for (st, slug, title), rs in sorted(by_game.items()):
        lg = "MLB" if slug.startswith("mlb") else "TENNIS"
        print(f"\n{lg} · {title} · {st.astimezone():%-I:%M %p}{'  (LIVE)' if st <= now else ''}")
        for r in sorted(rs, key=lambda r: -r[-1]):
            _, _, q, w, name, side, px, usd = r
            pnl, vol, hold, rank = cache.get(w, (0, 0, 0, None))
            gate = hold >= chk.MIN_HOLD and pnl >= chk.MIN_PNL
            tag = ("SHARP " if gate else "      ") + ("      " if (w in pool or name in pool) else "NEW   ")
            play = BB.play_text({"market": q, "side": side})
            print(f"   {tag}{usd:>10,.0f}  {play:24} {chk.am(px):>6}  {(name or w[:10]):18} hold {hold:5.1f}%  P/L {chk.m0(pnl):>10}  vol {chk.m0(vol):>8}")
    # --discover: write every gate-clearing wallet the pool does not know into
    # discovered.json, with its full leaderboard row so check.build_pool can
    # treat it exactly like a ranked wallet. Additive, deduped, never removes.
    if "--discover" in a:
        path = os.path.join(HERE, "discovered.json")
        try:
            disc = json.load(open(path))
        except (FileNotFoundError, ValueError):
            disc = {}
        added = 0
        for w in wallets:
            pnl, vol, hold, rank = cache.get(w, (0, 0, 0, None))
            if w in pool or w in disc or not (hold >= chk.MIN_HOLD and pnl >= chk.MIN_PNL):
                continue
            row = (B.get(LB.format(w=w)) or [{}])[0]
            if row.get("proxyWallet"):
                row["discovered"] = now.isoformat()
                disc[w] = row
                added += 1
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(disc, f)
        os.replace(tmp, path)
        print(f"\ndiscovered: +{added} new sharp wallet(s), {len(disc)} total in discovered.json")
    print(f"\n[{B.STATS['ok']} requests ok, {B.STATS['fail']} failed]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
