#!/usr/bin/env python3
"""Import Juice Reel bet exports into a normalised ledger.

Juice Reel exports one row per LEG; a parlay is several rows sharing
juice_bet_id. This collapses them to one record per bet, maps the columns onto
bets.jsonl's schema, and writes juicereel.jsonl (newest last, deduped on
juice_bet_id). Re-running with more exports is safe and incremental.

  juicereel.py ~/Downloads/juiceReelBets_*.csv          # import + summary
  juicereel.py --report                                 # summary of what is stored
  juicereel.py --overlap                                # rows that may duplicate bets.jsonl

bets.jsonl is NEVER written here -- it is Santhosh's own hand-kept ledger and
the two overlap in September. Use --overlap to see the collision before
deciding to merge.
"""
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "juicereel.jsonl")
BETS = os.path.join(HERE, "bets.jsonl")

# Juice Reel's book names -> the spellings already used in bets.jsonl.
BOOKS = {"NoVig": "Novig", "Fanatics": "fanatics", "Play23": "play23",
         "SportsBets": "sportsbets", "Sun22": "sun22", "DraftKings": "draftkings",
         "ProphetX": "ProphetX"}
RESULT = {"Won": "won", "Lost": "lost", "Push": "push",
          "CashedOut": "cashedout", "Pending": None}
MAIN = {"Moneyline", "Spread", "GameOu", "TeamOu", "Moneyline3Way"}


def num(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def leg_text(r):
    """Condense one leg to board-style text: 'Chiefs -7.5', 'Over 43.5', 'Reds ML'."""
    t, on = r.get("leg_type") or "", (r.get("bet_on") or "").strip()
    n = num(r.get("bet_on_spread_total_number"))
    if t in ("Moneyline", "Moneyline3Way"):
        return f"{on} ML"
    if t == "Spread" and n is not None:
        return f"{on} {n:+g}"
    if t in ("GameOu", "TeamOu") and n is not None:
        return f"{on} {n:g}"
    desc = (r.get("leg_description") or "").strip()
    return desc or (f"{on} {n:g}" if n is not None else on)


def to_record(legs):
    """Collapse a bet's legs (list of CSV rows) into one record."""
    h = legs[0]
    texts = [leg_text(r) for r in legs]
    single = len(legs) == 1
    rec = {
        "bet_id": "jr:" + h["juice_bet_id"],
        "source": "juicereel",
        "date": (h.get("date_placed") or "")[:10],
        "placed_at": h.get("date_placed") or None,
        "settled_at": h.get("date_settled") or None,
        "kind": "single" if single else "parlay",
        "book": BOOKS.get(h.get("sportsbook"), (h.get("sportsbook") or "").lower()),
        "pick": " + ".join(texts),
        "game": h.get("event_name") or None,
        "odds": int(num(h.get("odds_american"), 0)),
        "risk": num(h.get("risk_amount"), 0.0),
        "to_win": num(h.get("max_potential_win"), 0.0),
        "result": RESULT.get(h.get("bet_result"), (h.get("bet_result") or "").lower() or None),
        "pnl": num(h.get("amount_won_or_lost")),
        "clv": num(h.get("clv_percent")),
        "n_legs": len(legs),
        "market": h.get("leg_type") if single else "parlay",
        "league": h.get("leg_league") or None,
        "sport": h.get("leg_sport") or None,
        "start": h.get("event_start_date") or None,
        "boosted": (h.get("is_odds_boosted") or "").upper() == "TRUE",
        "freeplay": bool((h.get("if_freeplay_then_amount_actually_at_risk") or "").strip()),
    }
    if not single:
        rec["legs"] = texts
        rec["league"] = None if len({r.get("leg_league") for r in legs}) > 1 else rec["league"]
    return rec


def read_csvs(paths):
    by_bet = defaultdict(list)
    seen_leg = set()
    for p in paths:
        with open(p, newline="", encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                k = (r.get("juice_bet_id"), r.get("bet_leg_id"))
                if k in seen_leg:
                    continue
                seen_leg.add(k)
                by_bet[r["juice_bet_id"]].append(r)
    return [to_record(v) for v in by_bet.values()]


def load_store():
    if not os.path.exists(STORE):
        return []
    return [json.loads(l) for l in open(STORE) if l.strip()]


def save_store(rows):
    rows.sort(key=lambda r: (r.get("placed_at") or "", r["bet_id"]))
    tmp = STORE + ".tmp"
    with open(tmp, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, STORE)


def do_import(paths):
    incoming = read_csvs(paths)
    store = load_store()
    have = {r["bet_id"]: i for i, r in enumerate(store)}
    added = updated = 0
    for r in incoming:
        if r["bet_id"] in have:
            old = store[have[r["bet_id"]]]
            if old != r:                     # a pending bet that has since settled
                store[have[r["bet_id"]]] = r
                updated += 1
        else:
            store.append(r)
            added += 1
    save_store(store)
    print(f"{added} new, {updated} updated, {len(store)} total in {STORE}")
    return store


def report(rows):
    if not rows:
        print("nothing stored yet")
        return
    d = sorted(r["date"] for r in rows if r["date"])
    print(f"\n{len(rows)} bets, {d[0]} .. {d[-1]}")
    settled = [r for r in rows if r["result"] in ("won", "lost", "push", "cashedout")]
    rk = sum(r["risk"] for r in settled)
    pl = sum(r["pnl"] or 0 for r in settled)
    print(f"settled {len(settled)}: risked ${rk:,.0f}  net ${pl:+,.0f}  ROI {100*pl/rk:+.2f}%")
    print(dict(Counter(r["result"] for r in rows)))

    def block(label, sub):
        if not sub:
            return
        rk = sum(r["risk"] for r in sub)
        pl = sum(r["pnl"] or 0 for r in sub)
        w = sum(1 for r in sub if r["result"] == "won")
        print(f"  {label:<26}{len(sub):>5}{w:>5}w{rk:>10,.0f}{pl:>+9,.0f}"
              f"{100*pl/rk if rk else 0:>8.1f}%")

    print(f"\n  {'class':<26}{'n':>5}{'won':>6}{'risked':>10}{'net':>9}{'ROI':>9}")
    block("single: main market", [r for r in settled if r["n_legs"] == 1 and r["market"] in MAIN])
    block("single: prop/exotic", [r for r in settled if r["n_legs"] == 1 and r["market"] not in MAIN])
    block("parlay", [r for r in settled if r["n_legs"] > 1])

    print(f"\n  {'book':<26}{'n':>5}{'won':>6}{'risked':>10}{'net':>9}{'ROI':>9}")
    for b in sorted({r["book"] for r in settled}):
        block(b, [r for r in settled if r["book"] == b])

    clv = [r for r in rows if r["clv"] is not None]
    print(f"\nCLV: {len(clv)}/{len(rows)} bets carry a value ({100*len(clv)/len(rows):.0f}%)"
          " -- Juice Reel prices main markets on major leagues only")
    if clv:
        v = [r["clv"] for r in clv]
        print(f"  mean {100*statistics.mean(v):+.2f}%  median {100*statistics.median(v):+.2f}%"
              f"  beat close {100*sum(1 for x in v if x > 0)/len(v):.0f}%")
        print(f"  {'league':<26}{'n':>5}{'meanCLV':>10}{'beat':>7}")
        for lg in sorted({r["league"] for r in clv if r["league"]}):
            s = [r["clv"] for r in clv if r["league"] == lg]
            print(f"  {lg[:25]:<26}{len(s):>5}{100*statistics.mean(s):>+9.2f}%"
                  f"{100*sum(1 for x in s if x > 0)/len(s):>6.0f}%")


def overlap(rows):
    """Flag stored bets that look like rows already in the hand-kept bets.jsonl."""
    if not os.path.exists(BETS):
        print("no bets.jsonl")
        return
    manual = [json.loads(l) for l in open(BETS) if l.strip()]
    mdates = {r.get("date") for r in manual}
    cand = [r for r in rows if r["date"] in mdates]
    print(f"bets.jsonl: {len(manual)} hand-kept rows, {min(mdates)} .. {max(mdates)}")
    print(f"juicereel.jsonl rows falling on those same dates: {len(cand)}")
    hit = 0
    for m in manual:
        for r in cand:
            if (r["date"] == m.get("date")
                    and abs((r["risk"] or 0) - (m.get("risk") or 0)) < 0.01
                    and r["odds"] == m.get("odds")):
                hit += 1
                break
    print(f"hand-kept rows with a date+risk+odds twin in the Juice Reel data: {hit}/{len(manual)}")
    print("\nMerging the two would double-count those. bets.jsonl was NOT modified.")


DROP = os.path.expanduser("~/Downloads")


def find_exports():
    """Every Juice Reel export sitting in ~/Downloads, oldest first."""
    import glob
    return sorted(glob.glob(os.path.join(DROP, "juiceReelBets_*.csv")),
                  key=os.path.getmtime)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args and "--report" not in flags and "--overlap" not in flags:
        # Monday routine: just run `juicereel.py` after dropping the export in
        # ~/Downloads. Re-reading a file already imported is harmless -- the
        # store dedupes on juice_bet_id.
        args = find_exports()
        if args:
            print("picked up %d export(s) from ~/Downloads:" % len(args))
            for a in args:
                print("  " + os.path.basename(a))
        else:
            print("no juiceReelBets_*.csv in ~/Downloads -- export from the app "
                  "(Analytics -> bottom -> export bets by CSV)")
    rows = do_import(args) if args else load_store()
    if "--overlap" in flags:
        overlap(rows)
    else:
        report(rows)
