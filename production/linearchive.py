#!/usr/bin/env python3
"""
Price archive — every book, every line, on a schedule, kept forever.

    python3 linearchive.py              # one poll, append to today's file
    python3 linearchive.py --dry        # show what it would record, write nothing
    python3 linearchive.py --sports mlb,nfl
    python3 linearchive.py --status     # what is archived so far

books.py is a LOOKUP: it fetches every book's price, prints it, and discards the
response. Every quote in this project so far has evaporated. This records the
same calls instead, so that after a week there is a time series of how each line
moved at each book — a dataset that cannot be bought retroactively, only
collected.

WHY THIS AND NOT MORE WALLET WORK. The 552-game out-of-sample backtest found the
wallet gate returns -8.2% with a game-clustered CI excluding zero, and that
conviction does not order outcomes. The two things it DID support were both
about price movement: fresh-vs-stale entries (+39.1% ROI, CI excludes zero) and
a monotonic drift filter. myclv.py then found the placed bets average -0.40 pts
of closing-line value and beat the close only 18% of the time. The evidence says
the weakness is price, not picks — and price history is the one input nobody
here has ever stored.

COST. One call covers one sport + one market across every game and every book.
regions=us,us_ex makes it 2 credits. Three markets over three sports is 18
credits a poll; hourly over a 12-hour window is ~6,500/month against a 20,000
plan. The poll SKIPS sports with no game inside LOOKAHEAD_H, so overnight and
off-season hours cost nothing at all.

STORAGE. One JSONL file per UTC day under lines/, one row per
(game, market, book, price) observation. A few MB a month.
"""
import json, os, sys, time
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import books
from oddskey import get_key

OUT = os.path.join(HERE, "lines")
MARKETS = ["h2h", "spreads", "totals"]
SPORTS = {                      # label -> Odds API sport key
    "mlb": "baseball_mlb",
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
}
LOOKAHEAD_H = 14                # only poll a sport with a game starting this soon
BOOKS = set(books.MY_BOOKS) | set(books.REFS)


def day_file(ts=None):
    d = datetime.fromtimestamp(ts or time.time(), timezone.utc)
    return os.path.join(OUT, f"{d:%Y-%m-%d}.jsonl")


def rows_from(sport_label, market, events, now):
    """Flatten one API response into observation rows."""
    out = []
    for ev in events or []:
        ct = ev.get("commence_time") or ""
        try:
            start = int(datetime.fromisoformat(
                ct.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
        if not (0 < start - now < LOOKAHEAD_H * 3600):
            continue            # only pre-game, and only inside the window
        for bk in ev.get("bookmakers") or []:
            if bk.get("key") not in BOOKS:
                continue
            for mk in bk.get("markets") or []:
                for o in mk.get("outcomes") or []:
                    if o.get("price") is None:
                        continue
                    out.append({
                        "t": now, "sport": sport_label, "mkt": market,
                        "gid": ev.get("id"), "start": start,
                        "home": ev.get("home_team"), "away": ev.get("away_team"),
                        "book": books.BOOK_LABEL.get(bk["key"], bk["key"]),
                        "name": o.get("name"), "point": o.get("point"),
                        "price": int(o["price"]),
                    })
    return out


def has_game_soon(sport_key, key, now):
    """One cheap h2h call decides whether the other markets are worth polling."""
    evs = books.fetch(sport_key, "h2h", key)
    for ev in evs or []:
        ct = ev.get("commence_time") or ""
        try:
            start = int(datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
        if 0 < start - now < LOOKAHEAD_H * 3600:
            return True, evs
    return False, evs


def status():
    if not os.path.isdir(OUT):
        print("nothing archived yet")
        return 0
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".jsonl"))
    if not files:
        print("nothing archived yet")
        return 0
    tot = 0
    print(f"{'DAY':12} {'ROWS':>8} {'GAMES':>7} {'BOOKS':>6} {'POLLS':>6}  SIZE")
    for f in files:
        p = os.path.join(OUT, f)
        games, bks, polls, n = set(), set(), set(), 0
        for line in open(p):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1
            games.add(r.get("gid")); bks.add(r.get("book")); polls.add(r.get("t"))
        tot += n
        print(f"{f[:-6]:12} {n:>8,} {len(games):>7} {len(bks):>6} {len(polls):>6}  "
              f"{os.path.getsize(p)/1024:,.0f} KB")
    print(f"{'TOTAL':12} {tot:>8,}")
    return 0


def main():
    a = sys.argv[1:]
    if "--status" in a:
        return status()
    dry = "--dry" in a
    want = (a[a.index("--sports") + 1].split(",") if "--sports" in a
            else list(SPORTS))
    os.makedirs(OUT, exist_ok=True)
    try:
        key = get_key()
    except Exception as e:
        print(f"no odds key: {e}", file=sys.stderr)
        return 1

    now = int(time.time())
    rows, polled, skipped = [], [], []
    for label in want:
        sk = SPORTS.get(label)
        if not sk:
            continue
        soon, evs = has_game_soon(sk, key, now)
        if not soon:
            skipped.append(label)          # nothing within the window: free
            continue
        rows += rows_from(label, "h2h", evs, now)
        for m in MARKETS:
            if m == "h2h":
                continue                    # already fetched by the probe
            rows += rows_from(label, m, books.fetch(sk, m, key), now)
        polled.append(label)

    q = books.quota()
    stamp = datetime.fromtimestamp(now, timezone.utc).astimezone()
    print(f"ARCHIVE · {stamp:%a %-I:%M %p %Z} · {len(rows):,} observations")
    print(f"  polled: {', '.join(polled) or 'none'}"
          + (f" · skipped (no game within {LOOKAHEAD_H}h): {', '.join(skipped)}"
             if skipped else ""))
    if rows:
        g = len({r['gid'] for r in rows}); b = len({r['book'] for r in rows})
        print(f"  {g} game(s) · {b} book(s) · "
              + ", ".join(sorted({r['mkt'] for r in rows})))
    print(f"  credits: {q['used']} used, {q['remaining']} remaining")

    if dry:
        for r in rows[:8]:
            print("   ", json.dumps(r))
        print(f"   ... ({len(rows)} rows, nothing written)")
        return 0
    if rows:
        with open(day_file(now), "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"  appended to {day_file(now)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
