#!/usr/bin/env python3
"""
Novig resting-order archive — the one dataset the Unjuiced "Sharp Money" idea
needs and that cannot be bought retroactively.

    python3 novigdepth.py              # one poll, append to today's file
    python3 novigdepth.py --dry        # show what it would record, write nothing
    python3 novigdepth.py --leagues NFL,NBA
    python3 novigdepth.py --status     # what is archived so far

WHY. Unjuiced (2026-10-26) reads the pregame order books on Novig and ProphetX,
calls a market "leaning" when >=70% of the resting size sits on one side, and
then asks whether your sportsbook price beats what those posters are paying.
They publish no ROI, no CLV and no sample, and say outright that their tier is
"not a rating of the bet" — so the lean is an untested hypothesis, not a result.

lines/ already archives Novig and ProphetX PRICES (books.py -> linearchive.py),
so the price half of that idea is testable on data we hold. The lean half is
not: nothing anywhere records how much size was RESTING on each side. That is
what this collects, so the question can be answered in a few weeks instead of
never.

COST. A full book() per market is impossible: 10,386 pregame markets were open
across five leagues on 2026-10-26, and that is one request each. The catalog
already carries each outcome's best resting bid (price + qty) and costs one
paginated call per league, so the sweep is two tiers:

    tier 1  catalog      every pregame market, TOP of book, ~5 calls
    tier 2  book(id)     full ladder, only for markets already leaning at top

Tier 2 is capped (--max-books) so a busy Saturday cannot turn into a 3,000-call
sweep.

DOLLARS, NOT QTY. Resting qty is in hundredths of a $1 contract, so a side's
real exposure is qty/100 * price (novig._qty_dollars). This matters: a market
seen on 2026-10-26 had 171,000 qty resting at price 0.001 on one side, which is
$1.71 of dust, not size. Ranking on raw qty would read that as the whole book.
Every figure here is dollars at risk.

PRIVATE. Novig order books are account-gated data read with Santhosh's own
read key. The archive stays on disk; nothing here posts, emails or trades.
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import novig

ARCHIVE = os.path.join(HERE, "novigdepth")
LEAGUES = ["NFL", "NBA", "NHL", "NCAAF", "MLB"]
# Main markets only. Props are a different question and books.py cannot price
# most of them, so a prop row could never be compared against a sportsbook.
MAIN = {"MONEYLINE", "MONEY", "SPREAD", "TOTAL"}
HORIZON_H = 24          # only markets kicking off inside this window
LEAN_MIN = 0.60         # fetch the full ladder once top-of-book looks this skewed
MAX_BOOKS = 150         # hard cap on tier-2 calls per poll
CATALOG_LIMIT = 3000


def dollars(qty, price):
    """Resting qty -> dollars at risk. novig._qty_dollars, without the import."""
    try:
        return round(float(qty) / 100 * float(price), 2)
    except (TypeError, ValueError):
        return 0.0


def top_of_book(m):
    """[{name, outcome_id, price, qty, dollars}] from the catalog row itself."""
    out = []
    for o in m.get("outcomes") or []:
        bid = o.get("bid") or {}
        p, q = bid.get("price"), bid.get("qty")
        out.append({"name": o.get("name"), "outcome_id": o.get("outcomeId"),
                    "price": float(p) if p is not None else None,
                    "qty": q, "dollars": dollars(q, p) if p is not None else 0.0})
    return out


def ladder_dollars(bk, sides):
    """Full resting ladder per outcome -> dollars and order count, in place."""
    orders = (bk.get("book") or bk).get("orders") or {}
    for s in sides:
        lst = orders.get(s["outcome_id"]) or []
        s["depth_dollars"] = round(sum(dollars(x.get("qty"), x.get("price")) for x in lst), 2)
        s["orders_n"] = len(lst)
        s["ladder"] = [{"price": float(x.get("price")), "qty": x.get("qty")} for x in lst]
    return sides


def lean_of(sides, key):
    """(side name, share) for the fattest side by `key`, or (None, None)."""
    tot = sum(s.get(key) or 0.0 for s in sides)
    if tot <= 0:
        return None, None, 0.0
    best = max(sides, key=lambda s: s.get(key) or 0.0)
    return best["name"], round((best.get(key) or 0.0) / tot, 4), round(tot, 2)


def poll(leagues, horizon_h=HORIZON_H, lean_min=LEAN_MIN, max_books=MAX_BOOKS,
         main_only=True, verbose=False):
    now = datetime.now(timezone.utc)
    hi = (now + timedelta(hours=horizon_h)).timestamp()
    rows, scanned, booked, errs = [], 0, 0, 0

    for lg in leagues:
        try:
            cat = novig.catalog_markets(lg, limit=CATALOG_LIMIT) or []
        except Exception as e:
            errs += 1
            print(f"  !! {lg} catalog FAILED: {str(e)[:120]}", flush=True)
            continue
        for m in cat:
            if m.get("status") != "OPEN":
                continue
            ts = (m.get("startsTs") or 0) / 1000
            if not (now.timestamp() < ts <= hi):
                continue
            if main_only and (m.get("marketType") or "") not in MAIN:
                continue
            scanned += 1
            sides = top_of_book(m)
            if len(sides) < 2 or not any(s["dollars"] for s in sides):
                continue
            tname, tshare, ttot = lean_of(sides, "dollars")
            rows.append({"league": lg, "event_id": m.get("eventId"),
                         "market_id": m.get("marketId"),
                         "description": m.get("description"),
                         "market_type": m.get("marketType"),
                         "starts_ts": m.get("startsTs"),
                         "open_interest": m.get("openInterest"),
                         "volume": m.get("volume"),
                         "sides": sides,
                         "top_lean_side": tname, "top_lean": tshare,
                         "top_dollars": ttot, "depth": False})

    # tier 2: the ladder, for the most one-sided and busiest rows only
    cands = [r for r in rows if (r["top_lean"] or 0) >= lean_min]
    cands.sort(key=lambda r: -(r["open_interest"] or 0))
    for r in cands[:max_books]:
        try:
            bk = novig.book(r["market_id"])
        except Exception:
            errs += 1
            continue
        if not isinstance(bk, dict):
            errs += 1
            continue
        ladder_dollars(bk, r["sides"])
        dname, dshare, dtot = lean_of(r["sides"], "depth_dollars")
        r.update(depth=True, lean_side=dname, lean=dshare, total_dollars=dtot,
                 seq=bk.get("seq"))
        booked += 1

    stamp = now.isoformat()
    for r in rows:
        r["t"] = stamp
        r.setdefault("lean_side", r["top_lean_side"])
        r.setdefault("lean", r["top_lean"])
        r.setdefault("total_dollars", r["top_dollars"])
    return rows, {"scanned": scanned, "rows": len(rows), "booked": booked,
                  "errors": errs, "t": stamp}


def write(rows):
    os.makedirs(ARCHIVE, exist_ok=True)
    day = datetime.now(timezone.utc).astimezone().date().isoformat()
    path = os.path.join(ARCHIVE, f"{day}.jsonl")
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return path


def status():
    if not os.path.isdir(ARCHIVE):
        print("nothing archived yet")
        return
    tot = 0
    for name in sorted(os.listdir(ARCHIVE)):
        if not name.endswith(".jsonl"):
            continue
        p = os.path.join(ARCHIVE, name)
        n = sum(1 for _ in open(p))
        tot += n
        print(f"  {name}  {n:7,} rows  {os.path.getsize(p)/1e6:6.2f} MB")
    print(f"  {'TOTAL':14} {tot:7,} rows")


def main():
    a = sys.argv[1:]
    if "--status" in a:
        return status()
    leagues = LEAGUES
    if "--leagues" in a:
        leagues = [x.strip().upper() for x in a[a.index("--leagues") + 1].split(",") if x.strip()]
    lean_min = float(a[a.index("--lean") + 1]) if "--lean" in a else LEAN_MIN
    max_books = int(a[a.index("--max-books") + 1]) if "--max-books" in a else MAX_BOOKS
    horizon = float(a[a.index("--horizon") + 1]) if "--horizon" in a else HORIZON_H
    t0 = time.time()
    rows, st = poll(leagues, horizon_h=horizon, lean_min=lean_min, max_books=max_books,
                    main_only="--all-types" not in a)
    took = time.time() - t0
    lean = [r for r in rows if (r.get("lean") or 0) >= 0.70]
    print(f"novigdepth {st['t']} · {st['scanned']} pregame main markets · "
          f"{st['rows']} with resting size · {st['booked']} ladders · "
          f"{len(lean)} at >=70% lean · {st['errors']} errors · {took:.0f}s", flush=True)
    if "--dry" in a:
        for r in sorted(lean, key=lambda r: -(r.get("total_dollars") or 0))[:15]:
            print(f"  {r['league']:6} {(r['description'] or '')[:44]:44} "
                  f"lean {r['lean']:.0%} {r['lean_side'][:18]:18} "
                  f"${r['total_dollars']:>10,.0f}{'  [ladder]' if r['depth'] else ''}")
        print("(dry run — nothing written)")
        return 0
    path = write(rows)
    print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
