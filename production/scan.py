#!/usr/bin/env python3
"""
Sharp-money scan across every dated Polymarket sports game.

Finds positions held by the top ~690 all-time SPORTS-PnL traders (top 0.1% of
Polymarket's ~688K MAU), ranks them, and prints a digest. Stdlib + curl only.

Cost is driven by WALLETS, not games — it's 690 position lookups whether you
scan 3 games or 300, so the whole slate costs the same ~45s as one matchup.

PRIORITY MODEL (the "Kch-Temp shape", after the 2026-09-10 49ers play):
one high-hold trader, large size, STACKED across two correlated markets on the
same game, taking an UNDERDOG, entered at prices near the current market. That
combination is ranked above raw size. See signals.jsonl + resolve.py for whether
it actually works — two wins is an anecdote, not an edge.
"""
import json, os, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# ── tuning ───────────────────────────────────────────────────────────────────
POOL_SIZE      = 690      # ~0.1% of Polymarket's ~688K monthly actives
PAGE           = 50       # leaderboard API max per call
CONCURRENCY    = 6        # measured sweet spot; higher gets 429'd and runs SLOWER
RETRIES        = 5
BACKOFF        = 0.4      # seconds, exponential
HORIZON_DAYS   = 3        # games starting within this window
SIZE_OVERRIDE  = 100_000  # stake this large is its own signal, ignore hold%
MIN_STAKE      = 5_000    # otherwise need this much staked at entry...
MIN_HOLD       = 1.5      # ...AND this lifetime hold %...
MIN_VOL        = 5e6      # ...over at least this much volume (sample-size floor)

# Kch-Temp-shape thresholds
DOG_PRICE      = 0.45     # entry below this = betting an underdog
FRESH_DRIFT    = 0.10     # |entry - current| / entry; small = bet at today's price
STACK_MIN      = 2        # distinct markets on one game by one trader
CONVICTION_MIN = 20_000   # a stack this large is the headline tier

TAGS = ["nfl", "cfb", "nba", "cbb", "mlb", "tennis", "golf"]
LEDGER = "signals.jsonl"

LEADERBOARD = ("https://data-api.polymarket.com/v1/leaderboard"
               "?category=SPORTS&timePeriod=ALL&orderBy=PNL&limit={n}&offset={o}")
POSITIONS   = ("https://data-api.polymarket.com/positions"
               "?user={w}&limit=100&sortBy=CURRENT&sortDirection=DESC")
EVENTS_BASE = "https://gamma-api.polymarket.com/events?closed=false&tag_slug={t}"

# Counts every call so an unreachable network can be told apart from a genuinely
# empty result. Without this the script reports "no games" when nothing resolved.
STATS = {"ok": 0, "fail": 0, "unreachable": 0}


def get(url, retries=RETRIES):
    """GET JSON via curl, retrying through the positions API's 429 throttle."""
    for attempt in range(retries + 1):
        p = subprocess.run(
            ["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}", url],
            capture_output=True, text=True)
        body, _, code = p.stdout.rpartition("\n")
        if code == "200":
            STATS["ok"] += 1
            try:
                return json.loads(body)
            except ValueError:
                return None
        if code == "429" and attempt < retries:
            time.sleep(BACKOFF * (2 ** attempt))
            continue
        if attempt >= retries:
            STATS["fail"] += 1
            # curl reports 000 when it could not connect at all (DNS, proxy, firewall)
            if code == "000":
                STATS["unreachable"] += 1
            return None
        time.sleep(BACKOFF)
    STATS["fail"] += 1
    return None


def get_all(base_url, page=100, max_pages=20):
    """Page through a gamma-api list endpoint.

    The API silently caps `limit` at 100 — asking for 300 returns 100 with no
    error and no indication more exist. Without paging, the slate was quietly
    truncated to the 100 highest-volume events per tag, which dropped real
    fixtures (the 2026-09-12 US Open WTA final sat at offset 100).
    """
    out = []
    for i in range(max_pages):
        chunk = get(f"{base_url}&limit={page}&offset={i * page}")
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page:
            break
    return out


def league_of(tag, slug):
    if tag != "tennis":
        return {"cfb": "NCAAF", "cbb": "NCAAB", "golf": "PGA"}.get(tag, tag.upper())
    if slug.startswith("wta-"):
        return "WTA"
    if slug.startswith("atp-"):
        return "ATP"
    return "TENNIS"


def short(name, wallet):
    """Wallets with no display name report a raw '0xabc…-<timestamp>' string."""
    if not name or name.lower().startswith("0x"):
        return (wallet or name)[:10] + "…"
    return name if len(name) <= 22 else name[:21] + "…"


def build_slate():
    """Every dated game starting between now and HORIZON_DAYS out."""
    slate, now = {}, datetime.now(timezone.utc)
    for tag in TAGS:
        for e in get_all(EVENTS_BASE.format(t=tag)) or []:
            raw = e.get("startTime")
            if not raw:
                continue
            try:
                st = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            days = (st - now).total_seconds() / 86400
            if not (-0.3 <= days <= HORIZON_DAYS):
                continue
            slate.setdefault(e["slug"], {
                "league": league_of(tag, e["slug"]),
                "title": e.get("title") or e["slug"],
                "start": st,
                "slug": e["slug"],
            })
    return slate


def sweep(slate):
    """Check every wallet in the pool against the slate."""
    pool = []
    for off in range(0, POOL_SIZE, PAGE):
        pool.extend(get(LEADERBOARD.format(n=PAGE, o=off)) or [])
    pool = pool[:POOL_SIZE]

    slugs = list(slate)
    hits, unresolved = [], []

    def check(trader):
        rows = get(POSITIONS.format(w=trader["proxyWallet"]))
        if not isinstance(rows, list):
            unresolved.append(trader["proxyWallet"])
            return
        for p in rows:
            if (p.get("currentValue") or 0) <= 0:
                continue
            stake = p.get("initialValue") or 0
            if stake <= 1000:
                continue
            es = p.get("eventSlug") or ""
            base = next((s for s in slugs if es == s or es.startswith(s + "-")), None)
            if not base:
                continue
            vol = trader.get("vol") or 0
            avg = p.get("avgPrice") or 0
            cur = p.get("curPrice") or 0
            hits.append({
                "league": slate[base]["league"],
                "game": slate[base]["title"],
                "slug": base,
                "start": slate[base]["start"],
                "user": short(trader.get("userName"), trader.get("proxyWallet")),
                "wallet": trader.get("proxyWallet"),
                "rank": int(trader["rank"]),
                "hold": (trader["pnl"] / vol * 100) if vol else 0.0,
                "vol": vol,
                "side": p.get("outcome"),
                "market": p.get("title"),
                "stake": stake,
                "live": p.get("currentValue"),
                "entry": avg,
                "now": cur,
                # betting a side the market prices as the underdog
                "dog": 0 < avg < DOG_PRICE,
                # entered near today's price => a deliberate current bet, not a
                # stale position already deep in profit from an old line
                "fresh": avg > 0 and abs(cur - avg) / avg <= FRESH_DRIFT,
            })

    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(check, pool))
    return hits, unresolved, len(pool)


def add_stacks(hits):
    """Tag positions that are part of a one-trader, one-game, multi-market stack.

    This is the Kch-Temp shape: spread + moneyline on the same game. Sides are
    reported verbatim rather than auto-aligned — Polymarket mixes full names and
    abbreviations across market types ("Bills" on the moneyline, "BUF" on a
    spread), so claiming they match would be guessing.
    """
    groups = defaultdict(list)
    for h in hits:
        groups[(h["wallet"], h["slug"])].append(h)
    for members in groups.values():
        markets = {m["market"] for m in members}
        total = sum(m["stake"] for m in members)
        # "Underdog" is a property of the whole stack, not the individual leg.
        # Kch-Temp's 49ers play was a moneyline dog at 0.35 plus a spread at
        # 0.49; judged leg-by-leg the spread (80% of the money) would not count
        # as a dog bet at all, which misreads a single decision as two.
        any_dog = any(m["dog"] for m in members)
        for m in members:
            m["stack"] = len(markets)
            m["stack_stake"] = total
            m["stack_dog"] = any_dog
            m["stack_sides"] = sorted({x["side"] for x in members})


def qualify(h):
    """Tier this position, or None to drop it."""
    stacked = h.get("stack", 1) >= STACK_MIN
    quality = h["hold"] >= MIN_HOLD and h["vol"] >= MIN_VOL
    # The prioritised shape: quality trader, real money, stacked, on a dog.
    if stacked and quality and h.get("stack_stake", 0) >= CONVICTION_MIN \
            and h.get("stack_dog", h["dog"]):
        return "CONVICTION"
    if h["stake"] >= SIZE_OVERRIDE:
        return "SIZE"
    if h["stake"] >= MIN_STAKE and quality:
        return "EDGE"
    return None


def score(h):
    """Rank within the digest. Conviction traits beat raw size."""
    s = h["stake"]
    if h.get("stack", 1) >= STACK_MIN:
        s *= 2.5
    if h.get("stack_dog", h["dog"]):
        s *= 1.8
    if h["fresh"]:
        s *= 1.3
    s *= 1 + max(h["hold"], 0) / 5.0
    return s


def money(n):
    return f"${n:,.0f}"


def main():
    slate = build_slate()
    if not slate:
        # An empty slate is only meaningful if the network actually answered.
        if STATS["ok"] == 0:
            how = "unreachable (curl could not connect — DNS, proxy or firewall)" \
                  if STATS["unreachable"] else "failing"
            print(f"NETWORK FAILURE: {STATS['fail']} calls {how}, 0 succeeded.")
            print("This is NOT 'no games today' — no data was retrieved at all.")
            return 2
        print("No dated sports games found in the horizon window.")
        return 0

    hits, unresolved, pool_n = sweep(slate)
    add_stacks(hits)

    flagged = []
    for h in hits:
        t = qualify(h)
        if t:
            h["why"] = t
            h["score"] = score(h)
            flagged.append(h)
    flagged.sort(key=lambda h: -h["score"])

    now = datetime.now(timezone.utc)
    print(f"SHARP MONEY SCAN · {now:%Y-%m-%d %H:%M} UTC")
    print(f"{len(slate)} games in next {HORIZON_DAYS}d · "
          f"{pool_n - len(unresolved)}/{pool_n} wallets checked")
    if unresolved:
        print(f"!! {len(unresolved)} wallet lookups failed — digest is PARTIAL")
    n_conv = sum(1 for h in flagged if h["why"] == "CONVICTION")
    print(f"{len(flagged)} flagged from {len(hits)} raw positions over $1,000"
          f" · {n_conv} conviction-shape\n")

    if not flagged:
        print("Nothing cleared the bar today.")

    for h in flagged:
        traits = []
        if h.get("stack", 1) >= STACK_MIN:
            traits.append(f"STACK x{h['stack']} ({money(h['stack_stake'])} on this game)")
        if h["dog"]:
            traits.append(f"DOG @ {h['entry']:.2f}")
        elif h.get("stack_dog"):
            traits.append("stack includes a dog leg")
        if h["fresh"]:
            traits.append("fresh entry")
        print(f"[{h['why']}] {money(h['stake'])} on {h['side']}  ·  {h['league']}")
        print(f"    {h['market']}")
        print(f"    {h['user']} · #{h['rank']} all-time · {h['hold']:.1f}% hold "
              f"on {money(h['vol']/1e6)}M volume")
        print(f"    entry {h['entry']:.3f} → now {h['now']:.3f} · "
              f"worth {money(h['live'])} · starts {h['start']:%a %H:%M} UTC")
        if traits:
            print(f"    >> {' | '.join(traits)}")
        if h.get("stack", 1) >= STACK_MIN:
            print(f"    >> sides held: {', '.join(h['stack_sides'])}")
        print()

    out = []
    for h in flagged:
        r = dict(h)
        r["start"] = h["start"].isoformat()
        out.append(r)
    with open("scan-latest.json", "w") as f:
        json.dump({"generated": now.isoformat(), "games": len(slate), "pool": pool_n,
                   "unresolved": len(unresolved), "flagged": out}, f, indent=1)

    # Append to the ledger so resolve.py can score these against real outcomes
    # later. Without this, "the strategy works" stays an anecdote.
    with open(LEDGER, "a") as f:
        for r in out:
            f.write(json.dumps({
                "logged": now.isoformat(), "slug": r["slug"], "game": r["game"],
                "league": r["league"], "market": r["market"], "side": r["side"],
                "why": r["why"], "stake": r["stake"], "entry": r["entry"],
                "user": r["user"], "rank": r["rank"], "hold": r["hold"],
                "stack": r.get("stack", 1), "dog": r["dog"], "fresh": r["fresh"],
                "start": r["start"],
            }) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
