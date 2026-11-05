#!/usr/bin/env python3
"""
Shadow log: wallets OUTSIDE the tracked pool agreeing on a side. Never published.

    python3 outside.py                  # hourly job: log games about an hour out
    python3 outside.py --dry            # same read, print only, write nothing
    python3 outside.py --settle         # grade finished plays (nightly)
    python3 outside.py --report         # the running record

THE RULE (out5): 5+ wallets outside the pool each hold at least MIN_USD on one side
of a market, and no pool wallet holds at least MIN_USD on that side. Each market and
side is logged once, at the price when it first fires, about an hour before start.

WHY: the 2026-11-04 test, with a pool chosen only from earlier results, had this as
the best cut: -0.16% [-4.2, +4.1] at any size and +1.82% [-3.5, +6.8] at $1,000+. It
was one of many cuts and +/- zero, so this is a forward test and not a finding. The
sweep reads pool wallets only, so this has to read /holders market by market.

Each row also records how many outside wallets held $2,500+ and $10,000+, because the
backtest changed sign with the size floor (-12.62% at $10,000+). The floor is judged
on the forward data and must not be picked from it after the fact without a holdout.

Holder value is shares x current price, not the amount paid (see holders.py). Reads
wait on the sweep lock and never take it. outside.jsonl is PRIVATE, same footing as
signals.jsonl, and nothing here posts, emails or touches the pool or picks.
"""
import json, os, re, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))
import btlib as B
import holders as H
import liveboard as LB

LEDGER = os.path.join(HERE, "outside.jsonl")
NEED, MIN_USD, VIG = 5, 1000.0, 0.015
LEAD_MIN, LEAD_MAX = 30, 90      # minutes before start; an hourly run sees each game once
TAGS = ["nba", "ncaa-basketball", "nfl", "cfb", "nhl", "tennis", "ucl", "epl", "la-liga",
        "soccer"]                # Serie A only reaches gamma through the generic soccer tag
BOARD = "https://data-api.polymarket.com/v2/leaderboard?category=sports&time_period={p}&sort_by=PNL&limit={n}"
GAME_SLUG = re.compile(r"^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")


def load_pool():
    """The wallets check.build_pool reads: all-time top 690, month top 500, discovered.
    None if a board cannot be read, so a partial pool can never look like outside money."""
    out = set()
    for p, n in (("all", 690), ("month", 500)):
        js = B.get(BOARD.format(p=p, n=n))
        rows = js.get("data") if isinstance(js, dict) else None
        if not rows or len(rows) < n:
            return None
        out.update(r["user_id"].lower() for r in rows if r.get("user_id"))
    try:
        out.update(w.lower() for w in json.load(open(os.path.join(HERE, "discovered.json"))))
    except (OSError, ValueError):
        return None
    return out


def games(now, lead_max=LEAD_MAX, lead_min=LEAD_MIN):
    """In-scope full-game events starting inside the window, plus any failed slices."""
    lo = (now + timedelta(minutes=lead_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (now + timedelta(minutes=lead_max)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found, holes = {}, []
    for tag in TAGS:
        for ev in H.events(H.GAMMA_KICK, tag, lo, hi, holes):
            slug = ev.get("slug") or ""
            if LB.comp_of(slug) and GAME_SLUG.match(slug):
                found[slug] = ev
    return list(found.values()), holes


def main_markets(ev):
    """holders.main_markets plus the soccer Yes/No markets it was not written for."""
    yield from H.main_markets(ev)
    for m in ev.get("markets") or []:
        q = (m.get("question") or "").strip()
        if not m.get("closed") and (LB.WILL_WIN.match(q) or LB.DRAW.search(q)):
            yield q, m


def fired(rows, pool):
    """rows: (wallet, name, side, price, usd) for one market -> {side: details}."""
    on = defaultdict(lambda: {"out": {}, "in": 0, "price": None})
    for wallet, name, side, price, usd in rows:
        if not wallet or usd < MIN_USD:
            continue
        x = on[side]
        x["price"] = price
        if wallet.lower() in pool:
            x["in"] += 1
        else:
            x["out"][wallet.lower()] = usd
    return {s: x for s, x in on.items() if len(x["out"]) >= NEED and not x["in"]}


def load():
    try:
        return [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        return []


def scan(dry=False, lead_max=LEAD_MAX, max_markets=None):
    now = datetime.now(timezone.utc)
    stamp = f"[{now.astimezone():%F %T %Z}]"
    pool = load_pool()
    if pool is None:
        print(f"{stamp} !! pool unreadable, nothing logged")
        return 1
    evs, holes = games(now, lead_max)
    jobs = [(ev, q, m) for ev in evs for q, m in main_markets(ev)]
    if max_markets:
        jobs = jobs[:max_markets]
    have = {(r["slug"], r["market"], r["side"]) for r in load()}
    new = []

    def one(j):
        ev, q, m = j
        return ev, q, H.holders_for(m)

    for ev, q, rows in B.pmap(one, jobs, concurrency=2):
        for side, x in fired(rows, pool).items():
            if (ev["slug"], q, side) in have:
                continue
            usd = list(x["out"].values())
            new.append({"rule": "out5", "fired_at": now.isoformat(timespec="seconds"),
                        "start": ev.get("startTime"), "slug": ev["slug"],
                        "league": LB.comp_of(ev["slug"])[0], "game": ev.get("title"),
                        "market": q, "side": side, "price": x["price"], "n": len(usd),
                        "n_2500": sum(u >= 2500 for u in usd), "n_10k": sum(u >= 10000 for u in usd),
                        "total_usd": round(sum(usd))})
    if not dry and new:
        with open(LEDGER, "a") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    print(f"{stamp} {len(evs)} games, {len(jobs)} markets, pool {len(pool)}: {len(new)} new out5"
          + (" (dry)" if dry else "") + f" · {B.STATS['fail']} failed reads"
          + (f" · {len(holes)} gamma slices FAILED" if holes else ""))
    if dry:
        for r in new:
            print(f"  {r['league']} {r['game']}: {r['market']} / {r['side']} at {r['price']:.2f}, n={r['n']}")
    return 0


def result_of(ev, market, side):
    """'won' / 'lost' from the settled market itself, or None if not settled yet."""
    for m in ev.get("markets") or []:
        if (m.get("question") or "").strip() != market:
            continue
        names, prices = H.plist(m.get("outcomes")), H.plist(m.get("outcomePrices"))
        if side not in names or len(prices) != len(names):
            return None
        p = float(prices[names.index(side)])
        return "won" if p >= 0.99 else "lost" if p <= 0.01 else None
    return None


def settle():
    rows = load()
    now = datetime.now(timezone.utc)
    todo = [r for r in rows if not r.get("result") and r.get("start")
            and datetime.fromisoformat(r["start"].replace("Z", "+00:00")) < now - timedelta(hours=3)]
    done = 0
    cache = {}
    for r in todo:
        if r["slug"] not in cache:
            got = B.get(f"https://gamma-api.polymarket.com/events?slug={r['slug']}") or []
            cache[r["slug"]] = got[0] if got else {}
        res = result_of(cache[r["slug"]], r["market"], r["side"])
        if res:
            r["result"] = res
            done += 1
    if done:
        tmp = LEDGER + ".tmp"
        with open(tmp, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, LEDGER)
    print(f"outside: {done} graded, {len(todo) - done} unresolved")


def flat(r):
    return (1 / (r["price"] + VIG) - 1) if r["result"] == "won" else -1.0


def report():
    rows = load()
    print(f"{'cut':14} {'fired':>6} {'graded':>7} {'games':>6} {'W-L':>9} {'flat ROI':>9}  (at the logged price + {VIG:.3f})")
    for name, f in (("5+ at $1,000", lambda r: True), ("5+ at $2,500", lambda r: r["n_2500"] >= NEED),
                    ("5+ at $10,000", lambda r: r["n_10k"] >= NEED)):
        rs = [r for r in rows if f(r)]
        g = [r for r in rs if r.get("result") in ("won", "lost") and 0 < (r.get("price") or 0) < 0.985]
        w = sum(r["result"] == "won" for r in g)
        roi = f"{sum(map(flat, g)) / len(g) * 100:+.1f}%" if g else "—"
        print(f"{name:14} {len(rs):>6} {len(g):>7} {len({r['slug'] for r in g}):>6} {w:>4}-{len(g) - w:<4} {roi:>9}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--settle" in a:
        settle()
    if "--report" in a:
        report()
    if not ({"--settle", "--report"} & set(a)):
        sys.exit(scan("--dry" in a,
                      int(a[a.index("--lead-max") + 1]) if "--lead-max" in a else LEAD_MAX,
                      int(a[a.index("--max-markets") + 1]) if "--max-markets" in a else None))
