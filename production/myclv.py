#!/usr/bin/env python3
"""
"my clv" — did YOUR bets beat the closing line?

    python3 myclv.py                # every settled bet that can be priced
    python3 myclv.py --days 3
    python3 myclv.py --verbose      # per-bet detail

clv.py measures closing-line value on the SIGNALS the board surfaces. Nothing
has ever measured it on bets.jsonl — the wagers actually placed. That is the
more useful number, and the faster-converging one: win rate needs hundreds of
results to separate skill from variance, CLV gives a read from dozens, because
it asks whether the price was good rather than whether the coin landed.

The benchmark is Polymarket's last trade before kickoff, for the same outcome.
It is NOT the book's own closing number, which nobody archives — so read this as
"did I beat the market", not "did I beat my book". The bias is consistent across
books, which is what makes the BY BOOK breakdown meaningful: a book that
repeatedly gives you a better number than the market close is worth more of your
volume, regardless of whether those particular bets won.

Positive CLV means you bought at a better price than the market settled at.
That is the one thing under your control — the result is not.
"""
import json, os, re, statistics, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check as chk
import settle as S

BETS = os.path.join(HERE, "bets.jsonl")
HIST = ("https://clob.polymarket.com/prices-history"
        "?market={t}&startTs={a}&endTs={b}&fidelity=1")


def implied(odds):
    return (100.0 / (odds + 100.0)) if odds > 0 else (-odds / (-odds + 100.0))


def token_for(ev, pick):
    """CLOB token id for the outcome this bet backed."""
    kind, who, num = S.parse_pick(pick)
    if kind in (None, "parlay"):
        return None, "parlay or unparseable"
    for mk in ev.get("markets", []):
        q = (mk.get("question") or "").strip()
        names = S.plist(mk.get("outcomes"))
        toks = S.plist(mk.get("clobTokenIds"))
        if len(names) != len(toks) or not toks:
            continue
        if kind == "total":
            if not re.search(rf"O/U\s*{re.escape(f'{num:g}')}\s*$", q, re.I):
                continue
            if who in names:
                return toks[names.index(who)], q
        elif kind == "ml":
            if q != (ev.get("title") or ""):
                continue
            key = who.lower().split()[-1]
            for i, n in enumerate(names):
                if key in n.lower():
                    return toks[i], q
        elif kind == "spread":
            if not q.lower().startswith("spread"):
                continue
            if str(abs(num)) not in q:
                continue
            key = who.lower().split()[-1]
            for i, n in enumerate(names):
                if key in n.lower():
                    return toks[i], q
    return None, f"no market matching {pick!r}"


def closing(tok, kick_ts, cache):
    if tok in cache:
        return cache[tok]
    d = chk.get(HIST.format(t=tok, a=kick_ts - 6 * 3600, b=kick_ts + 300))
    h = d.get("history") if isinstance(d, dict) else (d if isinstance(d, list) else None)
    pts = sorted((int(p["t"]), float(p["p"])) for p in (h or []) if "t" in p)
    before = [p for t, p in pts if t <= kick_ts]
    cache[tok] = before[-1] if before else None
    return cache[tok]


def main():
    verbose = "--verbose" in sys.argv
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else None
    rows = [json.loads(l) for l in open(BETS) if l.strip()]
    rows = [b for b in rows if b.get("result") in ("won", "lost")]
    if days:
        cut = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        rows = [b for b in rows if b.get("date", "") >= cut]
    if not rows:
        print("No settled bets.")
        return 0

    bydate = defaultdict(list)
    for b in rows:
        bydate[b["date"]].append(b)

    cache, out, skip = {}, [], defaultdict(int)
    for day, bs in sorted(bydate.items()):
        ledger = S.slugs_from_ledger(day)
        ecache, events = {}, []
        for b in bs:
            if b["kind"] == "parlay":
                skip["parlay"] += 1
                continue
            ev = S.find_event(b.get("game"), events, day, ledger, ecache)
            if not ev:
                skip["no event"] += 1
                continue
            tok, why = token_for(ev, b.get("pick"))
            if not tok:
                skip[why[:24]] += 1
                continue
            raw = ev.get("startTime")
            try:
                kick = int(datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).timestamp())
            except Exception:
                skip["no kickoff"] += 1
                continue
            close = closing(tok, kick, cache)
            if close is None:
                skip["no price history"] += 1
                continue
            b["close_p"] = close
            b["my_p"] = implied(b["odds"])
            b["clv"] = (close - b["my_p"]) * 100      # points of implied prob
            out.append(b)

    if not out:
        print(f"Nothing priceable. Skipped: {dict(skip)}")
        return 0

    def block(label, bs):
        if not bs:
            print(f"  {label:20}       —")
            return
        v = [b["clv"] for b in bs]
        beat = sum(1 for x in v if x > 0)
        games = len({(b['date'], b['game']) for b in bs})
        print(f"  {label:20} {len(bs):>3} bets  CLV {statistics.mean(v):>+6.2f} pts  "
              f"beat close {beat}/{len(bs)} ({beat/len(bs)*100:>3.0f}%)  {games} games")

    print(f"YOUR CLOSING-LINE VALUE · {len(out)} bets priced, "
          f"{sum(skip.values())} unpriceable\n")
    print("Benchmark: Polymarket's last trade before kickoff on the same outcome.")
    print("Positive = you bought at a better price than the market closed at.\n")
    block("ALL", out)
    print("\nBY BOOK")
    for bk in sorted({b["book"] for b in out}):
        block(bk, [b for b in out if b["book"] == bk])
    print("\nBY TYPE")
    for lab, fn in [("totals", lambda b: b["pick"].split()[0] in ("Over", "Under")),
                    ("spreads", lambda b: S.parse_pick(b["pick"])[0] == "spread"),
                    ("moneyline", lambda b: S.parse_pick(b["pick"])[0] == "ml")]:
        block(lab, [b for b in out if fn(b)])
    print("\nBY RESULT")
    block("won", [b for b in out if b["result"] == "won"])
    block("lost", [b for b in out if b["result"] == "lost"])

    v = [b["clv"] for b in out]
    m = statistics.mean(v)
    print("\n" + "=" * 72)
    if m > 0.5:
        print(f"Average CLV {m:+.2f} pts — you are buying better than the market closes.")
        print("That is the signature of a real edge and it shows up long before")
        print("the win column does.")
    elif m < -0.5:
        print(f"Average CLV {m:+.2f} pts — you are paying worse than the close.")
        print("Any profit so far is more likely variance than edge. The fix is")
        print("price, not picks: shop harder and take the exchanges more often.")
    else:
        print(f"Average CLV {m:+.2f} pts — flat. No evidence either way yet.")
    if skip:
        print(f"\nunpriceable: {dict(skip)}")
    if verbose:
        print("\nDETAIL (worst to best)")
        for b in sorted(out, key=lambda x: x["clv"]):
            print(f"  {b['clv']:>+6.2f}  {b['result']:4} {b['book']:9} {b['odds']:>+6d} "
                  f"(you {b['my_p']*100:>5.1f}% vs close {b['close_p']*100:>5.1f}%)  "
                  f"{b['pick'][:28]:30}{b['game'][:24]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
