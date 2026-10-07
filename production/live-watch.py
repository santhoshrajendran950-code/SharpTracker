#!/usr/bin/env python3
"""
Live pre-game watch on a fixed set of events.

Reports two things per event:
  SHARP ACTION  — open positions held by the top ~690 all-time sports-PnL
                  traders, tiered with the same CONVICTION / SIZE / EDGE model
                  as scan.py (see sharp-money-conviction-pattern notes).
  HIGH VOLUME   — the most-traded markets, plus how prices and volume have moved
                  since the previous run in this series.

Writes live-snapshots.json so each run can diff against the last one.
"""
import json, os, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

WATCH = {
    "atp-tiafoe-shelton-2026-09-11": "US Open ATP · Tiafoe vs Shelton",
    "cfb-missr-kan-2026-09-11":      "NCAAF · Missouri vs Kansas",
}

POOL_SIZE, PAGE, CONCURRENCY = 690, 50, 6
RETRIES, BACKOFF = 5, 0.4
MIN_STAKE_SHOWN = 1_000     # focused watch, so show everything meaningful
TOP_MARKETS     = 6         # "high volume plays" per event
DETAIL_MAX      = 4         # positions listed per event below the summary
MOVE_MIN        = 0.02      # price move worth calling out (2 cents)

# bars for the summarized "sharpest play" shortlist: a trader has to clear all
# three — track record, absolute scale, and real size on THIS play
SHORTLIST_HOLD  = 1.5       # % lifetime hold
SHORTLIST_PNL   = 250_000   # $ lifetime profit
SHORTLIST_STAKE = 2_500     # $ staked on this position

# tiering, same model as scan.py
SIZE_OVERRIDE, MIN_STAKE, MIN_HOLD, MIN_VOL = 100_000, 5_000, 1.5, 5e6
DOG_PRICE, FRESH_DRIFT, STACK_MIN, CONVICTION_MIN = 0.45, 0.10, 2, 20_000

SNAP = "live-snapshots.json"
STATS = {"ok": 0, "fail": 0, "unreachable": 0}


def get(url, retries=RETRIES):
    for attempt in range(retries + 1):
        p = subprocess.run(["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}", url],
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
            if code == "000":
                STATS["unreachable"] += 1
            return None
        time.sleep(BACKOFF)
    STATS["fail"] += 1
    return None


def plist(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def short(name, wallet):
    if not name or name.lower().startswith("0x"):
        return (wallet or name)[:10] + "…"
    return name if len(name) <= 22 else name[:21] + "…"


def money(n):
    return f"${n:,.0f}"


def fetch_markets():
    """Current price + volume for every market in each watched event."""
    out = {}
    for slug in WATCH:
        data = get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        ev = data[0] if isinstance(data, list) and data else None
        if not ev:
            out[slug] = None
            continue
        mk = []
        for m in ev.get("markets", []):
            names = plist(m.get("outcomes"))
            prices = [float(x) for x in plist(m.get("outcomePrices")) or []]
            if len(names) != len(prices) or not prices:
                continue
            mk.append({
                "q": m.get("question") or "",
                "type": m.get("sportsMarketType"),
                "line": m.get("line"),
                "vol": float(m.get("volume") or 0),
                "vol24": float(m.get("volume24hr") or 0),
                "px": dict(zip(names, prices)),
            })
        mk.sort(key=lambda x: -x["vol"])
        out[slug] = {"title": ev.get("title"), "closed": ev.get("closed"),
                     "start": ev.get("startTime"), "markets": mk}
    return out


def fetch_sharp():
    """Positions on the watched events held by the top-PnL pool."""
    pool = []
    for off in range(0, POOL_SIZE, PAGE):
        pool.extend(get("https://data-api.polymarket.com/v1/leaderboard"
                        f"?category=SPORTS&timePeriod=ALL&orderBy=PNL&limit={PAGE}&offset={off}") or [])
    pool = pool[:POOL_SIZE]
    slugs = list(WATCH)
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
                "slug": base, "user": short(tr.get("userName"), tr.get("proxyWallet")),
                "wallet": tr.get("proxyWallet"), "rank": int(tr["rank"]),
                "hold": (tr["pnl"] / vol * 100) if vol else 0.0, "vol": vol,
                "pnl": tr.get("pnl") or 0,
                "side": p.get("outcome"), "market": p.get("title"),
                "stake": stake, "live": p.get("currentValue"),
                "entry": avg, "now": cur,
                "dog": 0 < avg < DOG_PRICE,
                "fresh": avg > 0 and abs(cur - avg) / avg <= FRESH_DRIFT,
            })

    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(check, pool))

    groups = defaultdict(list)
    for h in hits:
        groups[(h["wallet"], h["slug"])].append(h)
    for members in groups.values():
        any_dog = any(m["dog"] for m in members)
        total = sum(m["stake"] for m in members)
        for m in members:
            m["stack"] = len({x["market"] for x in members})
            m["stack_stake"] = total
            m["stack_dog"] = any_dog
            m["stack_sides"] = sorted({x["side"] for x in members})
    return hits, len(bad), len(pool)


def tier(h):
    quality = h["hold"] >= MIN_HOLD and h["vol"] >= MIN_VOL
    if (h.get("stack", 1) >= STACK_MIN and quality
            and h.get("stack_stake", 0) >= CONVICTION_MIN and h.get("stack_dog")):
        return "CONVICTION"
    if h["stake"] >= SIZE_OVERRIDE:
        return "SIZE"
    if h["stake"] >= MIN_STAKE and quality:
        return "EDGE"
    return "watch"


import math


def sub_scores(h):
    """Three 0-1 sub-scores: track record, scale, skin in the game.

    hold  — lifetime ROI; 5% is full marks (most sharps sit under 5%)
    pnl   — lifetime profit; $100K scores 0, $10M scores 1
    stake — wagered on THIS play; $1K scores 0, $200K scores 1
    """
    hold = max(0.0, min(h["hold"] / 5.0, 1.0))
    pnl = max(0.0, min(math.log10(max(h["pnl"], 1) / 1e5) / 2.0, 1.0)) if h["pnl"] > 0 else 0.0
    stake = max(0.0, min(math.log10(max(h["stake"], 1) / 1e3) / math.log10(200), 1.0))
    return hold, pnl, stake


def conviction(h):
    """0-100. Geometric-style blend so a zero on any axis can't be bought off
    by the other two — a huge stake from an unprofitable wallet stays low."""
    hold, pnl, stake = sub_scores(h)
    base = ((hold + 0.05) * (pnl + 0.05) * (stake + 0.05)) ** (1 / 3.0)
    mult = 1.0
    if h.get("stack", 1) >= STACK_MIN:
        mult *= 1.35
    if h.get("stack_dog", h["dog"]):
        mult *= 1.20
    if h["fresh"]:
        mult *= 1.10
    return min(base * mult * 100, 100)


def rank(h):
    s = h["stake"]
    if h.get("stack", 1) >= STACK_MIN:
        s *= 2.5
    if h.get("stack_dog", h["dog"]):
        s *= 1.8
    if h["fresh"]:
        s *= 1.3
    return s * (1 + max(h["hold"], 0) / 5.0)


def main():
    now = datetime.now(timezone.utc)
    mkts = fetch_markets()
    if all(v is None for v in mkts.values()):
        if STATS["ok"] == 0:
            print(f"NETWORK FAILURE: {STATS['fail']} calls failed, 0 succeeded.")
            print("This is NOT 'no action' — no data was retrieved at all.")
            return 2
        print("Could not load either watched event.")
        return 2

    hits, bad, pool_n = fetch_sharp()
    for h in hits:
        h["tier"] = tier(h)

    prev = None
    if os.path.exists(SNAP):
        try:
            hist = json.load(open(SNAP))
            prev = hist[-1] if hist else None
        except Exception:
            hist = []
    else:
        hist = []

    for h in hits:
        h["conv"] = conviction(h)

    lines = []
    lines.append(f"LIVE WATCH · {now.astimezone():%-I:%M %p %Z} ({now:%H:%M} UTC)")
    lines.append(f"{pool_n - bad}/{pool_n} sharp wallets checked"
                 + (f"  !! {bad} lookups failed — PARTIAL" if bad else ""))
    if prev:
        lines.append(f"comparing against the {prev['label']} run")
    lines.append("")

    # ── the summary: sharpest play per event ─────────────────────────
    lines.append("#" * 62)
    lines.append("SHARPEST PLAY PER GAME")
    lines.append("ranked on track record (hold %), scale (lifetime P/L), and")
    lines.append("size wagered on this specific play. Score is 0-100.")
    lines.append("#" * 62)
    for slug, label in WATCH.items():
        mine = sorted([h for h in hits if h["slug"] == slug],
                      key=lambda h: -h["conv"])
        good = [h for h in mine
                if h["hold"] >= SHORTLIST_HOLD and h["pnl"] >= SHORTLIST_PNL
                and h["stake"] >= SHORTLIST_STAKE]
        lines.append("")
        lines.append(f"  {label}")
        if not good:
            best = mine[0] if mine else None
            lines.append(f"    NO QUALIFYING PLAY — nobody clears "
                         f"{SHORTLIST_HOLD}% hold + {money(SHORTLIST_PNL)} lifetime "
                         f"+ {money(SHORTLIST_STAKE)} staked.")
            if best:
                lines.append(f"    (closest: {best['user']} {money(best['stake'])} on "
                             f"{best['side']}, {best['hold']:.1f}% hold, "
                             f"{money(best['pnl'])} lifetime — score {best['conv']:.0f})")
            continue
        biggest = max(mine, key=lambda x: x["stake"]) if mine else None
        for h in good[:3]:
            hs, ps, ss = sub_scores(h)
            tags = []
            if h.get("stack", 1) >= STACK_MIN:
                tags.append(f"stacked x{h['stack']}")
            if h.get("stack_dog", h["dog"]):
                tags.append("underdog")
            if h["fresh"]:
                tags.append("fresh")
            lines.append(f"    [{h['conv']:5.1f}] {money(h['stake']):>9} on {h['side']}"
                         f"  ({h['market'][:40]})")
            lines.append(f"            {h['user']} · #{h['rank']} · {h['hold']:.1f}% hold"
                         f" · {money(h['pnl'])} lifetime P/L"
                         f" · entry {h['entry']:.3f} → {h['now']:.3f}")
            lines.append(f"            record {hs*100:.0f}/100 · scale {ps*100:.0f}/100"
                         f" · size {ss*100:.0f}/100"
                         + (f"  [{', '.join(tags)}]" if tags else ""))
        # The biggest bet on the game is worth naming even when it misses a bar,
        # otherwise the summary quietly buries the largest money on the board.
        if biggest and biggest not in good[:3]:
            miss = []
            if biggest["hold"] < SHORTLIST_HOLD:
                miss.append(f"hold {biggest['hold']:.1f}% < {SHORTLIST_HOLD}%")
            if biggest["pnl"] < SHORTLIST_PNL:
                miss.append(f"lifetime {money(biggest['pnl'])} < {money(SHORTLIST_PNL)}")
            if biggest["stake"] < SHORTLIST_STAKE:
                miss.append(f"stake {money(biggest['stake'])} < {money(SHORTLIST_STAKE)}")
            why = "; ".join(miss) if miss else "outranked on score"
            lines.append(f"    BIGGEST BET (excluded): {money(biggest['stake'])} on "
                         f"{biggest['side']} by {biggest['user']} — {why}")
    lines.append("")

    for slug, label in WATCH.items():
        info = mkts.get(slug)
        lines.append("=" * 62)
        lines.append(label)
        if not info:
            lines.append("  (event could not be loaded this run)")
            lines.append("")
            continue
        if info.get("start"):
            st = datetime.fromisoformat(info["start"].replace("Z", "+00:00"))
            mins = (st - now).total_seconds() / 60
            when = f"{int(mins)} min to start" if 0 < mins < 600 else (
                "UNDERWAY" if mins <= 0 else st.astimezone().strftime("%-I:%M %p %Z"))
            lines.append(f"  starts {st.astimezone():%-I:%M %p %Z} · {when}")
        lines.append("")

        # ── sharp action ─────────────────────────────────────────────
        mine = sorted([h for h in hits if h["slug"] == slug], key=lambda h: -rank(h))
        real = [h for h in mine if h["tier"] != "watch"]
        lines.append(f"  SHARP ACTION — {len(mine)} position(s) over {money(MIN_STAKE_SHOWN)}"
                     f" ({len(real)} clearing the tier bar)")
        if not mine:
            lines.append("    none — no top-0.1% wallet is positioned here.")
        elif len(mine) > DETAIL_MAX:
            lines.append(f"    (showing top {DETAIL_MAX} of {len(mine)} by rank)")
        for h in mine[:DETAIL_MAX]:
            traits = []
            if h.get("stack", 1) >= STACK_MIN:
                traits.append(f"STACK x{h['stack']} ({money(h['stack_stake'])}: "
                              f"{', '.join(h['stack_sides'])})")
            if h["dog"]:
                traits.append(f"DOG @ {h['entry']:.2f}")
            elif h.get("stack_dog"):
                traits.append("stack has a dog leg")
            if h["fresh"]:
                traits.append("fresh")
            lines.append(f"    [{h['tier']:10}] {money(h['stake']):>10} on {h['side']}")
            lines.append(f"                 {h['market'][:52]}")
            lines.append(f"                 {h['user']} · #{h['rank']} · {h['hold']:.1f}% hold"
                         f" · entry {h['entry']:.3f} → {h['now']:.3f}")
            if traits:
                lines.append(f"                 >> {' | '.join(traits)}")
        # ── convergence: independent wallets on the same side ────────
        by_side = defaultdict(list)
        for h in mine:
            by_side[h["side"]].append(h)
        conv = [(sd, hs) for sd, hs in by_side.items()
                if len({x["wallet"] for x in hs}) >= 2]
        if conv:
            lines.append("")
            lines.append("  CONVERGENCE — separate wallets landing on the same side")
            for sd, hs in sorted(conv, key=lambda kv: -sum(x["stake"] for x in kv[1])):
                w = len({x["wallet"] for x in hs})
                tot = sum(x["stake"] for x in hs)
                px = min(x["entry"] for x in hs)
                dog = " (underdog)" if px < DOG_PRICE else ""
                lines.append(f"    {w} wallets · {money(tot)} on {sd}{dog}")
                lines.append(f"      {', '.join(sorted({x['user'] for x in hs}))}")
        lines.append("")

        # ── high volume + movement ───────────────────────────────────
        lines.append(f"  HIGH VOLUME — top {TOP_MARKETS} markets by total traded")
        pv = {}
        if prev:
            pv = (prev.get("markets", {}).get(slug) or {})
        for m in info["markets"][:TOP_MARKETS]:
            fav = max(m["px"].items(), key=lambda kv: kv[1])
            row = (f"    {money(m['vol']):>10} vol ({money(m['vol24'])} 24h)  "
                   f"{fav[0][:22]:24} {fav[1]*100:4.1f}%   {m['q'][:38]}")
            lines.append(row)
            old = pv.get(m["q"])
            if old:
                d_px = fav[1] - old.get("px", {}).get(fav[0], fav[1])
                d_vol = m["vol"] - old.get("vol", m["vol"])
                notes = []
                if abs(d_px) >= MOVE_MIN:
                    notes.append(f"price {d_px*100:+.1f} pts on {fav[0][:18]}")
                if d_vol > 0:
                    notes.append(f"+{money(d_vol)} traded since last run")
                if notes:
                    lines.append(f"               >> MOVE: {' · '.join(notes)}")
        lines.append("")

    lines.append("Informational only — this reports what anonymous traders hold "
                 "and what the market traded. Not advice.")

    text = "\n".join(lines)
    print(text)

    hist.append({
        "at": now.isoformat(),
        "label": now.astimezone().strftime("%-I:%M %p"),
        "markets": {slug: {m["q"]: {"px": m["px"], "vol": m["vol"]}
                           for m in (mkts[slug]["markets"] if mkts.get(slug) else [])}
                    for slug in WATCH},
    })
    with open(SNAP, "w") as f:
        json.dump(hist[-12:], f, indent=1)
    with open("live-body.txt", "w") as f:
        f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
