#!/usr/bin/env python3
"""
Tail one trusted bettor's pregame football positions with a size ladder.

    tail.py              current recommendations (prints, logs new ones to tails.jsonl)
    tail.py --paper      also send new/upsized recs to the Novig PAPER exchange
    tail.py --quiet      print only when something is new (for the poll loop)
    tail.py --x          also post new recs >= x_min_units to X (codename, blurred stake)
    tail.py record       graded record of everything in tails.jsonl

Rules (tail.json):
  - pregame NFL / NCAAF only — every one of his football positions since 7/15
    was entered before kickoff, median 4.5h ahead, so there is time to follow
  - stake = his current position cost; < $20k ignored, $20k-100k 0.5u, $100k+ 1u
  - his line must not have run away: current price <= his avg + 3c
  - price window .40-.645 (+150 .. -180), same as the house rules
  - correlated legs in one game (ML + spread on one side) share a 1.5u cap;
    a game where he holds both sides of the same market is skipped
Backtest (his 129 football positions, tail price = T-15 Polymarket + 1.5c vig):
  >=$20k +21% [+1,+42] game-clustered; since 9/21 only +4% [-19,+28];
  $100k+ since 9/21 +30% [-1,+59] on 19 games. Small and selected-on — unproven.

tails.jsonl is PRIVATE (same footing as bets.jsonl): never synced, never sent.
"""
import json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
CFG = json.loads((HERE / "tail.json").read_text())
LEDGER = HERE / "tails.jsonl"
UA = {"User-Agent": "Mozilla/5.0"}


def get(url):
    for i in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))
        except Exception:
            time.sleep(1 + i)
    return None


def american(p):
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def prob(odds):
    return -odds / (-odds + 100) if odds < 0 else 100 / (odds + 100)


_starts = {}
def start_of(event_slug):
    if event_slug not in _starts:
        d = get(f"https://gamma-api.polymarket.com/events?slug={event_slug}") or []
        st = None
        if d:
            st = d[0].get("startTime")
            for m in d[0].get("markets", []):
                if m.get("gameStartTime"):
                    st = m["gameStartTime"]
                    break
        if st:
            st = st.replace(" ", "T").replace("Z", "+00:00")
            if st.endswith("+00"):
                st += ":00"
            st = datetime.fromisoformat(st)
        _starts[event_slug] = (st, d[0].get("title") if d else None)
    return _starts[event_slug]


def units_for(stake):
    u = 0.0
    for floor, units in CFG["tiers"]:
        if stake >= floor:
            u = units
    return u


def play_text(title, outcome):
    t = title.lower()
    if "o/u" in t:
        return f"{outcome} {title.split('O/U')[-1].strip()}"
    if title.startswith("Spread:"):
        fav, line = title[len("Spread:"):].rsplit("(", 1)
        line = float(line.rstrip(")"))
        return f"{outcome} {line:+g}" if outcome.strip() in fav else f"{outcome} {-line:+g}"
    return f"{outcome} ML"


def scan():
    now = datetime.now(timezone.utc)
    recs = []
    for who, wallet in CFG["wallets"].items():
        pos = get(f"https://data-api.polymarket.com/positions?user={wallet}&sizeThreshold=100&limit=500")
        if pos is None:                       # silence must mean "nothing new", not "couldn't look"
            sys.exit(f"positions lookup failed for {who}")
        pos = [p for p in pos if 0.01 < p["curPrice"] < 0.99
               and p["eventSlug"].split("-")[0] in CFG["leagues"]]
        by_cond = {}
        for p in pos:
            by_cond.setdefault(p["conditionId"], []).append(p)
        for p in pos:
            start, game = start_of(p["eventSlug"])
            if not start or start <= now:
                continue
            stake = p["initialValue"]
            r = {"who": who, "league": CFG["leagues"][p["eventSlug"].split("-")[0]],
                 "event": p["eventSlug"], "game": game or p["title"], "market": p["title"],
                 "side": p["outcome"], "play": play_text(p["title"], p["outcome"]),
                 "asset": p["asset"], "stake": round(stake), "entry": round(p["avgPrice"], 4),
                 "now": round(p["curPrice"], 4), "start": start.isoformat(),
                 "units": units_for(stake), "why": ""}
            if r["units"] == 0:
                r["why"] = "under $20k"
            elif len(by_cond[p["conditionId"]]) > 1:
                r["why"], r["units"] = "holds both sides", 0
            elif p["curPrice"] - p["avgPrice"] > CFG["max_drift"]:
                r["why"], r["units"] = f"line gone (+{100*(p['curPrice']-p['avgPrice']):.1f}c)", 0
            elif not CFG["price_min"] <= p["curPrice"] <= CFG["price_max"]:
                r["why"], r["units"] = "outside +150..-180", 0
            recs.append(r)
    # correlated legs share the per-game cap
    games = {}
    for r in recs:
        if r["units"]:
            games.setdefault(r["event"], []).append(r)
    for legs in games.values():
        tot = sum(r["units"] for r in legs)
        if tot > CFG["game_cap_units"]:
            for r in legs:
                r["units"] = round(r["units"] * CFG["game_cap_units"] / tot * 4) / 4
                r["why"] = f"game cap {CFG['game_cap_units']}u"
    return recs


def add_books(recs):
    try:
        import books
        from oddskey import get_key
        key = get_key()
    except Exception as e:
        print(f"  (book prices unavailable: {e})", file=sys.stderr)
        return
    for r in recs:
        if not r["units"]:
            continue
        h = {"league": r["league"], "game": r["game"], "side": r["side"], "market": r["market"],
             "start": datetime.fromisoformat(r["start"]), "slug": r["event"]}
        try:
            px = [(b, o) for b, o, *_ in books.prices_for(h, key) if b not in books.REFS_LABELS]
        except Exception:
            px = []
        r["best"] = f"{px[0][1]:+d} {px[0][0]}" if px else "—"
        r["best_odds"] = px[0][1] if px else None


def load_ledger():
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def log_new(recs):
    seen = {}
    for x in load_ledger():
        seen[x["asset"]] = max(seen.get(x["asset"], 0), x["units"])
    new = []
    with LEDGER.open("a") as f:
        for r in recs:
            if r["units"] and r["units"] > seen.get(r["asset"], 0):
                r["logged"] = datetime.now(timezone.utc).isoformat()
                r["upsize"] = r["asset"] in seen
                f.write(json.dumps(r) + "\n")
                new.append(r)
    return new


def limit_prob(r):
    """Worst price to accept: market +2c, never more than his entry +3c or past -180."""
    return min(r["now"] + 0.02, r["entry"] + CFG["max_drift"], CFG["price_max"])


def paper(r):
    limit = american(limit_prob(r))
    env = dict(os.environ, NOVIG_HOST="https://api.paper.novig.com")
    cmd = ["/usr/bin/python3", str(HERE / "place.py"), "order", r["league"], r["game"],
           r["side"], r["market"], r["start"], "--units", str(r["units"]), "--odds", str(limit)]
    out = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print("   paper:", (out.stdout + out.stderr).strip().splitlines()[-1:])


def x_text(r):
    """Public post: codename, blurred stake, units — never the name or exact dollars."""
    import anon, xcard
    price = (f"{r['best_odds']:+d} at {r['best'].split()[-1]}" if r.get("best_odds")
             else f"{american(limit_prob(r)):+d} or better")
    game = " · ".join(b for b in (r["league"], r["game"].replace(" vs. ", "/"),
                                  xcard.when_text(r["start"])) if b)
    return "\n".join([f"TAIL · {r['play']}   {price}",
                      f"{r['units']:g}u" + (" (added)" if r.get("upsize") else ""),
                      "", f"{anon.alias(r['who'])} · {anon.money(r['stake'])} on it",
                      game])


def post_x(fresh):
    import xpost
    for r in fresh:
        if r["units"] < CFG.get("x_min_units", 1.0):
            continue
        ok, why = xpost.post(x_text(r))
        r["x_posted"] = ok
        print(f"   X: {'posted' if ok else 'FAILED ' + why}")


def show(recs, new):
    keys = {r["asset"] for r in new}
    live = sorted([r for r in recs if r["units"]], key=lambda r: (r["start"], -r["stake"]))
    print(f"TAIL · {datetime.now().strftime('%a %-I:%M %p')} · {', '.join(CFG['wallets'])} · "
          f"{len(live)} rec(s), {len(new)} new")
    for r in live:
        st = datetime.fromisoformat(r["start"]).astimezone().strftime("%-I:%M%p").lower()
        lim = american(limit_prob(r))
        print(f"{'NEW ' if r['asset'] in keys else '    '}{r['units']:.2g}u | {r['play']} | {r['game']} {st} | "
              f"${r['stake']:,} @ {american(r['entry']):+d} | now {american(r['now']):+d} | "
              f"bet at {lim:+d} or better | best {r.get('best', '?')} {('· ' + r['why']) if r['why'] else ''}")
    for r in sorted([r for r in recs if not r["units"] and r["stake"] >= 5000], key=lambda r: -r["stake"]):
        print(f"    pass | {r['play']} | {r['game']} | ${r['stake']:,} | {r['why']}")


def record():
    rows = {}
    for x in load_ledger():
        rows[x["asset"]] = x                      # last (largest) size wins
    graded = risk = pnl = 0
    for x in rows.values():
        d = get(f"https://clob.polymarket.com/prices-history?market={x['asset']}&interval=1d&fidelity=60") or {}
        h = d.get("history") or []
        if not h or 0.02 < h[-1]["p"] < 0.98:
            continue
        won = h[-1]["p"] >= 0.98
        p = prob(x["best_odds"]) if x.get("best_odds") else x["now"] + 0.015
        graded += 1
        risk += x["units"]
        pnl += x["units"] * ((1 - p) / p if won else -1)
    print(f"tail record: {len(rows)} logged, {graded} graded, {pnl:+.2f}u on {risk:.2f}u"
          + (f" (ROI {100*pnl/risk:+.1f}%)" if risk else ""))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["record"]:
        record()
        sys.exit()
    recs = scan()
    led = load_ledger()
    fresh = [r for r in recs if r["units"] and r["units"] > max(
        [x["units"] for x in led if x["asset"] == r["asset"]] or [0])]
    for r in fresh:
        r["upsize"] = any(x["asset"] == r["asset"] for x in led)
    if "--quiet" in a and not fresh:
        sys.exit()
    # book prices cost Odds API credits — the poll only quotes what is new
    add_books(fresh if "--quiet" in a else recs)
    if "--x" in a:
        post_x(fresh)                         # before logging, so x_posted lands in the ledger
    new = log_new(recs)
    show(recs, new)
    if "--paper" in a:
        for r in new:
            paper(r)
