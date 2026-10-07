#!/usr/bin/env python3
"""
"big board" — the six-column table: Conv | Wager | Play | Odds | Game | Start.

    python3 bigboard.py            # qualifying plays, pre-game + live
    python3 bigboard.py --pre      # upcoming only
    python3 bigboard.py --all      # every live position, not just qualifiers

Emits pipe-delimited rows so the table renders identically every time.
Reuses check.py's scan and scoring so the numbers can't drift between views.
"""
import importlib.util, json, re, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("chk", "/Users/santhosh/.nightly-line/check.py")
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)


def k(n):
    """Compact money for email — $544K not $544,387, to keep lines phone-width."""
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1e6:
        return f"${n/1e6:.1f}M"
    if a >= 1e3:
        return f"${n/1e3:.0f}K"
    return f"${n:,.0f}"


def short_team(name):
    """Shorten consistently: strip club suffixes, then take the last word,
    keeping two when the last is ambiguous.

    'Falkirk FC'->Falkirk, 'Bristol City FC'->Bristol City, 'Boston Red Sox'->
    Boston Red Sox, 'Ohio State'->Ohio State, 'Colorado Rockies'->Rockies.
    Soccer clubs end in a suffix carrying no identity, which collapsed
    'Falkirk FC vs Heart of Midlothian FC' into 'FC/FC'.
    """
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return " ".join(parts)
    CLUB_SUFFIX = {"fc", "afc", "cf", "sc", "ac", "sv", "bk", "if",
                   "united", "town", "rovers", "county", "wanderers"}
    while len(parts) > 1 and parts[-1].lower().strip(".") in CLUB_SUFFIX:
        parts = parts[:-1]
    if len(parts) == 1:
        return parts[0]
    KEEP_TWO = {"state", "tech", "sox", "jays", "a&m", "southern", "dame",
                "force", "city", "bay", "cross", "albion", "athletic"}
    if parts[-1].lower() in KEEP_TWO:
        return " ".join(parts) if len(parts) <= 3 else " ".join(parts[-2:])
    return parts[-1]


PERIOD = re.compile(r"\b([1-4]Q|[12]H|1st Half|2nd Half|1st Quarter|[1-9]th Inning|1st Inning|First Half|First Quarter|First Inning|First [1-9] Innings|First Five)\b", re.I)


def period(mkt):
    """'1H' for a first-half market, '' for full game — so a 1H moneyline never
    prints as the same play as the full-game one (Giants ML went out twice at
    two sizes on 2026-09-21 because of exactly that)."""
    m = PERIOD.search(mkt or "")
    if not m:
        return ""
    t = m.group(1).upper()
    return {"1ST HALF": "1H", "FIRST HALF": "1H", "2ND HALF": "2H",
            "1ST QUARTER": "1Q", "FIRST QUARTER": "1Q"}.get(t, t)


def play_text(h):
    """Condense market + side into 'Illinois -6.5' / 'Over 8.5' / 'Padres ML'."""
    mkt, side = h.get("market") or "", h.get("side") or ""
    per = period(mkt)
    if per and side.strip().lower() not in ("yes", "no"):
        return f"{per} {_play_text(h)}"
    return _play_text(h)


def _play_text(h):
    mkt, side = h.get("market") or "", h.get("side") or ""
    if side.strip().lower() in ("yes", "no"):
        # a prop: "Yes ML" says nothing. Keep the question, drop the matchup.
        q = mkt.split(":")[0].strip().rstrip("?")
        q = re.sub(r"^(Will there be|Will)\s+", "", q, flags=re.I)
        return f"{side} · {q[:38]}"
    if side in ("Over", "Under") or re.search(r"O/U|Total", mkt, re.I):
        m = re.search(r"(\d+\.?\d*)\s*$", mkt) or re.search(r"O/U\s*(\d+\.?\d*)", mkt, re.I)
        return f"{side} {m.group(1)}" if m else side
    m = re.match(r"(?:1H\s+)?Spread:\s*(.+?)\s*\(([-+]?\d+\.?\d*)\)", mkt)
    if m:
        named, line = m.group(1).strip(), float(m.group(2))
        # the line is quoted for the named team; flip it for the other side
        if short_team(side).lower() != short_team(named).lower() and side.lower() != named.lower():
            line = -line
        return f"{short_team(side)} {line:+g}"
    return f"{short_team(side)} ML"


def game_text(h):
    t = h.get("game") or ""
    parts = re.split(r"\s+vs\.?\s+", t, maxsplit=1)
    if len(parts) == 2:
        return f"{h['league']} {short_team(parts[0])}/{short_team(parts[1])}"
    return f"{h['league']} {t[:24]}"


def start_text(h, now):
    mins = (h["start"] - now).total_seconds() / 60
    if mins < 0:
        return "LIVE"
    return f"{int(mins//60)}h{int(mins%60):02d}m"


def _emit_books(bk, h, okey):
    """One indented line of live prices; refs marked with * so the bettable
    books are distinguishable from the low-vig reference markets."""
    try:
        px = bk.prices_for(h, okey)
    except Exception as e:
        print(f"    books: error {e}")
        return
    if not px:
        print("    books: no match")
        return
    mine = [(l, p) for l, p in px if l in ("DK", "FD", "FAN", "MGM", "CZR")]
    refs = [(l, p) for l, p in px if l not in ("DK", "FD", "FAN", "MGM", "CZR")]
    parts = [f"{l} {p:+d}" for l, p in mine] + [f"*{l} {p:+d}" for l, p in refs]
    best = f"  BEST {mine[0][0]} {mine[0][1]:+d}" if mine else ""
    print("    " + "  ".join(parts) + best)


def main():
    # Two concurrent sweeps double the request rate and trip Polymarket's
    # throttle — a 6:19pm run overlapped with an interrupted one and came back
    # 261/690 failed. One at a time.
    import os, errno
    LOCK = "/tmp/.bigboard.lock"
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except OSError as e:
        if e.errno == errno.EEXIST:
            try:
                pid = int(open(LOCK).read().strip())
                os.kill(pid, 0)          # still alive?
                print(f"another bigboard is already running (pid {pid}) — "
                      f"wait for it or: kill {pid} && rm {LOCK}")
                return 1
            except (ValueError, ProcessLookupError, OSError):
                os.unlink(LOCK)          # stale lock, take it
                open(LOCK, "w").write(str(os.getpid()))
    import atexit
    atexit.register(lambda: os.path.exists(LOCK) and os.unlink(LOCK))

    argv = sys.argv[1:]
    pre_only = "--pre" in argv
    show_all = "--all" in argv
    wide = "--wide" in argv   # adds Bettor | Hold | Month P/L | Lifetime | Flags
    email = "--email" in argv # block layout; a 12-column table wraps unreadably
                              # on a phone, so mail gets one paragraph per play
    with_books = "--books" in argv   # live sportsbook prices under each row
    json_out = argv[argv.index("--json") + 1] if "--json" in argv else None
    # --min-score N : drop rows scoring below N. Defaults to chk.MIN_SCORE (0),
    # so the terminal board is unchanged; the scheduled Discord boards pass 60
    # so a channel post never carries a sub-40 row next to a 100.
    min_score = chk.MIN_SCORE
    if "--min-score" in argv:
        min_score = float(argv[argv.index("--min-score") + 1])
    live = not pre_only
    # league args (e.g. `bigboard.py nfl atp`) shrink the slate massively —
    # 7 tags/400 games down to 2 tags/~30, which is the whole runtime.
    TAG_FOR = {"nfl": "nfl", "ncaaf": "cfb", "cfb": "cfb", "mlb": "mlb",
               "nba": "nba", "ncaab": "cbb", "cbb": "cbb", "pga": "golf",
               "golf": "golf", "atp": "tennis", "wta": "tennis", "tennis": "tennis"}
    picked = [a.lower() for a in argv if a.lower() in TAG_FOR]
    tags = sorted({TAG_FOR[a] for a in picked}) if picked else chk.TAGS
    want_leagues = None
    if picked:
        want_leagues = {"NCAAF" if a in ("cfb", "ncaaf") else
                        "NCAAB" if a in ("cbb", "ncaab") else
                        "PGA" if a == "golf" else a.upper() for a in picked}

    now = datetime.now(timezone.utc)
    local_end = now.astimezone().replace(hour=23, minute=59, second=59)
    horizon = max((local_end - now.astimezone()).total_seconds() / 3600, 1.0)

    slate = {}
    for tag in tags:
        for e in chk.get_all("https://gamma-api.polymarket.com/events?closed=false"
                             f"&tag_slug={tag}") or []:
            raw = e.get("startTime")
            if not raw:
                continue
            try:
                st = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            mins = (st - now).total_seconds() / 60
            floor = -chk.LIVE_LOOKBACK_H * 60 if live else chk.LEAD_MIN
            if mins < floor or mins / 60 > horizon:
                continue
            slug = e["slug"]
            if tag == "tennis":
                lg = ("WTA" if slug.startswith("wta-")
                      else "ATP" if slug.startswith("atp-") else "TENNIS")
            else:
                lg = {"cfb": "NCAAF", "cbb": "NCAAB", "golf": "PGA"}.get(tag, tag.upper())
            if want_leagues and lg not in want_leagues:
                continue
            slate.setdefault(slug, {"league": lg, "title": e.get("title") or slug, "start": st})

    pool = chk.build_pool()      # shared: ALL/PNL + MONTH/PNL, deduped
    slugs = list(slate)
    hits, badl = [], []

    def check_one(tr):
        rs = chk.get("https://data-api.polymarket.com/positions"
                     f"?user={tr['proxyWallet']}&limit=100&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(rs, list):
            badl.append(1)
            return
        # Typical bet size, from every open position — median, not mean, so one
        # outlier does not define "normal". Caveat: the API returns their 100
        # largest by current value, so this skews high and the multiple below is
        # therefore conservative.
        sizes = sorted(z.get("initialValue") or 0 for z in rs
                       if (z.get("initialValue") or 0) > 0)
        typical = sizes[len(sizes) // 2] if sizes else 0
        for pz in rs:
            if (pz.get("currentValue") or 0) <= 0:
                continue
            stake = pz.get("initialValue") or 0
            if stake < 1000:
                continue
            es = pz.get("eventSlug") or ""
            base = next((x for x in slugs if es == x or es.startswith(x + "-")), None)
            if not base:
                continue
            vol = tr.get("vol") or 0
            avg, cur = pz.get("avgPrice") or 0, pz.get("curPrice") or 0
            hits.append({
                "wallet": tr["proxyWallet"],
                "user": chk.short(tr.get("userName"), tr["proxyWallet"]),
                "rank": int(tr["rank"]), "pnl": tr.get("pnl") or 0, "vol": vol,
                "hold": (tr["pnl"] / vol * 100) if vol else 0.0,
                "month_pnl": tr.get("month_pnl"),
                "slug": base, "league": slate[base]["league"], "game": slate[base]["title"],
                "start": slate[base]["start"], "market": pz.get("title"),
                "side": pz.get("outcome"), "stake": stake, "entry": avg, "now": cur,
                "dog": 0 < avg < chk.DOG_PRICE,
                "decided": cur >= chk.DECIDED_PX or cur <= (1 - chk.DECIDED_PX),
                "chalk": avg >= chk.MAX_ENTRY,
                "fresh": avg > 0 and abs(cur - avg) / avg <= chk.FRESH_DRIFT,
                "typical": typical,
                "vs_typical": (stake / typical) if typical else None,
                "n_open": len(sizes),
            })

    rel = chk.sweep_lock("bigboard", wait=120)
    if rel is None:
        print("another sweep is running — try again in a minute")
        return 1
    try:
        with ThreadPoolExecutor(chk.CONCURRENCY) as ex:
            list(ex.map(check_one, pool))
    finally:
        rel()

    groups = defaultdict(list)
    for h in hits:
        groups[(h["wallet"], h["slug"])].append(h)
    for mem in groups.values():
        # shared with check.py so the two views can never disagree about what
        # counts as a stack
        anyd = any(m["dog"] for m in mem)
        tot = sum(m["stake"] for m in mem)
        agree = chk.sides_agree(mem)
        book = chk.two_sided(mem)
        for m in mem:
            m["stack"] = len({x["market"] for x in mem}) if agree else 1
            m["stack_stake"] = tot if agree else m["stake"]
            m["stack_dog"] = anyd if agree else m["dog"]
            m["hedged"] = not agree
            m["two_sided"] = book
    for h in hits:
        h["conv"] = chk.conviction(h)
    chk.mark_consensus(hits)

    bad, pool_n = len(badl), len(pool)

    # chk.passes is the single gate — this used to be a second hand-rolled copy
    # of the same boolean, which is exactly the drift check.py's docstring warns
    # about. --min-score raises only the score bar; every other bar is unchanged.
    def passes(h):
        return chk.passes(h, min_score=min_score)

    board = [h for h in hits if not h["decided"]]
    good = [h for h in board if passes(h)]
    for h in good:
        h.setdefault("why", "BOARD")
    if json_out:
        # Structured copy of the qualifying rows, so postplays.py can size and
        # price them without re-parsing the printed table.
        try:
            with open(json_out, "w") as jf:
                json.dump([{k: (v.isoformat() if hasattr(v, "isoformat") else v)
                            for k, v in h.items()} for h in good], jf)
        except Exception as e:
            print(f"(could not write {json_out}: {e})", file=sys.stderr)
    logged = chk.log_signals(good, "bigboard", now)
    rows = board if show_all else good
    rows.sort(key=lambda h: -h["conv"])

    print(f"BIG BOARD · {now.astimezone():%a %-I:%M %p %Z} · {len(slate)} games · "
          f"{pool_n-bad}/{pool_n} wallets"
          + (f" · !! {bad} FAILED, PARTIAL" if bad else ""))
    print(f"{len(good)} qualifying of {len(board)} live positions"
          + (f" · {logged} new to ledger" if logged else ""))
    bk = None
    if with_books:
        try:
            import books as bk
            okey = bk.get_key()
        except Exception as e:
            print(f"(book prices unavailable: {e})")
            bk = None
    if email:
        for h in rows:
            flags = []
            if h.get("stack", 1) >= chk.STACK_MIN:
                flags.append(f"stacked x{h['stack']} ({chk.m0(h['stack_stake'])} on this game)")
            if h.get("stack_dog", h["dog"]):
                flags.append("underdog")
            if h.get("fresh"):
                flags.append("entered at current price")
            if h.get("two_sided"):
                flags.append("BOTH SIDES — hedger")
            elif h.get("hedged"):
                flags.append("mixed sides, no stack credit")
            mp = h.get("month_pnl")
            vt = h.get("vs_typical")
            print(f"[{h['conv']:.0f}]  {k(h['stake'])} on {play_text(h)}  {chk.am(h['entry'])}")
            print(f"   {game_text(h)}")
            print(f"   {h['start'].astimezone():%-I:%M %p} ({start_text(h, now)})")
            print(f"   {h['user']} · {h['hold']:.1f}% hold"
                  + (f" · {vt:.1f}x typical" if vt else ""))
            print(f"   P/L: {k(mp)} month · {k(h['pnl'])} lifetime")
            for f_ in flags:
                print(f"   >> {f_}")
            print()
        return 0

    if wide:
        print("Conv|Size|Wager|Play|Odds|Game|Start|Bettor|Hold|Month P/L|Lifetime|vs Avg|Flags")
    else:
        print("Conv|Size|Wager|Play|Odds|Game|Start")
    # Size = what picks.py would publish: units if the row is inside the price
    # window (-122..-180) and clears the pick gate, "alert" if it clears the
    # alert gate but is outside the window, "-" otherwise. Conviction (v4)
    # rates the bettor and ignores the odds, so without this column a 100 on
    # a +140 dog reads as a play when it is only a strong-bettor alert.
    import units as _U
    for h in rows:
        _, size = _U.public_size(h)
        base = (f"{h['conv']:.0f}|{size}|{chk.m0(h['stake'])}|{play_text(h)}|{chk.am(h['entry'])}"
                f"|{game_text(h)}|{start_text(h, now)}")
        if not wide:
            print(base)
            if bk:
                _emit_books(bk, h, okey)
            continue
        flags = []
        if h.get("stack", 1) >= chk.STACK_MIN:
            flags.append(f"x{h['stack']}")
        if h.get("stack_dog", h["dog"]):
            flags.append("dog")
        if h.get("fresh"):
            flags.append("fresh")
        if h.get("two_sided"):
            flags.append("BOOK")
        elif h.get("hedged"):
            flags.append("mixed")
        vt = h.get("vs_typical")
        vs = (f"{vt:.1f}x (med {chk.m0(h['typical'])}, n={h['n_open']})"
              if vt else "—")
        mp = h.get("month_pnl")
        mtxt = chk.m0(mp) if mp is not None else "—"
        print(f"{base}|{h['user']}|{h['hold']:.1f}%|{mtxt}|{chk.m0(h['pnl'])}|{vs}"
              f"|{','.join(flags) if flags else '-'}")
        if bk:
            _emit_books(bk, h, okey)
    return 0


if __name__ == "__main__":
    sys.exit(main())
