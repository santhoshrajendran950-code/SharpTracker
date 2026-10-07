#!/usr/bin/env python3
"""
Ad-hoc: sharpest plays on today's games for chosen leagues.

    python3 today.py cfb atp wta
    python3 today.py nfl --hours 48

Same conviction model as live-watch.py — ranks on track record (hold %),
scale (lifetime P/L) and size wagered on the specific play — but builds its
slate from whatever is on today rather than a fixed watchlist.
"""
import json, math, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

POOL_SIZE, PAGE, CONCURRENCY = 690, 50, 6
RETRIES, BACKOFF = 5, 0.4
MIN_STAKE_SHOWN = 1_000
SHORTLIST_HOLD, SHORTLIST_PNL, SHORTLIST_STAKE = 1.5, 250_000, 2_500
DOG_PRICE, FRESH_DRIFT, STACK_MIN = 0.45, 0.10, 2
DECIDED_PX = 0.97    # market effectively settled; not a live play
CHALK_PX   = 0.90    # entry this short is yield-scraping, not a directional read
MAX_GAMES_SHOWN = 12

TAG_FOR = {"cfb": "cfb", "ncaaf": "cfb", "nfl": "nfl", "mlb": "mlb",
           "nba": "nba", "ncaab": "cbb", "cbb": "cbb", "pga": "golf",
           "golf": "golf", "atp": "tennis", "wta": "tennis", "tennis": "tennis"}
STATS = {"ok": 0, "fail": 0, "unreachable": 0}


def get(url, retries=RETRIES):
    for a in range(retries + 1):
        p = subprocess.run(["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}", url],
                           capture_output=True, text=True)
        body, _, code = p.stdout.rpartition("\n")
        if code == "200":
            STATS["ok"] += 1
            try:
                return json.loads(body)
            except ValueError:
                return None
        if code == "429" and a < retries:
            time.sleep(BACKOFF * (2 ** a))
            continue
        if a >= retries:
            STATS["fail"] += 1
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


def short(n, w):
    if not n or n.lower().startswith("0x"):
        return (w or n)[:10] + "…"
    return n if len(n) <= 22 else n[:21] + "…"


def money(n):
    return f"${n:,.0f}"


def sub_scores(h):
    hold = max(0.0, min(h["hold"] / 5.0, 1.0))
    pnl = max(0.0, min(math.log10(max(h["pnl"], 1) / 1e5) / 2.0, 1.0)) if h["pnl"] > 0 else 0.0
    stake = max(0.0, min(math.log10(max(h["stake"], 1) / 1e3) / math.log10(200), 1.0))
    return hold, pnl, stake


def conviction(h):
    hold, pnl, stake = sub_scores(h)
    base = ((hold + 0.05) * (pnl + 0.05) * (stake + 0.05)) ** (1 / 3.0)
    m = 1.0
    if h.get("stack", 1) >= STACK_MIN:
        m *= 1.35
    if h.get("stack_dog", h["dog"]):
        m *= 1.20
    if h["fresh"]:
        m *= 1.10
    return min(base * m * 100, 100)


def main():
    args = [a.lower() for a in sys.argv[1:] if not a.startswith("--")]
    hours = 24
    if "--hours" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--hours") + 1])
    leagues = args or ["cfb", "atp", "wta"]
    want = set(l.upper().replace("NCAAF", "NCAAF") for l in leagues)
    want = {"NCAAF" if w in ("CFB", "NCAAF") else "NCAAB" if w in ("CBB", "NCAAB")
            else "PGA" if w == "GOLF" else w for w in want}

    now = datetime.now(timezone.utc)
    slate = {}
    for tag in {TAG_FOR.get(l, l) for l in leagues}:
        for e in get_all("https://gamma-api.polymarket.com/events?closed=false"
                         f"&tag_slug={tag}") or []:
            raw = e.get("startTime")
            if not raw:
                continue
            try:
                st = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            hrs = (st - now).total_seconds() / 3600
            if not (-4 <= hrs <= hours):
                continue
            slug = e["slug"]
            if tag == "tennis":
                lg = "WTA" if slug.startswith("wta-") else "ATP" if slug.startswith("atp-") else "TENNIS"
            else:
                lg = {"cfb": "NCAAF", "cbb": "NCAAB", "golf": "PGA"}.get(tag, tag.upper())
            if lg not in want:
                continue
            slate.setdefault(slug, {"league": lg, "title": e.get("title") or slug, "start": st})

    if not slate:
        if STATS["ok"] == 0:
            print("NETWORK FAILURE — no data retrieved. Not 'no games'.")
            return 2
        print(f"No {'/'.join(sorted(want))} games found starting in the next {hours:g}h.")
        return 0

    pool = []
    for off in range(0, POOL_SIZE, PAGE):
        pool.extend(get("https://data-api.polymarket.com/v1/leaderboard"
                        f"?category=SPORTS&timePeriod=ALL&orderBy=PNL&limit={PAGE}&offset={off}") or [])
    pool = pool[:POOL_SIZE]
    slugs = list(slate)
    hits, bad = [], []

    def check(tr):
        rows = get("https://data-api.polymarket.com/positions"
                   f"?user={tr['proxyWallet']}&limit=100&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(rows, list):
            bad.append(1)
            return
        for p in rows:
            if (p.get("currentValue") or 0) <= 0:
                continue
            stake = p.get("initialValue") or 0
            if stake < MIN_STAKE_SHOWN:
                continue
            es = p.get("eventSlug") or ""
            base = next((s for s in slugs if es == s or es.startswith(s + "-")), None)
            if not base:
                continue
            vol, avg, cur = tr.get("vol") or 0, p.get("avgPrice") or 0, p.get("curPrice") or 0
            hits.append({
                "slug": base, "league": slate[base]["league"], "game": slate[base]["title"],
                "start": slate[base]["start"], "user": short(tr.get("userName"), tr.get("proxyWallet")),
                "wallet": tr.get("proxyWallet"), "rank": int(tr["rank"]),
                "hold": (tr["pnl"] / vol * 100) if vol else 0.0, "vol": vol,
                "pnl": tr.get("pnl") or 0, "side": p.get("outcome"), "market": p.get("title"),
                "stake": stake, "live": p.get("currentValue"), "entry": avg, "now": cur,
                "dog": 0 < avg < DOG_PRICE,
                # a market already at ~0 or ~1 has resolved — reporting it as a
                # "play" would be reporting a result
                "decided": cur >= DECIDED_PX or cur <= (1 - DECIDED_PX),
                # backing a 0.95 favourite risks a lot to win little; that is
                # not the conviction shape even from a strong wallet
                "chalk": avg >= CHALK_PX,
                "fresh": avg > 0 and abs(cur - avg) / avg <= FRESH_DRIFT,
            })

    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(check, pool))

    groups = defaultdict(list)
    for h in hits:
        groups[(h["wallet"], h["slug"])].append(h)
    for mem in groups.values():
        anyd = any(m["dog"] for m in mem)
        tot = sum(m["stake"] for m in mem)
        for m in mem:
            m["stack"] = len({x["market"] for x in mem})
            m["stack_stake"] = tot
            m["stack_dog"] = anyd
            m["stack_sides"] = sorted({x["side"] for x in mem})
    for h in hits:
        h["conv"] = conviction(h)

    print(f"TODAY'S SHARP PLAYS · {'/'.join(sorted(want))} · {now.astimezone():%a %-I:%M %p %Z}")
    print(f"{len(slate)} games in window (next {hours:g}h) · "
          f"{len(pool)-len(bad)}/{len(pool)} wallets checked"
          + (f" · !! {len(bad)} FAILED, PARTIAL" if bad else ""))

    qual = [h for h in hits if h["hold"] >= SHORTLIST_HOLD and h["pnl"] >= SHORTLIST_PNL
            and h["stake"] >= SHORTLIST_STAKE and not h["decided"] and not h["chalk"]]
    n_dec = sum(1 for h in hits if h["decided"])
    n_chalk = sum(1 for h in hits if h["chalk"] and not h["decided"])
    print(f"{len(hits)} positions over {money(MIN_STAKE_SHOWN)} · "
          f"{len(qual)} clear the bar ({SHORTLIST_HOLD}% hold + "
          f"{money(SHORTLIST_PNL)} lifetime + {money(SHORTLIST_STAKE)} staked)")
    if n_dec or n_chalk:
        print(f"excluded: {n_dec} already-settled market(s), "
              f"{n_chalk} at {CHALK_PX:.0%}+ chalk")
    print()

    by_game = defaultdict(list)
    for h in qual:
        by_game[h["slug"]].append(h)
    if not by_game:
        print("No qualifying sharp play on any of today's games.")
        big = sorted(hits, key=lambda x: -x["stake"])[:3]
        if big:
            print("\nLargest positions regardless of trader quality:")
            for h in big:
                print(f"  {money(h['stake']):>9} on {h['side']} · {h['league']} · {h['game'][:36]}")
                print(f"            {h['user']} · {h['hold']:.1f}% hold · {money(h['pnl'])} lifetime")
        return 0

    order = sorted(by_game.items(), key=lambda kv: -max(x["conv"] for x in kv[1]))
    for slug, hs in order[:MAX_GAMES_SHOWN]:
        hs.sort(key=lambda x: -x["conv"])
        g = hs[0]
        mins = (g["start"] - now).total_seconds() / 60
        when = "UNDERWAY" if mins <= 0 else f"in {int(mins//60)}h{int(mins%60):02d}m"
        print(f"{g['league']} · {g['game']}  ({when}, {g['start'].astimezone():%-I:%M %p %Z})")
        for h in hs[:2]:
            hsc, psc, ssc = sub_scores(h)
            tags = []
            if h.get("stack", 1) >= STACK_MIN:
                tags.append(f"stacked x{h['stack']} ({money(h['stack_stake'])})")
            if h.get("stack_dog", h["dog"]):
                tags.append("underdog")
            if h["fresh"]:
                tags.append("fresh")
            print(f"   [{h['conv']:5.1f}] {money(h['stake']):>9} on {h['side']}  ({h['market'][:44]})")
            print(f"           {h['user']} · #{h['rank']} · {h['hold']:.1f}% hold · "
                  f"{money(h['pnl'])} lifetime · entry {h['entry']:.3f} → {h['now']:.3f}")
            print(f"           record {hsc*100:.0f} · scale {psc*100:.0f} · size {ssc*100:.0f}"
                  + (f"  [{', '.join(tags)}]" if tags else ""))
        print()
    if len(order) > MAX_GAMES_SHOWN:
        print(f"({len(order) - MAX_GAMES_SHOWN} more games with qualifying plays not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
