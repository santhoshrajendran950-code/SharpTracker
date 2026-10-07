#!/usr/bin/env python3
"""
Stage 1, generalized — every closed game/match/fight in a window, for ANY
sport in lib/sport_config.SPORTS. hist_slate.py (football-only) is untouched
and still runs the in-flight NFL/CFB season; this is the multi-sport sibling.

    python3 hist_slate2.py mlb 2025-10-01 2025-10-31
    python3 hist_slate2.py mma 2025-10-01 2025-10-31    # per-fight kickoffs

Writes windows/<sport>/<ym>/slate.json in the SAME shape hist_slate.py does,
so hist_discover / hist_prior / hist_adapt / the frozen bt_rebuild / bt_score
need no changes: {"games": [{slug, title, start, league, markets:[...]}]}.

h2h sports (mma/boxing/golf/tennis) get ONE GAME PER MARKET — a UFC card is
one gamma event but each fight has its own independent kickoff, and pricing a
6-fight card at one T-60 would price 5 fights at the wrong time. Each such
game's slug is "<event_slug>__m<i>" and carries exactly one market.

Every market is stamped with `market_type` (moneyline/spread/total/draw) from
sport_config so later analysis can split without re-deriving it.
"""
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import btlib as B
import sport_config as SC

GAMMA = ("https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=true"
         "&limit=100&offset={off}&start_date_min={lo}&start_date_max={hi}")


def fetch(tag, lo, hi):
    out, off, capped = [], 0, False
    while True:
        got = B.get(GAMMA.format(tag=tag, off=off, lo=lo, hi=hi)) or []
        out += got
        if len(got) < 100:
            return out, capped
        off += 100
        if off >= 2000:
            capped = True
            return out, capped


def fetch_all_no_dates(tag):
    """For NEEDS_FULL_FETCH tags: closed=true has no queryable event date at
    all, so the whole tag history is pulled once and cached (25 MMA events,
    258 boxing — small) and every window filters by market gameStartTime."""
    cache = os.path.join(HERE, f".fullfetch_{tag}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    out, off = [], 0
    while True:
        got = B.get(f"https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=true&limit=100&offset={off}") or []
        out += got
        if len(got) < 100 or off >= 2000:
            break
        off += 100
    json.dump(out, open(cache, "w"))
    return out


def parse_ts(s):
    """gameStartTime comes as '2025-10-03 05:00:00+00' — space separator and
    a bare +00 offset, neither of which datetime.fromisoformat accepts on
    Python 3.9 (production's interpreter). startTime/startDate are proper
    ISO with a trailing Z. Handles both."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if re.search(r"[+-]\d{2}$", s):
        s += ":00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def main():
    a = sys.argv[1:]
    tag, start, end = a[0], a[1], a[2]
    if tag not in SC.SPORTS:
        print(f"unknown sport tag {tag!r} — known: {', '.join(SC.SPORTS)}")
        return 1
    league, classify, kw = SC.SPORTS[tag]
    ym = start[:7]
    out_dir = os.path.join(HERE, "windows", tag, ym)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    lo = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    hi = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    evs, capped_any = {}, False
    if tag in SC.NEEDS_FULL_FETCH:
        for e in fetch_all_no_dates(tag):
            evs[e["slug"]] = e
    else:
        cur = lo - timedelta(days=3)
        while cur < hi + timedelta(days=3):
            nxt = cur + timedelta(days=5)
            got, capped = fetch(tag, cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d"))
            capped_any = capped_any or capped
            for e in got:
                evs[e["slug"]] = e
            cur = nxt

    games = []
    for slug, e in evs.items():
        est = parse_ts(e.get("startTime"))
        mk = list(classify(e, **kw))
        if not mk:
            continue
        h2h = classify in (SC._h2h_markets,)
        if h2h:
            # one game per market — independent kickoffs (a fight card, a
            # tennis day, a golf major's round of matchups)
            for i, (m, kick_override) in enumerate(mk):
                st = parse_ts(kick_override) or est
                if not st or not (lo <= st < hi + timedelta(days=1)):
                    continue
                q = (m.get("question") or "").strip()
                games.append({"slug": f"{slug}__m{i}", "title": q, "start": st.isoformat(),
                             "league": league, "markets": [{"question": q, "conditionId": m.get("conditionId"),
                             "outcomes": m.get("outcomes"), "clobTokenIds": m.get("clobTokenIds"),
                             "outcomePrices": m.get("outcomePrices"), "closed": m.get("closed"),
                             "market_type": SC.market_type(q, league)}]})
        else:
            if not est or not (lo <= est < hi + timedelta(days=1)):
                continue
            games.append({"slug": slug, "title": e.get("title"), "start": est.isoformat(), "league": league,
                         "markets": [{"question": (m.get("question") or "").strip(), "conditionId": m.get("conditionId"),
                                      "outcomes": m.get("outcomes"), "clobTokenIds": m.get("clobTokenIds"),
                                      "outcomePrices": m.get("outcomePrices"), "closed": m.get("closed"),
                                      "market_type": SC.market_type((m.get("question") or "").strip(), league)}
                                     for m, _ in mk]})
    games.sort(key=lambda g: g["start"])
    settled = sum(1 for g in games for m in g["markets"] if m.get("closed"))
    json.dump({"built": datetime.now(timezone.utc).isoformat(), "start": start, "end": end, "tag": tag,
               "league": league, "games": games, "capped": capped_any,
               "requests": dict(B.STATS)}, open(os.path.join(out_dir, "slate.json"), "w"))
    print(f"{tag} {ym}: {len(games)} games · {sum(len(g['markets']) for g in games)} markets ({settled} closed) · "
          f"{'CAPPED — partial' if capped_any else 'complete'} · {B.STATS['ok']} ok / {B.STATS['fail']} failed · {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
