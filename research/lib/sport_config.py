#!/usr/bin/env python3
"""
Per-sport market shape, so hist_slate.py can pull more than football.

Three classifiers, found by probing gamma directly on 2026-09-23 (see
SPORT_NOTES.md):

  team        moneyline = the event's own title as a market question;
              "Spread: X (-N)" and "{title}: O/U N" when present.
              Kickoff = event startTime. mlb/nba/nhl/college-basketball/
              soccer/cricket/rugby/wnba/esports/nfl/cfb.
  soccer3way  three Yes/No markets ("Will X win on DATE?" x2, "...end in a
              draw?") instead of one two-outcome market, + the same O/U
              totals. Same kickoff source. soccer only.
  h2h         one market per matchup, question = "A vs B", 2 outcomes, no
              spread/total. Kickoff is per-MARKET (startDate), not per-event
              — an MMA card or a golf major has one event with many
              independently-timed matchups. mma/boxing/golf(-matchup only)/
              tennis (tennis ALSO gets a "Match O/U N.5" total, unlike the
              other h2h sports).

Excluded, and why (checked before adding, not assumed):
  golf (outright)  the tag mostly serves tournament-winner futures; only the
                   "<Event>: A vs. B" matchup markets are used, filtered by title.
  f1, nascar       every market is a full-field outright with no head-to-head
                   contract at all — structurally excluded, not thin.
  nfl draft, awards, conference/tournament winners  filtered by title pattern
                   (no " vs" / " vs." with two proper names) inside hist_slate.
"""
import re

TEAM = re.compile(r"^Spread: .+\([+-][\d.]+\)$")


def _kick(m):
    """gameStartTime is the real kickoff ('2025-10-03 05:00:00+00'); startDate
    is when the MARKET WAS LISTED (often hours or days earlier) and createdAt
    is worse still. Found 2026-09-23: a tennis market's startDate read
    04:01 AM while its gameStartTime read the correct 5:00 AM match time."""
    return m.get("gameStartTime")


def _team_markets(ev):
    title = (ev.get("title") or "").strip()
    for m in ev.get("markets", []):
        q = (m.get("question") or "").strip()
        if q == title or TEAM.match(q) or re.match(rf"^{re.escape(title)}: O/U [\d.]+$", q):
            if m.get("clobTokenIds") and m.get("outcomePrices"):
                yield m, _kick(m)      # falls back to event startTime if None


def _threeway_markets(ev):
    """Soccer AND rugby: three Yes/No markets instead of one two-outcome one.
    Wording differs by sport — soccer: 'Will X win on 2026-02-14?' / 'Will A vs
    B end in a draw?'; rugby: 'Will X win?' / 'Will the match end in a draw?'
    (found 2026-09-23 — rugby was wrongly assigned the football-style
    classifier and every rugby row silently dropped as a result)."""
    title = (ev.get("title") or "").strip()
    parts = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
    if len(parts) != 2:
        return
    for m in ev.get("markets", []):
        q = (m.get("question") or "").strip()
        is_ml = bool(re.match(rf"^Will ({re.escape(parts[0])}|{re.escape(parts[1])}) win"
                              rf"(?: on \d{{4}}-\d{{2}}-\d{{2}})?\??$", q))
        is_draw = bool(re.match(r"^Will (the match|" + re.escape(title) + r") end in a draw\??$", q))
        is_total = bool(re.match(rf"^{re.escape(title)}: O/U [\d.]+$", q))
        if (is_ml or is_draw or is_total) and m.get("clobTokenIds") and m.get("outcomePrices"):
            yield m, _kick(m)


MINOR_TOUR = re.compile(r"^(M15|M25|M35|W15|W25|W35|W50|W75|W100|ITF|Challenger|UTR|WTA 125)\b"
                        r"|Juniors|Doubles|Qualification|Qualifying", re.I)


def _h2h_markets(ev, allow_totals=False):
    """FIXED 2026-09-23: was `"vs" in question`, which also matched "Set 1
    Winner: X vs Y", "Completed Match: X vs Y" and set-level totals ("X vs Y:
    Set 1 Games O/U 8.5") — all of which contain the string "vs" too. A tennis
    month came back with 111,276 "games" instead of the real few hundred.
    The moneyline is the market whose question IS the event title, exactly
    the same rule _team_markets uses."""
    title = (ev.get("title") or "").strip()
    if MINOR_TOUR.search(title):    # juniors/doubles/quals/ITF-graded: same filter production uses
        return
    for m in ev.get("markets", []):
        q = (m.get("question") or "").strip()
        is_total = allow_totals and bool(re.search(r"Match O/U [\d.]+", q))
        is_ml = q == title
        if (is_ml or is_total) and m.get("clobTokenIds") and m.get("outcomePrices"):
            yield m, _kick(m)


# Event-level start-date filtering silently returns ZERO for these tags (no
# queryable event date at all), so a full fetch + client-side date filter is
# available for a future sport that needs it (NEEDS_FULL_FETCH), but as of
# 2026-09-23 nothing in SPORTS uses it — see the MMA/BOXING exclusion below.
NEEDS_FULL_FETCH = set()

SPORTS = {
    # tag_slug: (display, classifier, kwargs)
    "mlb":  ("MLB", _team_markets, {}),
    "nba":  ("NBA", _team_markets, {}),
    "nhl":  ("NHL", _team_markets, {}),
    "college-basketball": ("NCAAB", _team_markets, {}),
    "cricket": ("CRICKET", _team_markets, {}),
    "wnba":    ("WNBA", _team_markets, {}),
    "esports": ("ESPORTS", _team_markets, {}),
    "soccer":  ("SOCCER", _threeway_markets, {}),
    "rugby":   ("RUGBY", _threeway_markets, {}),
    "tennis":  ("TENNIS", _h2h_markets, {"allow_totals": True}),
}

# f1, nascar: excluded structurally — every market is a full-field outright,
# no head-to-head contract exists to grade at all.
#
# mma, boxing, golf: excluded 2026-09-23 after checking, not assumed. Neither
# the event NOR any market carries a usable kickoff field for these three —
# gameStartTime is None on every row checked (including golf's real "A vs. B"
# Masters matchup markets, confirmed to classify correctly otherwise), event
# startTime is None, and the only timestamps left (startDate, createdAt) are
# when the market was LISTED, which can be hours to days before the actual
# fight/round and would systematically mistime the T-60 snapshot. Also small
# even if it worked: 25 MMA events and 258 boxing events in Polymarket's
# entire history (cached in .fullfetch_{tag}.json where that was tried).

MARKET_TYPE = re.compile(r"O/U [\d.]+")


def market_type(question, sport):
    if MARKET_TYPE.search(question):
        return "total"
    if TEAM.match(question):
        return "spread"
    if sport == "SOCCER" and re.search(r"end in a draw", question, re.I):
        return "draw"
    return "moneyline"
