#!/usr/bin/env python3
"""
"live board": today's emailed sharp plays beside the live score of their game.

    python3 liveboard.py            # readable lines
    python3 liveboard.py --wide     # pipe-delimited rows, one per play
    python3 liveboard.py --json     # the same rows as JSON

A play is anything already emailed: Sharp Alert (the `alerted` ledger in
check-alert-snapshot.json) or Rule-based (ruleplay.jsonl). Only the competitions
in COMPS are shown. A game is shown if it starts today, local time, or is still
in progress from last night.

This reads local files and ESPN's public scoreboard. It never sweeps wallets and
takes no sweep lock, so it cannot compete with a production run, and it writes
nothing. ESPN's feed is unofficial, so a game that cannot be matched with
confidence prints "score unavailable" rather than a guessed score.
"""
import json, os, re, subprocess, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ALERT_SNAP = os.path.join(HERE, "check-alert-snapshot.json")
RULE_LEDGER = os.path.join(HERE, "ruleplay.jsonl")
SIGNALS = os.path.join(HERE, "signals.jsonl")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day}&limit=500{extra}"

# Polymarket slug prefix -> (label, ESPN path, extra query). College feeds return
# only ranked or featured games unless the division group is named.
COMPS = {
    "nba": ("NBA", "basketball/nba", ""),
    "cbb": ("NCAAB", "basketball/mens-college-basketball", "&groups=50"),
    "ncaab": ("NCAAB", "basketball/mens-college-basketball", "&groups=50"),
    "nfl": ("NFL", "football/nfl", ""),
    "cfb": ("NCAAF", "football/college-football", "&groups=80"),
    "nhl": ("NHL", "hockey/nhl", ""),
    "ucl": ("UCL", "soccer/uefa.champions", ""),
    "epl": ("Premier League", "soccer/eng.1", ""),
    "lal": ("La Liga", "soccer/esp.1", ""),
    "sea": ("Serie A", "soccer/ita.1", ""),
    "atp": ("ATP", "tennis/atp", ""),
}
TENNIS, SOCCER = {"ATP"}, {"UCL", "Premier League", "La Liga", "Serie A"}
LOOKBACK = timedelta(hours=8)    # a game from last night can still be running
MAX_START_GAP = timedelta(hours=6)
MIN_NAME = 0.8
_CLUB = {"fc", "cf", "sc", "afc", "ac", "ssc", "bv", "cd", "rc", "as", "us", "ud",
         "rcd", "vfb", "vfl", "tsg", "sv", "club", "the"}
SPREAD = re.compile(r"^Spread:\s*(.+?)\s*\(([+-][\d.]+)\)\s*$")
WILL_WIN = re.compile(r"^Will (.+?) win\b", re.I)
DRAW = re.compile(r"end in a draw", re.I)
OU = re.compile(r"O/U\s+([\d.]+)\s*$")
PERIOD = re.compile(r"^(\dH|\dQ)\s|\b(1st|2nd|first|second) half\b|\bset \d\b", re.I)


# ---- names and matching -------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(w for w in s.split() if w not in _CLUB and not w.isdigit())


def name_score(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    x, y = set(a.split()), set(b.split())
    if x <= y or y <= x:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def best_name(q, names):
    return max((name_score(q, n) for n in names), default=0.0)


def teams_of(game):
    """'Open: A vs B' -> ['A', 'B']."""
    t = (game or "").split(": ", 1)[-1]
    parts = re.split(r"\s+vs\.?\s+", t, maxsplit=1)
    return [p.strip() for p in parts] if len(parts) == 2 else []


def side_of(name, teams):
    """Index of the team `name` refers to, or None if it is not clearly one."""
    scores = [best_name(name, [t]) for t in teams]
    if len(scores) != 2 or max(scores) < MIN_NAME or scores[0] == scores[1]:
        return None
    return 0 if scores[0] > scores[1] else 1


# ---- ESPN ---------------------------------------------------------------------
def _parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _names(c):
    t, a = c.get("team") or {}, c.get("athlete") or {}
    return [x for x in (t.get("displayName"), t.get("shortDisplayName"), t.get("name"),
                        t.get("location"), t.get("nickname"), a.get("displayName"),
                        a.get("fullName")) if x]


def _contest(comp, ev):
    """One ESPN competition -> the fields this board uses."""
    cs = comp.get("competitors") or []
    if len(cs) != 2:
        return None
    st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
    start = _parse_ts(comp.get("startDate") or comp.get("date") or ev.get("date"))
    out = {"start": start, "state": st.get("state") or "pre",
           "detail": st.get("shortDetail") or st.get("detail") or "", "sides": []}
    for c in cs:
        ls = c.get("linescores") or []
        out["sides"].append({
            "names": _names(c), "score": c.get("score"), "winner": c.get("winner"),
            "sets": [{"games": x.get("value"), "won": x.get("winner"),
                      "tiebreak": x.get("tiebreak")} for x in ls]})
    return out


def parse_scoreboard(js, tennis=False):
    out = []
    for ev in (js or {}).get("events") or []:
        if tennis:
            for g in ev.get("groupings") or []:
                # Men's Singles only: Polymarket's ATP slate has no doubles matches.
                if "singles" not in ((g.get("grouping") or {}).get("displayName") or "").lower():
                    continue
                out += [c for c in (_contest(x, ev) for x in g.get("competitions") or []) if c]
        else:
            out += [c for c in (_contest(x, ev) for x in ev.get("competitions") or []) if c]
    return out


def fetch(path, day, extra, tennis):
    """Contests for one feed and day, or None if the feed could not be read."""
    url = ESPN.format(path=path, day=day, extra=extra)
    p = subprocess.run(["curl", "-s", "--max-time", "15", "-w", "\n%{http_code}", url],
                       capture_output=True, text=True)
    body, _, code = p.stdout.rpartition("\n")
    if code != "200":
        return None
    try:
        return parse_scoreboard(json.loads(body), tennis)
    except ValueError:
        return None


def match(play, contests):
    """(contest, index of teams[0] within it), or None unless one match is clear."""
    teams = teams_of(play["game"])
    if len(teams) != 2:
        return None
    found = []
    for c in contests:
        if c["start"] and play["start"] and abs(c["start"] - play["start"]) > MAX_START_GAP:
            continue
        for first in (0, 1):
            a = best_name(teams[0], c["sides"][first]["names"])
            b = best_name(teams[1], c["sides"][1 - first]["names"])
            if a >= MIN_NAME and b >= MIN_NAME:
                found.append((a + b, c, first))
    if not found:
        return None
    found.sort(key=lambda x: -x[0])
    if len(found) > 1 and found[0][0] == found[1][0] and found[0][1] is not found[1][1]:
        return None                  # two different games fit equally well
    return found[0][1], found[0][2]


# ---- status -------------------------------------------------------------------
def _n(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _g(x):
    return f"{x:g}"


def _decided(v, state):
    """v > 0 the play is ahead; on a final, that is the result."""
    if state == "post":
        return "Won" if v > 0 else "Lost" if v < 0 else "Push"
    return None


def status(play, c, first):
    """A short winning/losing line for the play against contest `c`."""
    state = c["state"]
    if state == "pre":
        return "Not started"
    mkt, side, league = (play.get("market") or "").strip(), (play.get("side") or "").strip(), play["league"]
    if state == "post" and league in SOCCER and re.search(r"AET|ET\b|pen", c["detail"], re.I):
        return "Went past 90 min, check settlement"
    teams = teams_of(play["game"])
    a, b = c["sides"][first], c["sides"][1 - first]        # a = teams[0], b = teams[1]
    tennis = league in TENNIS
    sa, sb = _n(a["score"]), _n(b["score"])
    if tennis:
        sets_a = sum(1 for x in a["sets"] if x["won"])
        sets_b = sum(1 for x in b["sets"] if x["won"])
    elif sa is None or sb is None:
        return "Status unavailable"
    if PERIOD.search(mkt):
        return "Status unavailable (period market)"

    m = OU.search(mkt)
    if side in ("Over", "Under") and m:
        if not (mkt.startswith(play["game"]) or mkt.lower().startswith("match o/u")):
            return "Status unavailable (prop)"
        line = float(m.group(1))
        total = (sum(x["games"] or 0 for s in (a, b) for x in s["sets"]) if tennis else sa + sb)
        ahead = (total - line) * (1 if side == "Over" else -1)
        d = _decided(ahead, state)
        if d:
            return f"{d} (total {_g(total)})"
        if side == "Over":
            return (f"Over already hit (total {_g(total)})" if ahead > 0
                    else f"Needs {_g(line - total)} more (total {_g(total)})")
        return (f"Under already lost (total {_g(total)})" if ahead < 0
                else f"Under by {_g(line - total)} so far (total {_g(total)})")

    sp = SPREAD.match(mkt)
    if sp:
        if tennis:
            return "Status unavailable (tennis spread)"
        named, line = sp.group(1), float(sp.group(2))
        i = side_of(side, teams)
        j = side_of(named, teams)
        if i is None or j is None:
            return "Status unavailable"
        if i != j:
            line = -line
        mine, theirs = (sa, sb) if i == 0 else (sb, sa)
        v = mine - theirs + line
        d = _decided(v, state)
        if d:
            return d
        return (f"Covering by {_g(v)}" if v > 0 else f"Not covering by {_g(-v)}" if v < 0
                else "On the number")

    if DRAW.search(mkt) and side in ("Yes", "No"):
        level = sa == sb
        won = level if side == "Yes" else not level
        if state == "post":
            return "Won" if won else "Lost"
        return "Winning" if won else "Losing"

    w = WILL_WIN.match(mkt)
    if w and side in ("Yes", "No"):
        i = side_of(w.group(1), teams)
        if i is None:
            return "Status unavailable"
        mine, theirs = (sa, sb) if i == 0 else (sb, sa)
        won = mine > theirs if side == "Yes" else mine <= theirs
        if state == "post":
            return "Won" if won else "Lost"
        return "Winning" if won else "Losing"

    # moneyline: `side` names a team or player
    i = side_of(side, teams)
    if i is None:
        return "Status unavailable"
    if tennis:
        mine, theirs = (sets_a, sets_b) if i == 0 else (sets_b, sets_a)
        if state == "post":
            mw = (a if i == 0 else b).get("winner")
            return "Won" if mw else "Lost"
        if mine == theirs:
            return f"Level at {mine}-{theirs} in sets"
        return f"{'Leads' if mine > theirs else 'Trails'} {mine}-{theirs} in sets"
    mine, theirs = (sa, sb) if i == 0 else (sb, sa)
    v = mine - theirs
    d = _decided(v, state)
    if d:
        return d
    return f"Winning by {_g(v)}" if v > 0 else f"Losing by {_g(-v)}" if v < 0 else "Tied"


def score_text(play, c, first):
    a, b = c["sides"][first], c["sides"][1 - first]
    teams = teams_of(play["game"])
    if play["league"] in TENNIS:
        sets = []
        for x, y in zip(a["sets"], b["sets"]):
            sets.append(f"{_g(x['games'] or 0)}-{_g(y['games'] or 0)}")
        return " ".join(sets) or "0-0"
    return f"{teams[0]} {a.get('score') or 0}, {teams[1]} {b.get('score') or 0}"


# ---- plays --------------------------------------------------------------------
def comp_of(slug):
    return COMPS.get((slug or "").split("-", 1)[0])


def _lines(path):
    try:
        with open(path) as f:
            for line in f:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except FileNotFoundError:
        return


def _start(s):
    try:
        return _parse_ts(s)
    except (TypeError, ValueError):
        return None


def wallet_names():
    """wallet -> board name, in the form signals.jsonl stores it. Empty if unreadable,
    which only means a play may list every logged sharp on that side."""
    try:
        import check as chk
        store = json.load(open(os.path.join(HERE, "wallethist.json"))).get("wallets") or {}
        return {w: chk.short(x["user"], w) for w, x in store.items() if x.get("user")}
    except (OSError, ValueError, ImportError):
        return {}


def load_plays(now):
    """{(slug, market, side): play} for everything emailed that is in scope today."""
    plays = {}
    wanted = {}
    try:
        for k in json.load(open(ALERT_SNAP)).get("alerted") or {}:
            parts = k.split("|", 3)
            if len(parts) == 4 and comp_of(parts[1]):
                wanted.setdefault((parts[1], parts[2], parts[3]), set()).add(parts[0])
    except (OSError, ValueError):
        pass
    if wanted:
        names = wallet_names()
        for r in _lines(SIGNALS):
            k = (r.get("slug"), r.get("market"), r.get("side"))
            if k not in wanted or not r.get("start"):
                continue
            p = plays.get(k)
            if p is None:
                plays[k] = {"slug": k[0], "market": k[1], "side": k[2], "game": r.get("game"),
                            "start": _start(r["start"]), "entry": r.get("entry"),
                            "sharps": {}, "emails": {"Sharp Alert"}, "_alert": r.get("source") == "alert"}
                p = plays[k]
            elif r.get("source") == "alert" and not p["_alert"]:
                p["entry"], p["_alert"] = r.get("entry"), True   # the price the email showed
            # signals.jsonl has names but the alert ledger has wallets. Keep only the
            # wallets that were emailed, unless one has no stored name to compare.
            emailed = [names.get(w) for w in wanted[k]]
            if r.get("user") and (None in emailed or r["user"] in emailed):
                p["sharps"][r["user"]] = {"hold": r.get("hold"), "stake": r.get("stake")}  # latest wins
    for r in _lines(RULE_LEDGER):
        if not comp_of(r.get("slug")):
            continue
        k = (r.get("slug"), r.get("market"), r.get("side"))
        p = plays.get(k)
        if p is None:
            p = plays[k] = {"slug": k[0], "market": k[1], "side": k[2], "game": r.get("game"),
                            "start": _start(r.get("start")), "entry": r.get("entry"),
                            "sharps": {}, "emails": set(), "_alert": False}
        p["emails"].add("Rule-based +conv" if ">=70" in (r.get("title") or "") else "Rule-based")
        for s in r.get("sharps") or [{"user": u} for u in r.get("users") or []]:
            if s.get("user"):
                p["sharps"].setdefault(s["user"], {"hold": s.get("hold"), "stake": s.get("stake")})

    today = now.astimezone().date()
    keep = {}
    for k, p in plays.items():
        if not p["start"]:
            continue
        p["league"] = comp_of(p["slug"])[0]
        p["recent"] = p["start"].astimezone().date() != today and now - p["start"] <= LOOKBACK \
            and p["start"] <= now
        if p["start"].astimezone().date() == today or p["recent"]:
            keep[k] = p
    return keep


def feed_days(plays):
    need = set()
    for p in plays.values():
        path, extra = comp_of(p["slug"])[1:]
        # ESPN files games under its own US date, so read the UTC date as well
        for d in {p["start"].astimezone().strftime("%Y%m%d"),
                  p["start"].astimezone(timezone.utc).strftime("%Y%m%d")}:
            need.add((path, d, extra, p["league"] in TENNIS))
    return need


def play_label(p):
    try:
        import postplays
        t = postplays.play_text(p)
    except Exception:
        t = p.get("side") or ""
    mkt = (p.get("market") or "")
    if p.get("side") in ("Yes", "No"):
        w = WILL_WIN.match(mkt)
        t = (f"{p['side']}: {w.group(1)} to win" if w
             else f"{p['side']}: draw" if DRAW.search(mkt) else f"{p['side']}: {mkt}")
    elif t == p.get("side") and t not in ("Over", "Under"):
        t = f"{t} ML"
    return t


def build(now=None):
    now = now or datetime.now(timezone.utc)
    plays = load_plays(now)
    need = feed_days(plays)
    feeds, failed = {}, set()

    def one(n):
        return n, fetch(*n)

    if need:
        with ThreadPoolExecutor(6) as ex:
            for n, got in ex.map(one, need):
                if got is None:
                    failed.add(n[0])
                else:
                    feeds.setdefault(n[0], []).extend(got)

    import check as chk
    rows = []
    for p in plays.values():
        path = comp_of(p["slug"])[1]
        hit = match(p, feeds.get(path, []))
        row = {"league": p["league"], "game": p["game"], "play": play_label(p),
               "entry": chk.am(p.get("entry")), "email": " + ".join(sorted(p["emails"])),
               "sharps": sorted(({"user": u, **s} for u, s in p["sharps"].items()),
                                key=lambda s: -(s.get("stake") or 0)),
               "start": p["start"].isoformat(),
               "time": p["start"].astimezone().strftime("%-I:%M %p")}
        if hit:
            c, first = hit
            row.update(state=c["state"], score=score_text(p, c, first),
                       clock=c["detail"], status=status(p, c, first))
        else:
            row.update(state="unknown", clock="", score="score unavailable",
                       status="Feed down" if path in failed else "Status unavailable")
        if p["recent"] and row["state"] != "in":
            continue                 # last night's game is only shown while still running
        rows.append(row)
    order = {"in": 0, "unknown": 1, "pre": 2, "post": 3}
    rows.sort(key=lambda r: (order.get(r["state"], 1), r["start"], r["game"] or "", r["play"]))
    return rows, sorted(failed)


def money(x):
    if x is None:
        return "wager n/a"
    return f"${x / 1000:,.1f}K" if x >= 1000 else f"${x:,.0f}"


def sharp_text(s):
    """'primm, 7.2% hold, $3.4K'. Hold is lifetime, as logged on the live row."""
    hold = "hold n/a" if s.get("hold") is None else f"{s['hold']:.1f}% hold"
    return f"{s['user']}, {hold}, {money(s.get('stake'))}"


def play_cell(r):
    """One play as a table cell. Sharp Alert is the default, so only others are tagged."""
    tag = "" if r["email"] == "Sharp Alert" else f" [{r['email']}]"
    who = "; ".join(sharp_text(s) for s in r.get("sharps") or [])
    return f"{r['play']} ({r['entry']}): {r['status']}{tag}" + (f" · {who}" if who else "")


def wide_lines(rows):
    """A header plus one line per game, with its plays as Play 1, Play 2, ... columns.
    Rows arrive sorted, so games keep the board's order: live, upcoming, final."""
    games = {}
    for r in rows:
        g = games.setdefault((r["league"], r["game"], r["start"]), {"first": r, "cells": []})
        g["cells"].append(play_cell(r))
    width = max((len(g["cells"]) for g in games.values()), default=0)
    out = []
    if games:
        out.append(["League", "Game", "Score", "Clock"] + [f"Play {i + 1}" for i in range(width)])
    for g in games.values():
        r = g["first"]
        out.append([r["league"], r["game"] or "", r["score"], r["clock"] or r["time"]]
                   + g["cells"] + [""] * (width - len(g["cells"])))
    return out


def main():
    rows, failed = build()
    if "--json" in sys.argv:
        print(json.dumps({"rows": rows, "failed_feeds": failed}, indent=1))
        return 0
    for f in failed:
        print(f"!! score feed failed: {f}")
    if "--wide" in sys.argv:
        for line in wide_lines(rows):
            print(" | ".join(line))
        return 0
    if not rows:
        print("No emailed sharp plays on today's games in these competitions.")
    for r in rows:
        print(f"{r['league']:<15} {r['game']}  {r['score']}  [{r['clock'] or r['time']}]\n"
              f"{'':15} {play_cell(r)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
