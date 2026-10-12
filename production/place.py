#!/usr/bin/env python3
"""
Place a graded pick on Novig as a limit order — PAPER exchange by default.

    place.py quote  <league> "<Away vs Home>" <side> "<market>" <start-iso>
    place.py order  <league> "<Away vs Home>" <side> "<market>" <start-iso> \\
                    --units 1 --odds -115 [--tif GTC|IOC] [--dry-run]
    place.py from-picks [--since-min 40] [--dry-run]    picks.jsonl -> orders
    place.py status                                    resting / positions / fills
    place.py cancel <orderId> | --all
    place.py settle                                    grade filled orders (nightly)
    place.py record                                    paper P&L from orders.jsonl

Where the money goes
  NOVIG_HOST decides. Default https://api.paper.novig.com (play money).
  Production needs BOTH `--real` on the command line and NOVIG_REAL=1 in the
  environment; anything else exits before a single request is signed.

How an order is priced
  The pick carries the odds it was graded at (`odds`). That is the WORST price
  this will accept: prob_max = implied probability of those odds (+ `slack`).
  The book is read; if the best resting offer on the other side gives us a
  price at or below prob_max we take it (pregame takers pay no fee on game
  markets — `fee.charged == WHEN_LIVE`). Otherwise the order RESTS at prob_max
  as a maker. Resting orders are cancelled by the exchange at go-live, so a
  GTC order can never fill in-game.

Hard limits (place.json beside this file, every field optional)
  unit_dollars      10     dollars per unit
  max_order_dollars 50     a single order can never exceed this
  max_day_dollars   200    sum of today's orders can never exceed this
  slack             0.005  how far past the pick's price we will still pay
  leagues           ["NFL","NCAAF","MLB","NBA","NHL","WNBA","NCAAB"]

Every order is appended to orders.jsonl with the pick, the book seen, the
request body and the answer. The same pick (slug + pick text) is never sent
twice — `clientId` is NOT an idempotency key on Novig, so the ledger is.
"""
import json, os, re, sys, time, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import novig                                     # noqa: E402
from exkeys import NotConfigured                 # noqa: E402

HERE = Path(__file__).parent
ORDERS = HERE / "orders.jsonl"
PICKS = HERE / "picks.jsonl"
CFG_FILE = HERE / "place.json"
DEFAULTS = {"unit_dollars": 10.0, "max_order_dollars": 50.0, "max_day_dollars": 200.0,
            "slack": 0.005, "leagues": ["NFL", "NCAAF", "MLB", "NBA", "NHL", "WNBA", "NCAAB"]}


def cfg():
    c = dict(DEFAULTS)
    try:
        c.update(json.loads(CFG_FILE.read_text()))
    except FileNotFoundError:
        pass
    return c


def is_paper():
    return "paper" in novig.HOST


def guard_env(argv):
    if is_paper():
        return
    if "--real" in argv and os.environ.get("NOVIG_REAL") == "1":
        print(f"!! REAL MONEY: {novig.HOST}", file=sys.stderr)
        return
    sys.exit(f"refusing: NOVIG_HOST={novig.HOST} is not the paper exchange. "
             "Pass --real AND set NOVIG_REAL=1 to place with real money.")


# ------------------------------------------------------------------ ledger
def load_orders():
    out = []
    try:
        for line in ORDERS.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def log_order(rec):
    with open(ORDERS, "a") as f:
        f.write(json.dumps(rec) + "\n")


def already_sent(key):
    return any(o.get("key") == key and o.get("accepted") for o in load_orders())


def spent_today():
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(o.get("dollars", 0) for o in load_orders()
               if o.get("accepted") and o.get("placed_at", "")[:10] == today
               and o.get("host") == novig.HOST)


# ------------------------------------------------------------------ pricing
def row_of(league, game, side, market, start):
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return {"league": league, "game": game, "side": side, "market": market, "start": start}


def locate(h):
    """(market, my_outcome, other_outcome_ids, book) for a board row, or None."""
    ev, home, away = novig.find_event(h)
    if not ev:
        return None
    kind = novig._market_kind(h)
    mtype = novig.market_type_for(kind)
    line = novig._line(h) if kind != "h2h" else None
    side = h["side"]
    d = novig._get("/v3/catalog/markets", f"event={ev['eventId']}&marketType={mtype}&limit=500")
    for m in d.get("items") or []:
        if (m.get("status") or "OPEN").upper() != "OPEN":
            continue
        if line is not None:
            st = m.get("strike")
            if st is None:
                continue
            st = float(st)
            if kind == "totals":
                if abs(st - abs(line)) > 0.01:
                    continue
            else:
                side_is_home = bool(home) and novig._team_match(side, home)
                if abs(st - (line if side_is_home else -line)) > 0.01:
                    continue
        outs = m.get("outcomes") or []
        if len(outs) != 2:
            continue
        mine = None
        if kind == "totals":
            mine = next((o for o in outs if (o.get("name") or "").lower().startswith(side.lower())), None)
        else:
            other = away if (home and novig._team_match(side, home)) else home
            for o in outs:
                ab = (o.get("name") or "").split()[0]
                if novig._abbr_match(ab, side) or (home and away and novig._abbr_match(
                        ab, home if novig._team_match(side, home) else away)):
                    mine = o
                    break
            if mine is None and other:
                rest = [o for o in outs if not novig._abbr_match((o.get("name") or "").split()[0], other)]
                mine = rest[0] if len(rest) == 1 else None
        if mine is None:
            continue
        others = [o["outcomeId"] for o in outs if o["outcomeId"] != mine["outcomeId"]]
        return m, mine, others, novig.book(m["marketId"]), ev
    return None


def best_available(book, others):
    """(taker probability, dollars at that price) from the opposite ladder."""
    prob, avail = novig._best_price_for(book, None, others)
    return prob, avail


def plan(h, units, odds, c=None):
    """Decide the order without sending it. Returns a dict (ok or refused)."""
    c = c or cfg()
    loc = locate(h)
    if not loc:
        return {"ok": False, "why": "no open Novig market matches this row"}
    m, mine, others, book, ev = loc
    if h["start"] <= datetime.now(timezone.utc):
        return {"ok": False, "why": "event already started"}
    prob_max = novig.prob_of(odds) + c["slack"]
    best, avail = best_available(book, others)
    take = best is not None and best <= prob_max
    price = novig.snap_down(best if take else prob_max)
    if price is None:
        return {"ok": False, "why": f"price {prob_max:.3f} off the grid"}
    dollars = round(units * c["unit_dollars"], 2)
    if dollars > c["max_order_dollars"]:
        return {"ok": False, "why": f"${dollars} exceeds max_order_dollars {c['max_order_dollars']}"}
    if spent_today() + dollars > c["max_day_dollars"]:
        return {"ok": False, "why": f"would exceed max_day_dollars {c['max_day_dollars']} (spent {spent_today():.2f})"}
    qty = int(dollars * 100 / float(price))         # contracts pay 1 cent each
    return {"ok": True, "market": m["description"], "marketId": m["marketId"],
            "outcome": mine["name"], "outcomeId": mine["outcomeId"], "event": ev["description"],
            "best": best, "avail": avail, "prob_max": round(prob_max, 4), "take": take,
            "price": price, "qty": qty, "dollars": round(qty * float(price) / 100, 2),
            "american": novig.american(float(price)), "fee": m.get("fee")}


def send(p, h, units, odds, tif="GTC", key=None, pick_key=None, dry=False):
    body = {"outcomeId": p["outcomeId"], "price": p["price"], "qty": p["qty"],
            "tif": tif, "clientId": str(uuid.uuid4())}
    rec = {"placed_at": datetime.now(timezone.utc).isoformat(), "host": novig.HOST,
           "key": pick_key or f"{h['league']}|{h['game']}|{h['side']}|{h['market']}",
           "league": h["league"], "game": h["game"], "side": h["side"], "market": h["market"],
           "start": h["start"].isoformat(), "units": units, "pick_odds": odds,
           "plan": {k: v for k, v in p.items() if k != "fee"}, "request": body,
           "dollars": p["dollars"], "dry_run": dry}
    if dry:
        rec["accepted"] = False
        return rec
    try:
        ans = novig.call("POST", "/v3/orders", body=body, key=key or novig._key("trade"))
        rec["response"] = ans
        rec["accepted"] = bool(ans.get("orderId"))
        rec["orderId"] = ans.get("orderId")
    except Exception as e:
        rec["response"] = {"error": str(e)}
        rec["accepted"] = False
    log_order(rec)
    return rec


def describe(p, h, units):
    if not p["ok"]:
        return f"  refused: {p['why']}"
    mode = "TAKE" if p["take"] else "REST"
    bestp = f"{p['best']:.3f} ({novig.american(p['best'])}, ${p['avail']:,.0f})" if p["best"] else "none"
    return (f"  {p['event']} · {p['market']} · {p['outcome']}\n"
            f"  best available {bestp}; ceiling {p['prob_max']:.3f}\n"
            f"  {mode} {p['qty']} contracts @ {p['price']} ({p['american']:+d}) = ${p['dollars']:.2f} for {units}u")


# -------------------------------------------------------------------- cli
def cmd_quote(a):
    h = row_of(*a[:5])
    loc = locate(h)
    if not loc:
        sys.exit("no open Novig market matches this row")
    m, mine, others, book, ev = loc
    orders = (book.get("book") or book).get("orders") or {}
    print(f"{ev['description']} · {m['description']} · fee {m.get('fee')}")
    for o in m["outcomes"]:
        lad = orders.get(o["outcomeId"]) or []
        top = ", ".join(f"{x['price']}×{int(x['qty'])}" for x in lad[:4])
        print(f"  {'>' if o['outcomeId'] == mine['outcomeId'] else ' '} {o['name']:<12} bids: {top or '-'}")
    best, avail = best_available(book, others)
    if best:
        print(f"  you pay {best:.3f} = {novig.american(best):+d}, ${avail:,.0f} available")


def cmd_order(a):
    dry = "--dry-run" in a
    tif = a[a.index("--tif") + 1] if "--tif" in a else "GTC"
    units = float(a[a.index("--units") + 1]) if "--units" in a else 1.0
    if "--odds" not in a:
        sys.exit("--odds <american> is required: the worst price you accept")
    odds = int(a[a.index("--odds") + 1])
    h = row_of(*[x for x in a if not x.startswith("--")][:5])
    p = plan(h, units, odds)
    print(describe(p, h, units))
    if not p["ok"]:
        sys.exit(1)
    if already_sent(f"{h['league']}|{h['game']}|{h['side']}|{h['market']}"):
        sys.exit("  already sent (orders.jsonl) — not sending twice")
    rec = send(p, h, units, odds, tif=tif, dry=dry)
    print(f"  {'DRY RUN, nothing sent' if dry else ('accepted ' + str(rec.get('orderId')) if rec['accepted'] else 'REFUSED ' + json.dumps(rec.get('response')))}")


def cmd_from_picks(a):
    dry = "--dry-run" in a
    mins = int(a[a.index("--since-min") + 1]) if "--since-min" in a else 40
    c = cfg()
    cut = datetime.now(timezone.utc) - timedelta(minutes=mins)
    rows = [json.loads(l) for l in PICKS.read_text().splitlines() if l.strip()]
    n = sent = 0
    for pk in rows:
        if datetime.fromisoformat(pk["posted_at"]) < cut or pk.get("league") not in c["leagues"]:
            continue
        key = f"{pk.get('slug')}|{pk.get('pick')}"
        if already_sent(key):
            continue
        n += 1
        game = pk.get("title") or (pk.get("game") or "").replace("/", " vs ")
        h = row_of(pk["league"], game, pk["side"], pk.get("market") or "", pk["start"])
        units = float(pk.get("units") or 1.0)
        print(f"{pk['league']} {pk['pick']} {units}u @ {pk['odds']}")
        p = plan(h, units, int(pk["odds"]), c)
        print(describe(p, h, units))
        if not p["ok"]:
            continue
        rec = send(p, h, units, int(pk["odds"]), key=None, pick_key=key, dry=dry)
        sent += rec["accepted"]
        print(f"  {'DRY RUN' if dry else ('accepted ' + str(rec.get('orderId')) if rec['accepted'] else 'REFUSED ' + json.dumps(rec.get('response')))}")
    print(f"{n} candidate pick(s), {sent} order(s) sent")


def _items(d):
    return d if isinstance(d, list) else (d.get("items") or d.get("open") or [])


def cmd_status(_a):
    key = novig._key("trade")
    try:
        print(f"balance: ${float(novig.balance()):.2f}")
    except NotConfigured:
        pass
    items = _items(novig.call("GET", "/v3/orders", key=key))
    print(f"resting orders: {len(items)}")
    for o in items:
        print(f"  {o.get('orderId')} {o.get('outcomeId','')[:8]} {o.get('qty')}@{o.get('price')} "
              f"remaining {o.get('remaining')} {o.get('status')}")
    pos = _items(novig.call("GET", "/v3/portfolio/positions", key=key))
    print(f"positions: {len(pos)}")
    for p in pos:
        print(f"  {p.get('marketId','')[:8]} {p.get('outcomeId','')[:8]} {p.get('qty')} contracts, cost ${float(p.get('cost') or 0):.2f}")
    fills = _items(novig.call("GET", "/v3/portfolio/fills", "limit=10", key=key))
    print(f"last fills: {len(fills)}")
    for f in fills:
        print(f"  {datetime.fromtimestamp(f['ts']/1000, timezone.utc):%m-%d %H:%M} {f.get('qty')} "
              f"cost ${float(f.get('cost') or 0):.2f} fee ${float(f.get('fee') or 0):.4f} {'taker' if f.get('taker') else 'maker'}")


def save_orders(rows):
    tmp = ORDERS.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(ORDERS)


def cmd_settle(_a):
    """Stamp every accepted order with its fill and, once graded, its result."""
    key = novig._key("trade")
    rows = load_orders()
    todo = [r for r in rows if r.get("accepted") and not r.get("final") and r.get("host") == novig.HOST]
    if not todo:
        print("settle: nothing open")
        return
    hist = {}
    after = None
    for _ in range(20):
        d = novig.call("GET", "/v3/history/positions", "limit=500" + (f"&after={after}" if after else ""), key=key)
        for pos in _items(d):
            hist[(pos.get("marketId"), pos.get("outcomeId"))] = pos
        after = d.get("next") if isinstance(d, dict) else None
        if not after:
            break
    graded = unfilled = 0
    for r in todo:
        try:
            o = novig.call("GET", f"/v3/orders/{r['orderId']}", key=key)
        except Exception as e:
            print(f"  {r['orderId'][:8]} read failed: {e}")
            continue
        r["order_status"] = o.get("status")
        r["filled"] = int(o.get("qty", 0)) - int(o.get("remaining", 0))
        if r["filled"] == 0:
            if o.get("status") in ("CANCELED", "REJECTED") or \
                    datetime.fromisoformat(r["start"]) < datetime.now(timezone.utc) - timedelta(hours=1):
                r["final"] = True
                r["result"] = "unfilled"
                unfilled += 1
            continue
        pos = hist.get((r["plan"]["marketId"], r["plan"]["outcomeId"]))
        if not pos:
            continue
        r["cost"] = float(pos.get("cost") or 0)
        res = (pos.get("result") or "TBD").upper()
        if res != "TBD" and pos.get("status", "").upper() in ("FINALIZED", "SETTLED", "DETERMINED"):
            r["result"] = {"WIN": "won", "LOSS": "lost", "LOSE": "lost"}.get(res, res.lower())
            r["payout"] = float(pos.get("payout") or 0)
            r["pnl"] = float(pos.get("pnl") or 0)
            r["settled_ts"] = pos.get("settledTs")
            r["final"] = True
            graded += 1
    save_orders(rows)
    print(f"settle: {len(todo)} open, {graded} graded, {unfilled} unfilled, "
          f"{len(todo) - graded - unfilled} still pending")


def cmd_record(_a):
    rows = [r for r in load_orders() if r.get("accepted") and r.get("host") == novig.HOST]
    sent = len(rows)
    done = [r for r in rows if r.get("final") and r.get("result") not in ("unfilled", None)]
    unf = sum(1 for r in rows if r.get("result") == "unfilled")
    pend = sent - len(done) - unf
    stake = sum(r.get("cost", 0) for r in done)
    pnl = sum(r.get("pnl", 0) for r in done)
    w = sum(1 for r in done if r.get("result") == "won")
    l = sum(1 for r in done if r.get("result") == "lost")
    print(f"paper record on {novig.HOST}: {sent} orders sent · {unf} unfilled · {pend} pending · "
          f"{w}-{l} settled{' (' + str(len(done) - w - l) + ' void)' if len(done) - w - l else ''}")
    if done:
        print(f"  staked ${stake:.2f} · P&L ${pnl:+.2f} · ROI {100 * pnl / stake:+.1f}%")
        by = {}
        for r in done:
            k = r.get("league") or "?"
            by.setdefault(k, [0, 0.0, 0.0])
            by[k][0] += 1; by[k][1] += r.get("cost", 0); by[k][2] += r.get("pnl", 0)
        for k, (n, st, pl) in sorted(by.items()):
            print(f"  {k:<6} {n:>3} · ${st:.2f} · {pl:+.2f}")


def cmd_cancel(a):
    key = novig._key("trade")
    if "--all" in a:
        print(novig.call("DELETE", "/v3/orders", key=key))
    else:
        print(novig.call("DELETE", f"/v3/orders/{a[0]}", key=key))


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("quote", "order", "from-picks", "status", "cancel", "settle", "record"):
        print(__doc__)
        sys.exit(1)
    guard_env(argv)
    print(f"[{'PAPER' if is_paper() else 'REAL'}] {novig.HOST}")
    rest = [x for x in argv[1:] if x != "--real"]
    {"quote": cmd_quote, "order": cmd_order, "from-picks": cmd_from_picks,
     "status": cmd_status, "cancel": cmd_cancel, "settle": cmd_settle,
     "record": cmd_record}[argv[0]](rest)
