#!/usr/bin/env python3
"""
Stage 1 — every closed NFL / NCAAF game in a window, main markets only.

    python3 hist_slate.py 2025-10-01 2025-10-31 [nfl cfb]

Writes windows/<start>/slate.json:
    {"built", "start", "end", "tags", "games": [ {slug, title, start, league,
      markets: [ {question, conditionId, outcomes, clobTokenIds, outcomePrices} ] } ]}

gamma's offset caps at 2000 per query, so the window is walked in 5-day
slices (a football week is ~80 games x 27 markets, well under the cap).
The date filter returns prop/inning sub-events but often not the main event,
so main slugs are derived from sub-event slugs and fetched directly — the
same fix holders.py needed in production.
"""
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import btlib as B

GAMMA = ("https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=true"
         "&limit=100&offset={off}&start_date_min={lo}&start_date_max={hi}")
SUB = re.compile(r"-(player-props|inning-\d+-winner|first-five-winner|first-half-winner|quarter-\d-winner|props)$")


def fetch(tag, lo, hi):
    out, off = [], 0
    while True:
        got = B.get(GAMMA.format(tag=tag, off=off, lo=lo, hi=hi)) or []
        out += got
        if len(got) < 100:
            return out
        off += 100
        if off >= 2000:
            print(f"  !! {tag} {lo}..{hi} hit the offset cap — PARTIAL", flush=True)
            return out


def main_markets(ev):
    title = (ev.get("title") or "").strip()
    for m in ev.get("markets", []):
        q = (m.get("question") or "").strip()
        if q == title or re.match(r"^Spread: .+\([+-][\d.]+\)$", q) \
                or re.match(rf"^{re.escape(title)}: O/U [\d.]+$", q):
            if m.get("clobTokenIds") and m.get("outcomePrices"):
                yield {"question": q, "conditionId": m.get("conditionId"),
                       "outcomes": m.get("outcomes"), "clobTokenIds": m.get("clobTokenIds"),
                       "outcomePrices": m.get("outcomePrices"), "closed": m.get("closed")}


def main():
    a = sys.argv[1:]
    start, end = a[0], a[1]
    tags = [x for x in a[2:] if x in ("nfl", "cfb")] or ["nfl", "cfb"]
    out_dir = os.path.join(HERE, "windows", start[:7])
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    lo = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    hi = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    evs = {}
    for tag in tags:
        cur = lo - timedelta(days=3)
        while cur < hi + timedelta(days=3):
            nxt = cur + timedelta(days=5)
            for e in fetch(tag, cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")):
                e["_tag"] = tag
                evs[e["slug"]] = e
            cur = nxt
        print(f"  {tag}: {sum(1 for e in evs.values() if e['_tag'] == tag)} events", flush=True)
    bases = {SUB.sub("", s) for s in evs if SUB.search(s)}
    for b in sorted(bases - set(evs)):
        for e in B.get(f"https://gamma-api.polymarket.com/events?slug={b}") or []:
            e["_tag"] = "nfl" if b.startswith("nfl") else "cfb"
            evs[e["slug"]] = e
    games = []
    for slug, e in evs.items():
        if SUB.search(slug):
            continue
        try:
            st = datetime.fromisoformat((e.get("startTime") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (lo <= st < hi + timedelta(days=1)):
            continue
        mk = list(main_markets(e))
        if not mk:
            continue
        games.append({"slug": slug, "title": e.get("title"), "start": st.isoformat(),
                      "league": "NFL" if e["_tag"] == "nfl" else "NCAAF", "markets": mk})
    games.sort(key=lambda g: g["start"])
    settled = sum(1 for g in games for m in g["markets"] if m.get("closed"))
    json.dump({"built": datetime.now(timezone.utc).isoformat(), "start": start, "end": end,
               "tags": tags, "games": games,
               "requests": dict(B.STATS)}, open(os.path.join(out_dir, "slate.json"), "w"))
    print(f"{len(games)} games · {sum(len(g['markets']) for g in games)} main markets ({settled} closed) · "
          f"{dict((l, sum(1 for g in games if g['league'] == l)) for l in ('NFL', 'NCAAF'))} · "
          f"{B.STATS['ok']} ok / {B.STATS['fail']} failed · {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
