#!/usr/bin/env python3
"""
Stage 2 — settled sports slate for the test window.

Same tags and league mapping as check.py, but closed=true and looking backwards.

Two traps, both of which silently corrupt the whole backtest rather than failing:

  * `startDate` is when the MARKET OPENED, not kickoff. For
    mlb-sd-col-2026-09-14-player-props it is 2026-09-14T15:24Z while the game
    started 2026-09-15T00:40Z — nine hours and a calendar day apart. Kickoff
    comes from the event's `startTime`, falling back to the market's
    `gameStartTime`. Getting this wrong would rebuild every position at the
    wrong moment.
  * gamma's `offset` caps at 2000, so a bare closed=true query cannot reach 30
    days of MLB. Queries are therefore bounded with start_date_min/max — padded
    by PAD_DAYS on each side, because startDate can precede kickoff — and the
    real kickoff filter is applied client-side afterwards.
"""
import sys, time
from datetime import datetime, timezone, timedelta

import btlib as B
chk = B.chk

WINDOW_DAYS = 30
PAD_DAYS = 3          # startDate can precede kickoff; pad the server-side filter
PAGE, OFFSET_CAP = 100, 2000
MAX_SPLIT = 6         # 30d -> <12h slices; far past what any tag needs


def parse_ts(raw):
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00").replace(" ", "T")
    if len(s) >= 3 and (s[-3] == "+" or s[-3] == "-") and ":" not in s[-3:]:
        s += ":00"                     # gameStartTime ends "+00", not "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def kickoff(e):
    """Real kickoff — never startDate."""
    t = parse_ts(e.get("startTime"))
    if t:
        return t
    for m in e.get("markets") or []:
        t = parse_ts(m.get("gameStartTime"))
        if t:
            return t
    return None


def league_for(tag, slug):
    if tag == "tennis":
        return ("WTA" if slug.startswith("wta-")
                else "ATP" if slug.startswith("atp-") else "TENNIS")
    return {"cfb": "NCAAF", "cbb": "NCAAB", "golf": "PGA"}.get(tag, tag.upper())


def plist(v):
    import json
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def fetch_window(tag, lo_s, hi_s):
    """One server-side date window, paged. Returns (events, hit_cap)."""
    out = []
    base = ("https://gamma-api.polymarket.com/events?closed=true"
            f"&tag_slug={tag}&start_date_min={lo_s}&start_date_max={hi_s}")
    for off in range(0, OFFSET_CAP, PAGE):
        got = B.get(f"{base}&limit={PAGE}&offset={off}")
        if got is None:
            print(f"  !! {tag} {lo_s}..{hi_s}: request FAILED at offset {off}", flush=True)
            return out, False, True
        out.extend(got)
        if len(got) < PAGE:
            return out, False, False
    return out, True, False          # filled every page -> assume capped


def fetch_tag(tag, lo, hi, depth=0):
    """All closed events for one tag. `lo`/`hi` are QUERY bounds, already
    padded by the caller — padding inside the recursion would make every split
    half overlap its sibling by 2*PAD_DAYS and the cap would never clear.

    gamma's offset caps at 2000, which 30 days of MLB blows straight through.
    When a window fills every page it is split in half by DATE and each half is
    fetched separately — the same shape as the /activity 5000-offset workaround.
    Without this the tag comes back silently truncated, which reads downstream as
    'those games had no sharp money' rather than as missing data.
    """
    lo_s, hi_s = lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")
    evs, capped, failed = fetch_window(tag, lo_s, hi_s)
    if not capped:
        return evs, failed
    span = (hi - lo).total_seconds()
    if depth >= MAX_SPLIT or span <= 86400:
        print(f"  !! {tag}: still capped at {lo_s}..{hi_s} after {depth} splits "
              f"— PARTIAL", flush=True)
        return evs, True
    mid = lo + timedelta(seconds=span / 2)
    a, fa = fetch_tag(tag, lo, mid, depth + 1)
    b, fb = fetch_tag(tag, mid, hi, depth + 1)
    return a + b, fa or fb


def main():
    t0 = time.time()
    now = datetime.now(timezone.utc)
    hi, lo = now, now - timedelta(days=WINDOW_DAYS)
    print(f"SLATE · settled sports · {lo:%Y-%m-%d} .. {hi:%Y-%m-%d} "
          f"({WINDOW_DAYS}d)", flush=True)

    slate, capped_tags, seen_ev = {}, [], 0
    for tag in chk.TAGS:
        evs, partial = fetch_tag(tag, lo - timedelta(days=PAD_DAYS),
                                 hi + timedelta(days=PAD_DAYS))
        seen_ev += len(evs)
        if partial:
            capped_tags.append(tag)
        kept = 0
        for e in evs:
            slug = e.get("slug")
            if not slug or slug in slate:
                continue
            ko = kickoff(e)
            if not ko or not (lo <= ko <= hi):
                continue
            mkts = []
            for m in e.get("markets") or []:
                names = plist(m.get("outcomes"))
                prices = plist(m.get("outcomePrices"))
                toks = plist(m.get("clobTokenIds"))
                if not names or len(names) != len(toks):
                    continue
                try:
                    prices = [float(x) for x in prices]
                except (TypeError, ValueError):
                    prices = []
                mkts.append({
                    "question": m.get("question") or "",
                    "outcomes": names, "prices": prices, "tokens": toks,
                    "closed": bool(m.get("closed")),
                })
            if not mkts:
                continue
            slate[slug] = {
                "league": league_for(tag, slug), "title": e.get("title") or slug,
                "kickoff": ko.isoformat(), "kickoff_ts": int(ko.timestamp()),
                "tag": tag, "markets": mkts,
            }
            kept += 1
        print(f"  {tag:7} {len(evs):>5} closed events -> {kept:>4} in window", flush=True)

    n_settled = sum(1 for s in slate.values()
                    if any(m["prices"] and max(m["prices"]) >= 0.9 for m in s["markets"]))
    print(f"\n{len(slate)} events in window · {n_settled} have at least one "
          f"cleanly settled market", flush=True)
    if capped_tags:
        print(f"!! offset cap hit on: {', '.join(capped_tags)} — slate is PARTIAL", flush=True)

    stats = B.report_stats("slate")
    B.save("bt_slate.json", {
        "built": now.isoformat(), "window_days": WINDOW_DAYS,
        "lo": lo.isoformat(), "hi": hi.isoformat(),
        "capped_tags": capped_tags, "events_seen": seen_ev,
        "n_events": len(slate), "n_settled": n_settled,
        "requests": stats, "slate": slate,
    })
    print(f"done in {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
