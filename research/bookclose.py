#!/usr/bin/env python3
"""
bookclose.py -- grade backtest positions on the SPORTSBOOK closing line.

Joins the scored backtest positions

    /Users/santhosh/nl-research/windows/<ym>/bt_scored.jsonl

to the archived sportsbook line history

    ~/.nightly-line/lines/YYYY-MM-DD.jsonl

and writes, per window,

    /Users/santhosh/nl-research/windows/<ym>/bookclose.json

keyed exactly "<slug>|<market>|<side>" as those strings appear in
bt_scored.jsonl, with

    {"close_fair": 0.5213, "close_raw": 0.5385, "n_books": 6,
     "t": 1791763205, "books": ["FD","DK", ...]}

so a position can be graded on book-closing-line value instead of the
Polymarket close.  Only bettable books count toward the median: the archive
also carries PM and KAL rows, and letting Polymarket into the "book close"
would defeat the point (--include-refs overrides, for diagnostics only).

Read-only everywhere except windows/<ym>/bookclose.json.  Nothing is ever
written into ~/.nightly-line.  bt_scored.jsonl is re-read from disk on every
run (a backfill may still be appending to it) -- nothing is cached between runs.

Usage
    python3 bookclose.py 2026-09 2026-10
    python3 bookclose.py 2026-10 --examples 5
    python3 bookclose.py 2026-10 --explain 'nfl-kc-lv-2026-10-04|Chiefs vs. Raiders|Chiefs'
    python3 bookclose.py 2026-10 --include-refs   # let PM/KAL into the median
    python3 bookclose.py 2026-10 --dry-run        # summary only, write nothing

Join rules (strict; an unresolvable position is counted, never guessed):
  * game   -- archive game whose `start` is within 3h of `kickoff_ts`, same
              sport, and whose {home, away} pair matches the position's two
              teams.  Ambiguous pairs (>1 archive game tied on match quality)
              are rejected, not picked.
  * market -- ml -> mkt == "h2h"; spread -> mkt == "spreads" and the row's
              `point` equals the handicap *as the position's own side receives
              it* (so a position on the +dog of "Spread: Chiefs (-2.5)" is
              priced off the Raiders +2.5 row); total -> mkt == "totals" and
              `point` equals the O/U number (both within 0.01).
  * close  -- per book, the LAST matching row with t <= start; then the MEDIAN
              implied probability across books.  Fewer than 2 books -> unmatched.
              The emitted "t" is the latest poll used; a book that moved off the
              position's point contributes its last row AT that point, which can
              be hours older (--explain shows every book's own t), so a consumer
              wanting a tight close should filter on start - t.
  * fair   -- the opposite side's median computed the same way, normalised so
              the two sum to 1.  No usable opposite side -> close_fair is null.

Team-name matching reuses production's own helpers (`norm`, `team_match` from
~/.nightly-line/books.py) so this research join and the live board agree.
Importing books.py is inert: it pulls in oddskey/exkeys, but both only define
functions at import time -- no keychain or network access happens until
books.fetch()/get_key() is called, which this script never does.
"""
import json
import os
import re
import statistics
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

RESEARCH = Path(__file__).resolve().parent
WINDOWS = RESEARCH / "windows"
NL = Path.home() / ".nightly-line"
LINES = NL / "lines"
WINDOW_S = 3 * 3600          # |archive start - kickoff_ts| tolerance
PT_EPS = 0.01                # point/handicap equality tolerance
MIN_BOOKS = 2

# dont_write_bytecode first: importing books.py must not drop a __pycache__
# entry into ~/.nightly-line, which is read-only to this script.
sys.dont_write_bytecode = True
sys.path.insert(0, str(NL))
from books import norm, team_match, REFS_LABELS   # noqa: E402  (see docstring)

# The archive labels college football "cfb", not "ncaaf".  Values actually
# present in ~/.nightly-line/lines: cfb, nfl, mlb.
SPORT_FOR_LEAGUE = {"NFL": {"nfl"}, "NCAAF": {"cfb"}}

REASONS = [
    "before_coverage",            # kickoff predates the line archive entirely
    "no_archive_file_for_date",   # archive has no file covering that date
    "no_game_match",              # no archive game with matching teams + start
    "ambiguous_game_match",       # two archive games tied -- refused
    "side_unresolved",            # position side maps to neither game team
    "bad_market_string",          # market string did not parse
    "no_market_or_point_match",   # game joined, but no rows at that mkt/point
    "too_few_books",              # fewer than MIN_BOOKS books at the close
]


# ---------------------------------------------------------------- odds helpers
def implied(odds):
    """American odds -> implied probability (vig included)."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- name match aids
def _words(s):
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


def quality(side, name):
    """How good a team_match is, for breaking ties between archive games.

    Only ever used to *choose between* candidates that team_match already
    accepted; it never creates a match of its own.
    """
    a, b = norm(side), norm(name)
    if not a or not b:
        return 0
    if a == b:
        return 100
    wa, wb = _words(side), _words(name)
    if wa and len(wa) <= len(wb):
        for i in range(len(wb) - len(wa) + 1):
            if wb[i:i + len(wa)] == wa:
                # whole-word run, e.g. "Oklahoma" in "Oklahoma Sooners".  Extra
                # words in the archive name cost a point, so "Oklahoma" prefers
                # "Oklahoma Sooners" (1 extra) to "Oklahoma State Cowboys" (2),
                # and a run at the start beats one in the middle.
                return 60 + 2 * len(wa) - (len(wb) - len(wa)) + (5 if i == 0 else 0)
    if a in b or b in a:
        return 20 + min(len(a), 20) / 100.0        # loose substring
    return 0


# --------------------------------------------------------------- position side
def parse_position(pos):
    """-> dict(kind, team, opp_team, point, ok, why) for one position row.

    kind is "h2h" | "spreads" | "totals".  team/opp_team are the strings fed to
    team_match against the archive `name` field ("Over"/"Under" for totals).
    point is the handicap/total the position's OWN side takes (None for ml).
    """
    game, market, side = pos.get("game") or "", pos.get("market") or "", pos.get("side") or ""
    teams = re.split(r" vs\. ", game)
    if len(teams) != 2:
        return {"ok": False, "why": "bad_market_string"}
    ta, tb = teams

    # ---- total
    m = re.search(r"O/U\s*(-?\d+(?:\.\d+)?)\s*$", market)
    if m or side in ("Over", "Under"):
        if not m or side not in ("Over", "Under"):
            return {"ok": False, "why": "bad_market_string"}
        return {"ok": True, "kind": "totals", "team": side,
                "opp_team": "Under" if side == "Over" else "Over",
                "point": float(m.group(1)), "teams": teams}

    # ---- spread
    if market.startswith("Spread:"):
        m = re.match(r"^Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)\s*$", market)
        if not m:
            return {"ok": False, "why": "bad_market_string"}
        named, hcap = m.group(1), float(m.group(2))
        own = resolve_side(side, teams, pos.get("slug") or "")
        if own is None:
            return {"ok": False, "why": "side_unresolved"}
        named_res = resolve_side(named, teams, pos.get("slug") or "")
        if named_res is None:
            return {"ok": False, "why": "side_unresolved"}
        # the named team receives `hcap`; the other team receives -hcap
        point = hcap if own == named_res else -hcap
        opp = tb if own == ta else ta
        return {"ok": True, "kind": "spreads", "team": own, "opp_team": opp,
                "point": point, "teams": teams}

    # ---- moneyline (market == game)
    own = resolve_side(side, teams, pos.get("slug") or "")
    if own is None:
        return {"ok": False, "why": "side_unresolved"}
    opp = tb if own == ta else ta
    return {"ok": True, "kind": "h2h", "team": own, "opp_team": opp,
            "point": None, "teams": teams}


def resolve_side(side, teams, slug):
    """Map a position's side/market label onto one of the game's two teams.

    Most sides are already the game's own team string.  A minority of NFL
    spread rows label the team by city abbrev ("KC", "IND", "LA") which
    team_match cannot reach from "Kansas City Chiefs"; for those, the slug's
    own away-home abbrev pair ("nfl-ind-kc-2026-09-20") resolves it positionally
    -- the slug and the `game` string are written in the same order (verified:
    every joined game in both windows agrees).  Returns None if unresolvable.
    """
    ta, tb = teams
    if side == ta:
        return ta
    if side == tb:
        return tb
    hit = [t for t in teams if team_match(side, t)]
    if len(hit) == 1:
        return hit[0]
    if len(hit) == 2:                                  # prefer the better one
        qa, qb = quality(side, ta), quality(side, tb)
        if qa != qb:
            return ta if qa > qb else tb
        return None
    m = re.match(r"^(?:nfl|cfb)-(.+)-\d{4}-\d{2}-\d{2}$", slug or "")
    if m:
        toks = m.group(1).split("-")
        if len(toks) == 2 and norm(toks[0]) != norm(toks[1]):
            s = norm(side)
            if s == norm(toks[0]):
                return ta
            if s == norm(toks[1]):
                return tb
    return None


# --------------------------------------------------------------- archive index
def near(sorted_ks, st):
    i = bisect_left(sorted_ks, st)
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(sorted_ks) and abs(sorted_ks[j] - st) <= WINDOW_S:
            return True
    return False


def archive_files():
    return sorted(LINES.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl"))


def load_archive(need_sports, kickoffs, verbose=True):
    """Single pass over the line files -> {gid: game} for games near a kickoff."""
    ks = sorted(set(kickoffs))
    games, dates, bad = {}, set(), 0
    tmin = tmax = None
    files = archive_files()
    for f in files:
        dates.add(f.name[:-len(".jsonl")])
        with f.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                t = r.get("t")
                if isinstance(t, (int, float)):
                    tmin = t if tmin is None else min(tmin, t)
                    tmax = t if tmax is None else max(tmax, t)
                if r.get("sport") not in need_sports:
                    continue
                st = r.get("start")
                gid = r.get("gid")
                if not isinstance(st, (int, float)) or not gid:
                    continue
                if not near(ks, st):
                    continue
                g = games.get(gid)
                if g is None:
                    g = games[gid] = {"gid": gid, "sport": r.get("sport"),
                                      "home": r.get("home"), "away": r.get("away"),
                                      "start": st, "rows": []}
                g["start"] = st        # latest poll wins if a start was revised
                g["rows"].append((r.get("mkt"), r.get("name"), r.get("point"),
                                  r.get("price"), r.get("book"), t))
    if verbose:
        print("archive: %d files (%s .. %s), %d games near a kickoff, "
              "%d unparseable rows"
              % (len(files), min(dates) if dates else "-",
                 max(dates) if dates else "-", len(games), bad), file=sys.stderr)
    by_sport = defaultdict(list)
    for g in games.values():
        by_sport[g["sport"]].append((g["start"], g["gid"]))
    for s in by_sport:
        by_sport[s].sort()
    return {"games": games, "by_sport": by_sport, "dates": dates,
            "tmin": tmin, "tmax": tmax}


def candidate_games(arch, sports, kickoff, teams):
    """Archive games near `kickoff` whose home/away pair matches `teams`."""
    ta, tb = teams
    out = []
    for sport in sports:
        lst = arch["by_sport"].get(sport) or []
        starts = [s for s, _ in lst]
        lo = bisect_left(starts, kickoff - WINDOW_S)
        for st, gid in lst[lo:]:
            if st > kickoff + WINDOW_S:
                break
            g = arch["games"][gid]
            h, a = g.get("home"), g.get("away")
            fwd = team_match(ta, a) and team_match(tb, h)
            rev = team_match(ta, h) and team_match(tb, a)
            if not (fwd or rev):
                continue
            q = max(quality(ta, a) + quality(tb, h) if fwd else -1,
                    quality(ta, h) + quality(tb, a) if rev else -1)
            out.append((q, "away_home" if fwd else "home_away", g))
    out.sort(key=lambda x: -x[0])
    return out


# ------------------------------------------------------------------ book close
def side_close(g, kind, name, point, include_refs=False):
    """{book: (t, price)} -- each book's last row at this mkt/side/point, t<=start."""
    start = g["start"]
    per = {}
    for mkt, nm, pt, price, book, t in g["rows"]:
        if mkt != kind or not book or not isinstance(t, (int, float)) or t > start:
            continue
        if not include_refs and book in REFS_LABELS:
            continue
        if kind == "totals":
            if nm != name:
                continue
        elif not team_match(name, nm):
            continue
        p = num(pt)
        if kind == "h2h":
            pass
        elif point is None or p is None or abs(p - point) > PT_EPS:
            continue
        prev = per.get(book)
        if prev is None or t >= prev[0]:
            per[book] = (t, price)
    return per


def median_prob(per):
    probs, books = [], []
    for book, (t, price) in per.items():
        p = implied(price)
        if p is None:
            continue
        probs.append(p)
        books.append(book)
    if not probs:
        return None, [], None
    order = sorted(range(len(books)), key=lambda i: books[i])
    return statistics.median(probs), [books[i] for i in order], None


# ----------------------------------------------------------------------- windows
def read_positions(ym):
    """Fresh read of bt_scored.jsonl (a backfill may be appending to it)."""
    p = WINDOWS / ym / "bt_scored.jsonl"
    if not p.exists():
        raise SystemExit("no such window: %s (expected %s)" % (ym, p))
    rows, bad = [], 0
    with p.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    return rows, bad


def group_positions(rows):
    """-> {key: {fields..., 'n_rows': k}} deduped on "<slug>|<market>|<side>"."""
    out = {}
    for r in rows:
        key = "%s|%s|%s" % (r.get("slug"), r.get("market"), r.get("side"))
        g = out.get(key)
        if g is None:
            out[key] = {"key": key, "slug": r.get("slug"), "league": r.get("league"),
                        "game": r.get("game"), "market": r.get("market"),
                        "side": r.get("side"), "kickoff_ts": r.get("kickoff_ts"),
                        "pm_close": r.get("close"), "n_rows": 1}
        else:
            g["n_rows"] += 1
    return out


def date_of(ts):
    import time
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def process(ym, arch, include_refs=False):
    rows, bad = read_positions(ym)
    keys = group_positions(rows)
    out, reasons, detail = {}, Counter(), {}
    kinds = {}
    rrows = Counter()
    orient = Counter()
    fair_null = Counter()
    tmin_date = min(arch["dates"]) if arch["dates"] else None

    for key, pos in keys.items():
        n = pos["n_rows"]

        def fail(why):
            reasons[why] += 1
            rrows[why] += n
            detail[key] = why

        info = parse_position(pos)
        kinds[key] = info.get("kind", "?")
        if not info.get("ok"):
            fail(info["why"])
            continue
        ko = pos["kickoff_ts"]
        sports = SPORT_FOR_LEAGUE.get(pos["league"]) or set()
        if not sports or not isinstance(ko, (int, float)):
            fail("no_game_match")
            continue
        if arch["tmin"] is not None and ko < arch["tmin"]:
            fail("before_coverage")
            continue
        if date_of(ko) not in arch["dates"]:
            fail("no_archive_file_for_date")
            continue

        cands = candidate_games(arch, sports, ko, info["teams"])
        if not cands:
            fail("no_game_match")
            continue
        if len(cands) > 1 and cands[0][0] == cands[1][0] and cands[0][2]["gid"] != cands[1][2]["gid"]:
            fail("ambiguous_game_match")
            continue
        q, orientation, g = cands[0]

        own = side_close(g, info["kind"], info["team"], info["point"], include_refs)
        if not own:
            fail("no_market_or_point_match")
            continue
        p_own, books, _ = median_prob(own)
        if p_own is None or len(books) < MIN_BOOKS:
            fail("too_few_books")
            continue

        opp_point = None if info["point"] is None else -info["point"] if info["kind"] == "spreads" else info["point"]
        opp = side_close(g, info["kind"], info["opp_team"], opp_point, include_refs)
        p_opp, opp_books, _ = median_prob(opp)
        if p_opp is None or len(opp_books) < MIN_BOOKS:
            close_fair = None
            fair_null["no_opposite_side" if p_opp is None else "opposite_too_few_books"] += 1
        elif (p_own + p_opp) <= 0:
            close_fair = None
            fair_null["degenerate_pair"] += 1
        else:
            close_fair = round(p_own / (p_own + p_opp), 6)

        t_used = max(t for t, _ in own.values())
        out[key] = {"close_fair": close_fair, "close_raw": round(p_own, 6),
                    "n_books": len(books), "t": int(t_used), "books": books}
        orient[(pos["league"], orientation)] += 1
        detail[key] = {"g": g, "info": info, "own": own, "opp": opp,
                       "p_own": p_own, "p_opp": p_opp, "orientation": orientation,
                       "pos": pos}

    return {"ym": ym, "rows": len(rows), "bad_rows": bad, "keys": len(keys),
            "keymap": keys, "out": out, "reasons": reasons, "rrows": rrows,
            "detail": detail, "orient": orient, "fair_null": fair_null,
            "kinds": kinds, "tmin_date": tmin_date}


def write_out(ym, out):
    d = WINDOWS / ym
    path = d / "bookclose.json"
    tmp = d / ("bookclose.json.tmp.%d" % os.getpid())
    with tmp.open("w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def print_summary(res, fh=sys.stderr):
    joined = len(res["out"])
    jrows = sum(res["keymap"][k]["n_rows"] for k in res["out"])
    print("\n== %s ==" % res["ym"], file=fh)
    print("positions: %d rows / %d unique keys%s"
          % (res["rows"], res["keys"],
             "  (%d unparseable lines)" % res["bad_rows"] if res["bad_rows"] else ""), file=fh)
    print("joined   : %d keys (%.1f%%) / %d rows (%.1f%%)"
          % (joined, 100.0 * joined / max(res["keys"], 1),
             jrows, 100.0 * jrows / max(res["rows"], 1)), file=fh)
    print("unmatched by reason (keys / rows):", file=fh)
    for why in REASONS:
        if res["reasons"][why]:
            print("   %-26s %5d / %5d" % (why, res["reasons"][why], res["rrows"][why]), file=fh)
    extra = [w for w in res["reasons"] if w not in REASONS]
    for why in extra:
        print("   %-26s %5d / %5d" % (why, res["reasons"][why], res["rrows"][why]), file=fh)
    if res["fair_null"]:
        print("joined but close_fair=null: %s"
              % ", ".join("%s=%d" % kv for kv in sorted(res["fair_null"].items())), file=fh)
    if res["orient"]:
        print("game-string orientation (team1 vs team2 -> archive):", file=fh)
        for (lg, o), c in sorted(res["orient"].items()):
            print("   %-6s %-10s %5d" % (lg, o, c), file=fh)
    bykind = {}
    for key, kind in res["kinds"].items():
        a = bykind.setdefault(kind, [0, 0])
        a[0] += 1
        if key in res["out"]:
            a[1] += 1
    print("by market (keys joined / keys total):", file=fh)
    for kind in sorted(bykind):
        tot, jn = bykind[kind][0], bykind[kind][1]
        print("   %-8s %4d / %4d  (%.0f%%)" % (kind, jn, tot, 100.0 * jn / max(tot, 1)), file=fh)
    nb = Counter(v["n_books"] for v in res["out"].values())
    if nb:
        print("n_books: %s" % ", ".join("%d books=%d" % kv for kv in sorted(nb.items())), file=fh)


def explain(res, key, fh=sys.stdout):
    d = res["detail"].get(key)
    if d is None:
        print("%s -> not present in %s" % (key, res["ym"]), file=fh)
        return
    if isinstance(d, str):
        print("%s -> UNMATCHED (%s)" % (key, d), file=fh)
        return
    import time
    pos, g, info = d["pos"], d["g"], d["info"]
    v = res["out"][key]
    print("-" * 78, file=fh)
    print("KEY      %s" % key, file=fh)
    print("position %s | %s | side=%s | league=%s | kickoff=%d (%s)"
          % (pos["slug"], pos["market"], pos["side"], pos["league"],
             pos["kickoff_ts"], time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(pos["kickoff_ts"]))), file=fh)
    print("game str %r  -> team1=%r team2=%r" % (pos["game"], info["teams"][0], info["teams"][1]), file=fh)
    print("archive  gid=%s sport=%s start=%d (%s)  dt=%+ds"
          % (g["gid"][:10], g["sport"], g["start"],
             time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(g["start"])),
             g["start"] - pos["kickoff_ts"]), file=fh)
    print("         home=%r  away=%r   -> game string is %s"
          % (g["home"], g["away"], d["orientation"].replace("_", " vs ")), file=fh)
    print("market   %s   own side=%r point=%s   opposite=%r"
          % (info["kind"], info["team"], info["point"], info["opp_team"]), file=fh)
    for label, per in (("OWN", d["own"]), ("OPP", d["opp"])):
        for book in sorted(per):
            t, price = per[book]
            print("   %s %-6s %+5d  p=%.4f   t=%s (%+ds vs start)"
                  % (label, book, price, implied(price),
                     time.strftime("%H:%M", time.localtime(t)), t - g["start"]), file=fh)
        print("   %s median = %s over %d books"
              % (label, "%.4f" % (d["p_own"] if label == "OWN" else d["p_opp"])
                 if (d["p_own"] if label == "OWN" else d["p_opp"]) is not None else "n/a",
                 len(per)), file=fh)
    print("close_raw  = %.4f   (own side, vig in)" % v["close_raw"], file=fh)
    if v["close_fair"] is None:
        print("close_fair = null", file=fh)
    else:
        other = 1.0 - v["close_fair"]
        print("close_fair = %.4f   opposite fair = %.4f   sum = %.6f"
              % (v["close_fair"], other, v["close_fair"] + other), file=fh)
        print("overround  = %.4f (raw own + raw opp)" % (d["p_own"] + d["p_opp"]), file=fh)
    print("polymarket close for reference = %s" % pos.get("pm_close"), file=fh)


def main(argv):
    yms, examples, include_refs, dry, explains = [], 0, False, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--examples":
            i += 1
            examples = int(argv[i])
        elif a == "--explain":
            i += 1
            explains.append(argv[i])
        elif a == "--include-refs":
            include_refs = True
        elif a == "--dry-run":
            dry = True
        elif a.startswith("-"):
            print(__doc__, file=sys.stderr)
            return 2
        else:
            yms.append(a)
        i += 1
    if not yms:
        print("usage: bookclose.py <ym> [<ym> ...] [--examples N] [--explain KEY] "
              "[--include-refs] [--dry-run]", file=sys.stderr)
        return 2

    all_rows, need_sports, kickoffs = {}, set(), []
    for ym in yms:
        rows, _ = read_positions(ym)
        all_rows[ym] = rows
        for r in rows:
            need_sports |= SPORT_FOR_LEAGUE.get(r.get("league")) or set()
            if isinstance(r.get("kickoff_ts"), (int, float)):
                kickoffs.append(r["kickoff_ts"])
    if include_refs:
        print("NOTE: --include-refs -- %s are in the median (not bettable books)"
              % ",".join(sorted(REFS_LABELS)), file=sys.stderr)
    else:
        print("books: bettable only; reference venues %s excluded from the median"
              % ",".join(sorted(REFS_LABELS)), file=sys.stderr)
    arch = load_archive(need_sports, kickoffs)

    results = []
    for ym in yms:
        res = process(ym, arch, include_refs)
        results.append(res)
        if not dry:
            path = write_out(ym, res["out"])
            print("wrote %s  (%d keys)" % (path, len(res["out"])), file=sys.stderr)
        print_summary(res)

    for res in results:
        for key in explains:
            if key in res["detail"]:
                explain(res, key)
        if examples:
            shown = 0
            for key in sorted(res["out"]):
                if shown >= examples:
                    break
                explain(res, key)
                shown += 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
