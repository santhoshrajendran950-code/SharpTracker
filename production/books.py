#!/usr/bin/env python3
"""
Live sportsbook prices for board rows, via The Odds API.

The API returns every game for a sport+market+region in ONE request, so a whole
slate costs a couple of credits rather than one per game. Polymarket itself is
carried in the feed (region us_ex), which means book prices and the reference
price arrive already matched to the same event — no fuzzy join needed between
this and the Polymarket scan.

Key is read via oddskey.py (keychain) and never printed.
"""
import json, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from oddskey import get_key

BASE = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
# books Santhosh can actually bet, plus the low-vig references
# Santhosh can bet legal US books AND non-Kalshi/non-Polymarket prediction
# markets — Novig, ProphetX and BetOpenly are peer-to-peer exchanges, so they
# belong in the bettable set, not the reference set. Only Kalshi and Polymarket
# are reference-only.
MY_BOOKS = ["draftkings", "fanduel", "fanatics", "betmgm", "williamhill_us",
            "betrivers", "novig", "prophetx", "betopenly"]
REFS = ["polymarket", "kalshi"]
REFS_LABELS = {"PM", "KAL"}      # reference-only: Santhosh cannot bet these
BOOK_LABEL = {"draftkings": "DK", "fanduel": "FD", "fanatics": "FAN",
              "betmgm": "MGM", "williamhill_us": "CZR", "betrivers": "BR",
              "novig": "NOVIG", "prophetx": "PX", "betopenly": "OPENLY",
              "polymarket": "PM", "kalshi": "KAL"}

# Soccer is per-COMPETITION on the Odds API, and the board only ever says
# "SOCCER" — but the Polymarket slug carries the league as its prefix
# (sea-mon-sas-…, epl-bre-che-…), so that is what picks the sport key. Without
# this every soccer row came back "no book prices matched", which reads as
# "no value available" when it actually means "never asked".
SOCCER_PREFIX = {
    "epl": "soccer_epl",            "elc": "soccer_efl_champ",
    "sea": "soccer_italy_serie_a",  "seb": "soccer_italy_serie_b",
    "lal": "soccer_spain_la_liga",  "la2": "soccer_spain_segunda_division",
    "bun": "soccer_germany_bundesliga", "bu2": "soccer_germany_bundesliga2",
    "fr1": "soccer_france_ligue_one",   "fr2": "soccer_france_ligue_two",
    "ucl": "soccer_uefa_champs_league", "uel": "soccer_uefa_europa_league",
    "mls": "soccer_usa_mls",        "scop": "soccer_spl",
    "por": "soccer_portugal_primeira_liga", "ned": "soccer_netherlands_eredivisie",
}


def soccer_keys(slug):
    """Competition sport-key(s) for a Polymarket soccer slug."""
    pre = (slug or "").split("-")[0].lower()
    k = SOCCER_PREFIX.get(pre)
    return [k] if k else list(dict.fromkeys(SOCCER_PREFIX.values()))


SPORT_FOR = {
    "NFL": ["americanfootball_nfl"],
    "NCAAF": ["americanfootball_ncaaf"],
    "MLB": ["baseball_mlb"],
    "NCAAB": ["basketball_ncaab"],
    "NBA": ["basketball_nba"],
    "WNBA": ["basketball_wnba"],
    "NHL": ["icehockey_nhl"],
    "PGA": ["golf_pga_championship_winner"],
}
_TENNIS_CACHE = {"keys": None}
_cache, _quota = {}, {"remaining": None, "used": None}


def _curl(url):
    p = subprocess.run(["curl", "-s", "-D", "-", "--max-time", "30", url],
                       capture_output=True, text=True)
    head, _, body = p.stdout.partition("\r\n\r\n")
    if not body:
        head, _, body = p.stdout.partition("\n\n")
    m = re.search(r"x-requests-remaining:\s*(\d+)", head, re.I)
    if m:
        _quota["remaining"] = int(m.group(1))
    m = re.search(r"x-requests-used:\s*(\d+)", head, re.I)
    if m:
        _quota["used"] = int(m.group(1))
    try:
        return json.loads(body)
    except ValueError:
        return None


def tennis_keys(key):
    """Tennis is split into many per-tournament sport keys; find the live ones."""
    if _TENNIS_CACHE["keys"] is None:
        d = _curl(f"https://api.the-odds-api.com/v4/sports/?apiKey={key}")
        _TENNIS_CACHE["keys"] = [s["key"] for s in (d or [])
                                 if s.get("active") and s["key"].startswith("tennis_")]
    return _TENNIS_CACHE["keys"]


def fetch(sport, market, key):
    """All games for one sport+market across both regions. ~2 credits."""
    ck = (sport, market)
    if ck in _cache:
        return _cache[ck]
    url = (BASE.format(sport=sport) + f"?apiKey={key}&regions=us,us_ex"
           f"&markets={market}&oddsFormat=american")
    _cache[ck] = _curl(url) or []
    return _cache[ck]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def team_match(side, name):
    """'Steelers' vs 'Pittsburgh Steelers' -> True."""
    a, b = norm(side), norm(name)
    return bool(a) and bool(b) and (a in b or b in a)


def market_for(h):
    mkt = h.get("market") or ""
    side = h.get("side") or ""
    if side in ("Over", "Under") or re.search(r"O/U|Total", mkt, re.I):
        return "totals"
    if re.match(r"(?:1H\s+)?Spread:", mkt):
        return "spreads"
    return "h2h"


def prices_for(h, key):
    """[(book_label, american_odds)] for the row's side, best first."""
    leagues = SPORT_FOR.get(h["league"])
    if h["league"] == "SOCCER":
        sports = soccer_keys(h.get("slug"))
    else:
        sports = leagues if leagues else tennis_keys(key)
    market = market_for(h)
    _parts = re.split(r"\s+vs\.?\s+", h.get("game") or "", maxsplit=1)
    want_a = _parts[0] if _parts else ""
    want_b = _parts[1] if len(_parts) > 1 else ""
    start = h["start"]

    for sport in sports:
        for ev in fetch(sport, market, key):
            try:
                ct = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            except Exception:
                continue
            if abs((ct - start).total_seconds()) > 3 * 3600:
                continue
            pair = (ev.get("home_team"), ev.get("away_team"))
            if not (any(team_match(want_a, p) for p in pair)
                    and any(team_match(want_b, p) for p in pair)):
                continue
            out = []
            for bk in ev.get("bookmakers", []):
                if bk["key"] not in MY_BOOKS + REFS:
                    continue
                for mk in bk.get("markets", []):
                    for o in mk.get("outcomes", []):
                        nm = o.get("name") or ""
                        ok = (nm == h["side"] or team_match(h["side"], nm)
                              or (h["side"] in ("Over", "Under") and nm == h["side"]))
                        if not ok:
                            continue
                        # totals/spreads must match the line too
                        pt = o.get("point")
                        if pt is not None:
                            m = re.search(r"(-?\d+\.?\d*)\s*$", h.get("market") or "")
                            if m and abs(abs(float(m.group(1))) - abs(pt)) > 0.01:
                                continue
                        out.append((BOOK_LABEL.get(bk["key"], bk["key"]), int(o["price"])))
            if out:
                seen, ded = set(), []
                for lbl, px in out:
                    if lbl not in seen:
                        seen.add(lbl)
                        ded.append((lbl, px))
                ded.sort(key=lambda t: -t[1])
                return ded
    return []


def quota():
    return _quota


if __name__ == "__main__":
    k = get_key()
    d = _curl(f"https://api.the-odds-api.com/v4/sports/?apiKey={k}")
    print(f"ok — {len(d or [])} sports, quota remaining {_quota['remaining']}")
