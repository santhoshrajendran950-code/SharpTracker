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

# Two ways to ask gamma for a window of games, because `start_date_*` does NOT
# mean kickoff. On a game event `startDate` is when the market OPENED (the
# 49ers/Rams Week 1 game opened 2026-07-31 and kicked off 2026-09-11) and the
# kickoff is in `startTime`. Filtering on the open date therefore returns games
# by when they were listed, so every game whose market opened before the window
# was silently invisible: that is how NFL Week 1 (13 games on 2026-09-13) went
# missing from the 2026-09 window entirely. `start_time_*` filters on kickoff
# and is the correct question; the open-date sweep is kept and unioned in
# because it costs little and catches anything gamma mis-stamps.
# PAGE is 10, not gamma's cap of 100, because a game event carries every prop
# market it ever listed (~300-380 of them) and the payload is what times out,
# not the number of events. Measured on these windows:
#     cfb, 2-day slice   limit=100 -> 46MB / 20s      limit=25 -> 13MB / 6.5s
#     nfl, 2-day slice   limit=25  -> 16MB / 34.7s    limit=10 -> 10MB / 7.6s
# check.get runs curl with --max-time 30, so the NFL page at 25 fails outright
# and the CFB page at 100 sits right on the edge and failed intermittently.
# That is the other half of the coverage gap, and it predates the kickoff fix.
# 10 keeps every observed page under ~10s with room to spare.
PAGE = 10
GAMMA_KICK = ("https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=true"
              f"&limit={PAGE}&offset={{off}}"
              "&start_time_min={lo}T00:00:00Z&start_time_max={hi}T00:00:00Z")
GAMMA_OPEN = ("https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=true"
              f"&limit={PAGE}&offset={{off}}&start_date_min={{lo}}&start_date_max={{hi}}")
SUB = re.compile(r"-(player-props|inning-\d+-winner|first-five-winner|first-half-winner|quarter-\d-winner|props)$")


def fetch(tmpl, tag, lo, hi, holes):
    """Every page of one slice. A slice that cannot be read is recorded in
    `holes` rather than silently returning short, because a dropped slice is
    indistinguishable from a quiet week and that is how coverage gaps hide."""
    out, off = [], 0
    while True:
        got = B.get(tmpl.format(tag=tag, off=off, lo=lo, hi=hi))
        if got is None:
            holes.append({"tag": tag, "lo": lo, "hi": hi, "offset": off, "why": "request failed"})
            print(f"  !! {tag} {lo}..{hi} offset {off} FAILED — slice incomplete", flush=True)
            return out
        out += got
        if len(got) < PAGE:
            return out
        off += PAGE
        if off >= 2000:
            holes.append({"tag": tag, "lo": lo, "hi": hi, "offset": off, "why": "offset cap"})
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
    evs, holes = {}, []
    for tag in tags:
        for tmpl, step, pad, what in ((GAMMA_KICK, 2, 1, "kickoff"), (GAMMA_OPEN, 5, 3, "open-date")):
            cur = lo - timedelta(days=pad)
            while cur < hi + timedelta(days=pad):
                nxt = cur + timedelta(days=step)
                for e in fetch(tmpl, tag, cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d"), holes):
                    e.setdefault("_tag", tag)
                    evs[e["slug"]] = e
                cur = nxt
            print(f"  {tag} by {what}: {len(evs)} events so far", flush=True)
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
               "tags": tags, "games": games, "holes": holes,
               "requests": dict(B.STATS)}, open(os.path.join(out_dir, "slate.json"), "w"))
    if holes:
        print(f"  !! {len(holes)} slice(s) INCOMPLETE — this slate is partial, see slate.json holes",
              flush=True)
    print(f"{len(games)} games · {sum(len(g['markets']) for g in games)} main markets ({settled} closed) · "
          f"{dict((l, sum(1 for g in games if g['league'] == l)) for l in ('NFL', 'NCAAF'))} · "
          f"{B.STATS['ok']} ok / {B.STATS['fail']} failed · {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
