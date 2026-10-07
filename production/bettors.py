#!/usr/bin/env python3
"""
Bettor-centric view of today's action.

    python3 bettors.py cfb atp wta
    python3 bettors.py nfl --hours 48

Lists each top-0.1% trader positioned on today's slate, then their wagers
grouped by game, with entry prices converted from implied % to American odds.

NOTE ON WIN/LOSS: Polymarket's public API cannot give an honest W/L record.
`positions?closed=true` is survivorship-biased — winning positions are redeemed
and drop out of the list while losers sit at zero value, so Kch-Temp (a trader
up $1.2M lifetime) reports 0W/96L. Hold rate and lifetime P/L are shown instead;
both come straight from the leaderboard and are trustworthy.
"""
import json, math, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

POOL_SIZE, PAGE, CONCURRENCY = 690, 50, 6
RETRIES, BACKOFF = 5, 0.4
MIN_STAKE_SHOWN = 1_000
TAG_FOR = {"cfb": "cfb", "ncaaf": "cfb", "nfl": "nfl", "mlb": "mlb", "nba": "nba",
           "ncaab": "cbb", "cbb": "cbb", "pga": "golf", "golf": "golf",
           "atp": "tennis", "wta": "tennis", "tennis": "tennis"}
STATS = {"ok": 0, "fail": 0}


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
            return None
        time.sleep(BACKOFF)
    return None


def get_all(base_url, page=100, max_pages=20):
    """The API silently caps `limit` at 100, so page explicitly."""
    out = []
    for i in range(max_pages):
        chunk = get(f"{base_url}&limit={page}&offset={i * page}")
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page:
            break
    return out


def american(p):
    """Implied probability -> American odds."""
    if not p or p <= 0 or p >= 1:
        return "—"
    if p >= 0.5:
        return f"-{round(100 * p / (1 - p)):,}"
    return f"+{round(100 * (1 - p) / p):,}"


def short(n, w):
    if not n or n.lower().startswith("0x"):
        return (w or n)[:10] + "…"
    return n if len(n) <= 22 else n[:21] + "…"


def money(n):
    return f"${n:,.0f}"


def main():
    args = [a.lower() for a in sys.argv[1:] if not a.startswith("--")]
    hours = 24
    if "--hours" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--hours") + 1])
    leagues = args or ["cfb", "atp", "wta"]
    want = {"NCAAF" if w in ("CFB", "NCAAF") else "NCAAB" if w in ("CBB", "NCAAB")
            else "PGA" if w == "GOLF" else w for w in (l.upper() for l in leagues)}

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
            if not (-4 <= (st - now).total_seconds() / 3600 <= hours):
                continue
            slug = e["slug"]
            if tag == "tennis":
                lg = ("WTA" if slug.startswith("wta-")
                      else "ATP" if slug.startswith("atp-") else "TENNIS")
            else:
                lg = {"cfb": "NCAAF", "cbb": "NCAAB", "golf": "PGA"}.get(tag, tag.upper())
            if lg not in want:
                continue
            slate.setdefault(slug, {"league": lg, "title": e.get("title") or slug, "start": st})

    if not slate:
        print(f"No {'/'.join(sorted(want))} games in the next {hours:g}h.")
        return 0

    pool = []
    for off in range(0, POOL_SIZE, PAGE):
        pool.extend(get("https://data-api.polymarket.com/v1/leaderboard"
                        f"?category=SPORTS&timePeriod=ALL&orderBy=PNL&limit={PAGE}&offset={off}") or [])
    pool = pool[:POOL_SIZE]
    slugs = list(slate)
    rows, bad = [], []

    def check(tr):
        res = get("https://data-api.polymarket.com/positions"
                  f"?user={tr['proxyWallet']}&limit=100&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(res, list):
            bad.append(1)
            return
        for p in res:
            if (p.get("currentValue") or 0) <= 0:
                continue
            stake = p.get("initialValue") or 0
            if stake < MIN_STAKE_SHOWN:
                continue
            es = p.get("eventSlug") or ""
            base = next((s for s in slugs if es == s or es.startswith(s + "-")), None)
            if not base:
                continue
            vol = tr.get("vol") or 0
            rows.append({
                "wallet": tr["proxyWallet"], "user": short(tr.get("userName"), tr["proxyWallet"]),
                "rank": int(tr["rank"]), "pnl": tr.get("pnl") or 0, "vol": vol,
                "hold": (tr["pnl"] / vol * 100) if vol else 0.0,
                "slug": base, "league": slate[base]["league"], "game": slate[base]["title"],
                "start": slate[base]["start"], "market": p.get("title"), "side": p.get("outcome"),
                "stake": stake, "live": p.get("currentValue"),
                "entry": p.get("avgPrice") or 0, "now": p.get("curPrice") or 0,
            })

    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(check, pool))

    print(f"BETTORS ON TODAY'S SLATE · {'/'.join(sorted(want))} · {now.astimezone():%a %-I:%M %p %Z}")
    print(f"{len(slate)} games · {len(pool)-len(bad)}/{len(pool)} wallets checked · "
          f"{len(rows)} positions over {money(MIN_STAKE_SHOWN)}")
    print("W/L omitted: the public API cannot give an honest one "
          "(closed positions are survivorship-biased toward losses).\n")

    by_user = defaultdict(list)
    for r in rows:
        by_user[r["wallet"]].append(r)
    order = sorted(by_user.values(), key=lambda rs: -sum(x["stake"] for x in rs))

    for rs in order:
        t = rs[0]
        total = sum(x["stake"] for x in rs)
        print("=" * 70)
        print(f"{t['user']}   #{t['rank']} all-time sports PnL")
        print(f"  hold {t['hold']:.2f}%  ·  lifetime P/L {money(t['pnl'])}  ·  "
              f"lifetime volume {money(t['vol'])}")
        print(f"  today: {money(total)} across {len(rs)} position(s), "
              f"{len({x['slug'] for x in rs})} game(s)")
        games = defaultdict(list)
        for r in rs:
            games[r["slug"]].append(r)
        for slug, g in sorted(games.items(), key=lambda kv: -sum(x["stake"] for x in kv[1])):
            h = g[0]
            mins = (h["start"] - now).total_seconds() / 60
            when = "UNDERWAY" if mins <= 0 else f"in {int(mins//60)}h{int(mins%60):02d}m"
            print(f"\n  {h['league']} · {h['game']}  ({when})")
            for r in sorted(g, key=lambda x: -x["stake"]):
                print(f"     {money(r['stake']):>10} on {r['side'][:24]:26} "
                      f"{american(r['entry']):>7} (entry {r['entry']*100:.1f}%)"
                      f"   now {american(r['now']):>7} ({r['now']*100:.1f}%)")
                print(f"                {r['market'][:58]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
