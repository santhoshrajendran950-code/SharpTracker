#!/usr/bin/env python3
"""
"check again" — high-conviction plays on the REST of today's games.

    python3 check.py              # all leagues, games left today
    python3 check.py --all        # include sub-threshold plays
    python3 check.py --hours 36   # widen the window past midnight

Leagues: NFL, NCAAF, NBA, NCAAB, MLB, ATP, WTA, PGA.
Upcoming only — includes everything right up to kickoff; drops games already
underway, since those are no longer a play.
Output is deliberately short: the qualifying plays and nothing else.
"""
import functools, json, math, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# CONCURRENCY was 6, tuned 2026-09-12 when the positions API was throttling
# hard (70% of a 690-sweep came back 429). Re-benchmarked 2026-09-13: 50 wallets
# at conc=32 completed in 0.7s with zero failures, so the limit had relaxed and
# the old setting was throttling US, not them. The unresolved counter in every
# report is the guard — if failures reappear, drop this back.
POOL_SIZE, PAGE, CONCURRENCY, RETRIES, BACKOFF = 690, 50, 24, 5, 0.4
# Candidate pool = two slices with distinct jobs (measured 2026-09-14):
#   ALL/PNL   — proven over time; scale and track record
#   MONTH/PNL — current form; catches sharps who have not accumulated lifetime
#               P/L yet and are structurally invisible to an all-time ranking
# WEEK/PNL added 221 wallets and 1 usable one; ALL/VOL added 168 and ZERO —
# ranking by volume selects market makers by construction. Both dropped.
MONTH_POOL = 500

# Stamped onto every logged signal. Bump this whenever scoring changes, so a
# post-mortem can attribute outcomes to the model that produced them — without
# it, every future tuning decision is uninterpretable.
#   v1  pre-2026-09-14: stack 1.35 / dog 1.20, no side-agreement check
#   v2  2026-09-14: stack 1.15 / dog 1.45, stack requires agreeing sides,
#                   two-sided books excluded, pool = ALL/PNL + MONTH/PNL
#   v3  2026-09-23: score = predicted ROI rank, mostly PRICE. v2 was
#                   anti-monotone in two independent windows (its top quintile
#                   returned -21% and -15%) because the x1.45 dog multiplier put
#                   every big underdog bet at 100, and underdogs lose. v3's
#                   bands order win rate perfectly in both windows and ROI in
#                   one; at v3 >= 70: 74% / 72% win rate, -1.3% / +8.2% ROI.
#   v3.1 2026-09-23: price term saturates at -180 (PX_CAP) so the pick window
#                   (Santhosh: picks -180 or better) spans the full scale —
#                   -180 with both bonuses = 100. Ordering unchanged.
#   v4  2026-09-23: BETTOR PROFILE ONLY. Santhosh: price "should just be a
#                   filter... this score should mostly be based on user
#                   profile characteristics". Price moved to picks.py as the
#                   window -122..-180; inside it, hold + wager + freshness
#                   order ROI in both windows (>=80: +18.5% / +28.1%).
#                   Outside the favourites window profile features do NOT
#                   order outcomes (every one flips sign between windows), so
#                   the score is only meaningful where picks.py applies it.
MODEL_VERSION = "v4"
TAGS = ["nfl", "cfb", "nba", "cbb", "mlb", "wnba", "nhl", "tennis", "golf", "soccer"]   # wnba, nhl added 2026-09-30

# STRICT bars (default). --loose restores the older, permissive set.
# Tightened 2026-09-12: the old bars passed things like a $3,136 bet at -739
# from a wallet hedging its own book, which scored 80 on stack/fresh bonuses
# while risking 7.4x the payout. Conviction has to cost the trader something.
# DEFAULT = the original bars (restored 2026-09-12 at Santhosh's request; the
# tightened set excluded the highest-hold wallets via a lifetime-P/L floor that
# rewarded churn rather than skill). --strict re-applies the tighter set.
MIN_HOLD, MIN_PNL, MIN_STAKE = 1.5, 250_000, 2_500
MIN_ENTRY, MAX_ENTRY = 0.02, 0.90
MIN_SCORE = 0.0

STRICT_HOLD, STRICT_PNL, STRICT_STAKE = 3.0, 1_000_000, 10_000
STRICT_ENTRY_LO, STRICT_ENTRY_HI = 0.15, 0.70
STRICT_SCORE = 60.0
DOG_PRICE, FRESH_DRIFT, STACK_MIN = 0.45, 0.10, 2
DECIDED_PX, CHALK_PX = 0.97, 0.90
LEAD_MIN = 0            # include everything right up to kickoff
LIVE_LOOKBACK_H = 6     # with --live, also include games started this recently

SNAP = "check-snapshot.json"
ALERT_SNAP = "check-alert-snapshot.json"   # separate state: manual checks must
                                           # not consume an alert's "new" flag
STATS = {"ok": 0, "fail": 0, "unreachable": 0}


def get(url, retries=RETRIES):
    for a in range(retries + 1):
        p = subprocess.run(["curl", "-s", "--max-time", "15", "-w", "\n%{http_code}", url],
                           capture_output=True, text=True)
        body, _, code = p.stdout.rpartition("\n")
        if code == "200":
            STATS["ok"] += 1
            try:
                return json.loads(body)
            except ValueError:
                return None
        if code == "429" and a < retries:
            time.sleep(BACKOFF * (2 ** a))
            continue
        # A stalled request costs a full --max-time per try. At 30s x 6 tries
        # that was 3 min per wallet per thread, and a data-api stall turned the
        # 7:00 PM 2026-09-25 board into a 46-minute sweep that blocked five pick
        # runs and the alert agents. One retry for a dead connection, not five.
        if code == "000" and a >= 1:
            STATS["fail"] += 1
            STATS["unreachable"] += 1
            return None
        if a >= retries:
            STATS["fail"] += 1
            if code == "000":
                STATS["unreachable"] += 1
            return None
        time.sleep(BACKOFF)
    STATS["fail"] += 1
    return None


def get_all(base, page=100, max_pages=20):
    """gamma-api silently caps `limit` at 100 — page explicitly or lose events."""
    out = []
    for i in range(max_pages):
        c = get(f"{base}&limit={page}&offset={i * page}")
        if not c:
            break
        out.extend(c)
        if len(c) < page:
            break
    return out


def am(p):
    if not p or p <= 0 or p >= 1:
        return "—"
    return f"-{round(100 * p / (1 - p)):,}" if p >= 0.5 else f"+{round(100 * (1 - p) / p):,}"


def short(n, w):
    if not n or n.lower().startswith("0x"):
        return (w or n)[:10] + "…"
    return n if len(n) <= 20 else n[:19] + "…"


def m0(n):
    return f"${n/1e6:.1f}M" if abs(n) >= 1e6 else f"${n:,.0f}"


def sub_scores(h):
    hold = max(0.0, min(h["hold"] / 5.0, 1.0))
    pnl = max(0.0, min(math.log10(max(h["pnl"], 1) / 1e5) / 2.0, 1.0)) if h["pnl"] > 0 else 0.0
    stake = max(0.0, min(math.log10(max(h["stake"], 1) / 1e3) / math.log10(200), 1.0))
    return hold, pnl, stake


LEDGER = "signals.jsonl"


SWEEP_LOCK = "/tmp/.nightly-sweep.lock"


def sweep_lock(who, wait=0):
    """One 1,000-wallet sweep at a time, across every script.

    bigboard, check, windows and the alert agents all sweep the same endpoint.
    Two at once doubles the request rate — that produced 261/690 failures once,
    and an 8m53s board run when windows.py overlapped. Returns a release
    callable, or None if another sweep holds the lock.
    """
    import errno, time as _t
    deadline = _t.time() + wait
    while True:
        try:
            fd = os.open(SWEEP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{who}:{os.getpid()}".encode())
            os.close(fd)

            def release():
                try:
                    os.unlink(SWEEP_LOCK)
                except OSError:
                    pass
            return release
        except OSError as e:
            if e.errno != errno.EEXIST:
                return lambda: None
            try:
                pid = int(open(SWEEP_LOCK).read().split(":")[-1])
                os.kill(pid, 0)                       # holder alive?
            except (ValueError, ProcessLookupError, OSError):
                try:
                    os.unlink(SWEEP_LOCK)             # stale, reclaim
                except OSError:
                    pass
                continue
            if _t.time() >= deadline:
                return None
            _t.sleep(2)


def build_pool():
    """Candidate wallets: top-690 lifetime sports P/L + top-500 this month.

    The pool is only a net — the quality bars (hold, lifetime P/L, stake) do the
    actual selecting. 57% of the all-time slice already clears 3% hold.

    Each trader is annotated with `month_pnl` from the MONTH slice, so recent
    form can be shown next to lifetime: a wallet up $1.5M lifetime but flat this
    month reads very differently from one up $400K in the last 30 days.
    """
    month = {}
    for off in range(0, MONTH_POOL, PAGE):
        for t in get("https://data-api.polymarket.com/v1/leaderboard"
                     f"?category=SPORTS&timePeriod=MONTH&orderBy=PNL"
                     f"&limit={PAGE}&offset={off}") or []:
            w = t.get("proxyWallet")
            if w:
                month[w] = t.get("pnl") or 0

    seen, pool = set(), []
    for off in range(0, POOL_SIZE, PAGE):
        for t in get("https://data-api.polymarket.com/v1/leaderboard"
                     f"?category=SPORTS&timePeriod=ALL&orderBy=PNL"
                     f"&limit={PAGE}&offset={off}") or []:
            w = t.get("proxyWallet")
            if w and w not in seen:
                seen.add(w)
                t["month_pnl"] = month.get(w)
                pool.append(t)

    # Month-slice wallets with no lifetime ranking — the discovery half. Their
    # month row carries only month vol/pnl, so each needs one lifetime lookup.
    # Done in parallel: serially this took 67s and dominated the whole scan.
    missing = [w for w in month if w not in seen]
    if missing:
        def lifetime(w):
            rows = get("https://data-api.polymarket.com/v1/leaderboard"
                       f"?category=SPORTS&timePeriod=ALL&orderBy=PNL&limit=1&user={w}")
            return (w, rows[0] if rows else None)
        with ThreadPoolExecutor(CONCURRENCY) as ex:
            for w, t in ex.map(lifetime, missing):
                if t:
                    t["month_pnl"] = month.get(w)
                    seen.add(w)
                    pool.append(t)

    # Third source, 2026-09-23: wallets holders.py found holding size on the
    # slate that neither leaderboard slice ranks. xifutloong3 (3.3% hold, $409K)
    # was on a $63K Phillies ML and invisible to the board. discovered.json is
    # written by the daily sweep; each entry carries its own leaderboard row.
    try:
        with open(os.path.join(HERE, "discovered.json")) as f:
            for w, t in json.load(f).items():
                if w not in seen and isinstance(t, dict) and t.get("proxyWallet"):
                    t.setdefault("month_pnl", month.get(w))
                    seen.add(w)
                    pool.append(t)
    except (FileNotFoundError, ValueError):
        pass
    return pool


def log_signals(rows, source, now):
    """Append qualifying signals to the ledger.

    Until 2026-09-14 only scan.py (the 9am daily) wrote here, so everything the
    alert and the board surfaced during the day went unmeasured — Kch-Temp's
    winning Commanders stack never entered the ledger because the 9am scan
    predated it. Every view now logs.

    Re-logs a position only when its stake moves more than GROWTH_PCT, which
    keeps repeated scans from spamming the file while still recording the
    trajectory (a position growing across scans is its own signal).
    """
    GROWTH_PCT = 0.02
    seen = {}
    try:
        with open(LEDGER) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                seen[(d.get("slug"), d.get("market"), d.get("side"),
                      d.get("user"))] = d.get("stake") or 0
    except FileNotFoundError:
        pass

    new = []
    for r in rows:
        k = (r["slug"], r["market"], r["side"], r["user"])
        prev = seen.get(k)
        if prev is not None and prev > 0 and abs(r["stake"] - prev) / prev <= GROWTH_PCT:
            continue
        start = r["start"]
        new.append({
            "logged": now.isoformat(), "source": source,
            "model": MODEL_VERSION,
            "slug": r["slug"], "game": r["game"], "league": r["league"],
            "market": r["market"], "side": r["side"],
            "why": r.get("why") or r.get("tier") or "BOARD",
            "stake": r["stake"], "entry": r["entry"], "user": r["user"],
            "rank": r["rank"], "hold": r["hold"],
            "stack": r.get("stack", 1), "dog": r["dog"], "fresh": r["fresh"],
            "consensus": r.get("consensus", 1), "opposed": r.get("opposed", 0),
            "conv": round(r.get("conv", 0), 1),
            "start": start.isoformat() if hasattr(start, "isoformat") else start,
        })
        seen[k] = r["stake"]
    if new:
        with open(LEDGER, "a") as f:
            for d in new:
                f.write(json.dumps(d) + "\n")
    return len(new)


def _norm_side(x):
    return re.sub(r"[^a-z0-9]", "", (x or "").lower())


def sides_agree(members):
    """Do all of one wallet's legs on one game back the SAME side?

    Polymarket mixes naming across market types — the moneyline says "Bills"
    while a spread says "BUF" — so this only returns True when agreement is
    *confident*, and fails closed otherwise. That is deliberate: the bug this
    fixes was a hedger holding Texans ML and Bills ML scoring 92, because the
    old code counted distinct markets without ever checking the side.
    """
    sides = {_norm_side(m["side"]) for m in members}
    if len(sides) <= 1:
        return True
    if {"over", "under"} & sides and len(sides) > 1:
        return False          # a totals leg plus anything else is not one side
    lst = sorted(sides)
    return all(a == b or a in b or b in a for i, a in enumerate(lst) for b in lst[i + 1:])


HEDGE_MATERIAL = 0.25    # the smaller side must be >= this x the larger side


def two_sided(members):
    """Wallet is on BOTH sides of the same market AT SIZE — a book, not an
    opinion. 2026-09-28: primm's $104K Bears +3.5 vanished from the board
    because of a $3K Eagles -3.5 ticket in the same market. A middle/lottery
    ticket under HEDGE_MATERIAL of the main side is not a hedge."""
    return bool(two_sided_markets(members))


def two_sided_markets(members):
    """The set of market titles on which this wallet is two-sided at size.
    Per MARKET, not per game: primm's $1.8K/$550 middle on an alt spread was
    knocking his $104K main-line position off the board (2026-09-28)."""
    by_market = defaultdict(lambda: defaultdict(float))
    for m in members:
        by_market[m["market"]][_norm_side(m["side"])] += float(m.get("stake") or 0)
    out = set()
    for mkt, sides in by_market.items():
        if len(sides) > 1:
            vals = sorted(sides.values())
            if vals[-2] >= HEDGE_MATERIAL * vals[-1]:
                out.add(mkt)
    return out



def _lean(h):
    """(group, token) — which way a position leans, ACROSS markets of a game.

    Blue Jays ML and Blue Jays -1.5 are the same opinion, and Over 8.5 and Over
    9 are the same opinion, so consensus is counted on the lean and not on the
    exact market. Yes/No props only mean something inside their own question,
    so those stay keyed to the market title.
    """
    mkt, side = (h.get("market") or ""), (h.get("side") or "").strip().lower()
    per = re.match(r"^(\dH|\dQ)\s", mkt)
    per = per.group(1) if per else ""
    if side in ("over", "under") or re.search(r"O/U|Total", mkt, re.I):
        return per + "total", side
    if side in ("yes", "no"):
        return per + "prop:" + mkt, side
    return per + "side", side


def mark_consensus(hits):
    """Stamp every row with `consensus` (distinct qualifying wallets leaning the
    same way in that game, itself included) and `opposed` (distinct qualifying
    wallets leaning the other way).

    `stack` is ONE wallet's legs on a game. It says nothing about agreement
    BETWEEN wallets, which is what the 2026-09-14 week pointed at: sides with 3+
    sharps went 10-7 (+13% flat) in the signal ledger against 24-27 (-14.5%) for
    exactly two, and Santhosh's own largest stakes sat on 4+ sharp sides (10-2).
    One week, 17 sides — a lead, not a finding — so it moves unit size a step or
    two (units.py) and deliberately does NOT feed the conviction score.

    Only wallets that clear the normal gate count. A hedger's two-sided book is
    already excluded by passes(), so one wallet cannot be its own consensus.
    Must run AFTER conviction is set, because passes() reads it.
    """
    lean = defaultdict(lambda: defaultdict(set))
    for h in hits:
        if "conv" in h and passes(h, min_score=0):
            grp, tok = _lean(h)
            lean[(h["slug"], grp)][tok].add(h["wallet"])
    for h in hits:
        grp, tok = _lean(h)
        sides = lean.get((h["slug"], grp), {})
        mine = sides.get(tok, set())
        h["consensus"] = max(len(mine), 1)
        h["opposed"] = len(set().union(*[w for t, w in sides.items() if t != tok]) - mine) \
            if len(sides) > 1 else 0
    return hits

def conviction(h):
    """v4 — the bettor, not the bet. See MODEL_VERSION.

      hold    career edge: 5%+ 45 · 3-5% 40 · 2-3% 25 · under 2% 10
      wager   $100K+ 45 · $25K+ 40 · $10K+ 35 · $5K+ 20 · under 10
      fresh   +10 if the price has not moved away from their entry

    Range 20-100. 100 = a 5%-hold wallet betting six figures at a price that
    has not moved. The odds are NOT in here; picks.py filters on them.
    """
    hold = h.get("hold") or 0
    st = h.get("stake") or 0
    s = 45 if hold >= 5 else 40 if hold >= 3 else 25 if hold >= 2 else 10
    s += 45 if st >= 100_000 else 40 if st >= 25_000 else 35 if st >= 10_000 else 20 if st >= 5_000 else 10
    if h.get("fresh"):
        s += 10
    return float(s)


def conviction_v2(h):
    a, b, c = sub_scores(h)
    base = ((a + 0.05) * (b + 0.05) * (c + 0.05)) ** (1 / 3.0)
    m = 1.0
    # Reweighted 2026-09-14. Measured CLV over 31 signals: underdog entries
    # +3.54 pts and beat the close 7/8; stacked +0.60. The old weights had
    # stack 1.35 / dog 1.20, which inverted what the data showed. Kept modest —
    # 8 underdog signals is thin evidence, so this is a tilt, not a rebuild.
    if h.get("stack", 1) >= STACK_MIN:
        m *= 1.15
    if h.get("stack_dog", h["dog"]):
        m *= 1.45
    if h["fresh"]:
        m *= 1.10
    return min(base * m * 100, 100)


def passes(h, min_hold=MIN_HOLD, min_pnl=MIN_PNL, min_stake=MIN_STAKE,
           lo=MIN_ENTRY, hi=MAX_ENTRY, min_score=MIN_SCORE):
    """Single gate — the leaderboard's "*", the filter, and any backtest must
    never drift apart. Defaults are the DEFAULT (non-strict) bars; main() passes
    whatever the flags resolved to.
    """
    return (h["hold"] >= min_hold and h["pnl"] >= min_pnl
            and h["stake"] >= min_stake and not h["decided"]
            and not h.get("two_sided")          # hedger's book, not a read
            and lo <= h["entry"] <= hi and h["conv"] >= min_score)


def main():
    show_all = "--all" in sys.argv
    # --alert N : print ONLY newly-seen plays scoring >= N, nothing else.
    # Silence means nothing new, so a wrapper can email on any output at all.
    live = "--live" in sys.argv     # include in-progress games, not just upcoming
    pool_size = POOL_SIZE
    if "--pool" in sys.argv:
        pool_size = int(sys.argv[sys.argv.index("--pool") + 1])
    strict = "--strict" in sys.argv
    min_hold = STRICT_HOLD if strict else MIN_HOLD
    min_pnl = STRICT_PNL if strict else MIN_PNL
    min_stake = STRICT_STAKE if strict else MIN_STAKE
    lo = STRICT_ENTRY_LO if strict else MIN_ENTRY
    hi = STRICT_ENTRY_HI if strict else MAX_ENTRY
    min_score = STRICT_SCORE if strict else MIN_SCORE
    if "--min-hold" in sys.argv:
        min_hold = float(sys.argv[sys.argv.index("--min-hold") + 1])
    alert_json = None
    if "--alert-json" in sys.argv:
        alert_json = sys.argv[sys.argv.index("--alert-json") + 1]
    alert = None
    if "--alert" in sys.argv:
        alert = float(sys.argv[sys.argv.index("--alert") + 1])
    snap_file = ALERT_SNAP if alert is not None else SNAP
    hours = 24.0
    if "--hours" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--hours") + 1])

    now = datetime.now(timezone.utc)
    local_end = now.astimezone().replace(hour=23, minute=59, second=59)
    horizon = min(hours, max((local_end - now.astimezone()).total_seconds() / 3600, 1.0))

    slate = {}
    for tag in TAGS:
        for e in get_all(f"https://gamma-api.polymarket.com/events?closed=false&tag_slug={tag}") or []:
            raw = e.get("startTime")
            if not raw:
                continue
            try:
                st = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            mins = (st - now).total_seconds() / 60
            floor = -LIVE_LOOKBACK_H * 60 if live else LEAD_MIN
            if mins < floor or mins / 60 > horizon:
                continue
            slug = e["slug"]
            if tag == "tennis":
                lg = ("WTA" if slug.startswith("wta-")
                      else "ATP" if slug.startswith("atp-") else "TENNIS")
            else:
                lg = {"cfb": "NCAAF", "cbb": "NCAAB", "golf": "PGA"}.get(tag, tag.upper())
            slate.setdefault(slug, {"league": lg, "title": e.get("title") or slug, "start": st})

    if not slate:
        if STATS["ok"] == 0:
            print("NETWORK FAILURE — no data retrieved. This is not 'no games'.")
            return 2
        print(f"No games left today (window {horizon:.1f}h).")
        return 0

    pool = build_pool()
    if pool_size != POOL_SIZE:          # --pool N still overrides for ad-hoc runs
        pool = pool[:pool_size]
    slugs = list(slate)
    hits, bad = [], []

    def check(tr):
        rs = get("https://data-api.polymarket.com/positions"
                 f"?user={tr['proxyWallet']}&limit=100&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(rs, list):
            bad.append(1)
            return
        for p in rs:
            if (p.get("currentValue") or 0) <= 0:
                continue
            stake = p.get("initialValue") or 0
            if stake < 1000:
                continue
            es = p.get("eventSlug") or ""
            base = next((s for s in slugs if es == s or es.startswith(s + "-")), None)
            if not base:
                continue
            vol = tr.get("vol") or 0
            avg, cur = p.get("avgPrice") or 0, p.get("curPrice") or 0
            hits.append({
                "wallet": tr["proxyWallet"], "user": short(tr.get("userName"), tr["proxyWallet"]),
                "rank": int(tr["rank"]), "pnl": tr.get("pnl") or 0, "vol": vol,
                "hold": (tr["pnl"] / vol * 100) if vol else 0.0,
                "month_pnl": tr.get("month_pnl"),
                "slug": base, "league": slate[base]["league"], "game": slate[base]["title"],
                "start": slate[base]["start"], "market": p.get("title"), "side": p.get("outcome"),
                "stake": stake, "entry": avg, "now": cur,
                "dog": 0 < avg < DOG_PRICE,
                "decided": cur >= DECIDED_PX or cur <= (1 - DECIDED_PX),
                "chalk": avg >= CHALK_PX,
                "fresh": avg > 0 and abs(cur - avg) / avg <= FRESH_DRIFT,
            })

    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(check, pool))

    groups = defaultdict(list)
    for h in hits:
        groups[(h["wallet"], h["slug"])].append(h)
    for mem in groups.values():
        anyd = any(m["dog"] for m in mem)
        tot = sum(m["stake"] for m in mem)
        agree = sides_agree(mem)
        book = two_sided_markets(mem)
        for m in mem:
            # only count it as a stack when the legs actually back the same side
            m["stack"] = len({x["market"] for x in mem}) if agree else 1
            m["stack_stake"] = tot if agree else m["stake"]
            m["stack_dog"] = anyd if agree else m["dog"]
            m["hedged"] = not agree
            m["two_sided"] = m["market"] in book
    for h in hits:
        h["conv"] = conviction(h)
    mark_consensus(hits)

    gate = functools.partial(passes, min_hold=min_hold, min_pnl=min_pnl,
                             min_stake=min_stake, lo=lo, hi=hi, min_score=min_score)

    good = [h for h in hits if gate(h)]
    good.sort(key=lambda h: -h["conv"])

    # diff against the previous check so new money is visible at a glance
    prev, prev_at = {}, None
    try:
        with open(snap_file) as f:
            d = json.load(f)
        prev_at = d.get("at")
        prev = {k: v for k, v in d.get("positions", {}).items()}
    except Exception:
        pass
    key = lambda h: f"{h['wallet']}|{h['slug']}|{h['market']}|{h['side']}"
    for h in hits:
        k = key(h)
        if not prev:
            h["delta"] = ""          # no baseline yet — nothing is "new"
        elif k not in prev:
            h["delta"] = "NEW"
        elif h["stake"] > prev[k] * 1.02:
            h["delta"] = f"+{m0(h['stake'] - prev[k])}"
        else:
            h["delta"] = ""
    gone = []
    if prev:
        live = {key(h) for h in hits}
        gone = [(k, v) for k, v in prev.items() if k not in live and v >= MIN_STAKE]

    if show_all:
        # full conviction leaderboard: every live position ranked, not just the
        # ones clearing the bar. "*" marks a qualifier.
        board = [h for h in hits if not h["decided"]]
        board.sort(key=lambda h: -h["conv"])
        print(f"CONVICTION LEADERBOARD · {now.astimezone():%a %-I:%M %p %Z} · "
              f"{len(slate)} games left · {len(pool)-len(bad)}/{len(pool)} wallets")
        if bad:
            print(f"!! {len(bad)} wallet lookups failed — list is PARTIAL")
        n_q = sum(1 for h in board if gate(h))
        print(f"{len(board)} live positions · {n_q} clear the bar (*) · "
              f"chalk shown but never qualifies\n")
        print(f"{'#':>3} {'SCORE':>6} {'':1} {'WAGER':>9} {'ODDS':>7}  "
              f"{'PLAY':24} {'BETTOR':20} {'HOLD':>6} {'LIFETIME':>9}  GAME")
        print("-" * 128)
        for i, h in enumerate(board, 1):
            q = "*" if gate(h) else " "
            flags = []
            if h.get("stack", 1) >= STACK_MIN:
                flags.append(f"x{h['stack']}")
            if h.get("stack_dog", h["dog"]):
                flags.append("dog")
            if h["chalk"]:
                flags.append("CHALK")
            mins = (h["start"] - now).total_seconds() / 60
            print(f"{i:>3} {h['conv']:>6.1f} {q:1} {m0(h['stake']):>9} {am(h['entry']):>7}  "
                  f"{h['side'][:23]:24} {h['user'][:19]:20} {h['hold']:>5.2f}% {m0(h['pnl']):>9}  "
                  f"{h['league']} {h['game'][:26]} {int(mins//60)}h{int(mins%60):02d}m"
                  + (f" [{','.join(flags)}]" if flags else ""))
        log_signals(good, "board", now)
        save(hits, now, snap_file)
        return 0

    if alert is not None:
        fresh = [h for h in good if h["conv"] >= alert and h["delta"] == "NEW"]
        fresh.sort(key=lambda h: -h["conv"])
        if fresh:
            print(f"{len(fresh)} new high-conviction play(s) "
                  f"scoring {alert:.0f}+ · {now.astimezone():%a %-I:%M %p %Z}")
            if bad:
                print(f"(note: {len(bad)} wallet lookups failed — scan was partial)")
            print()
            for h in fresh:
                mins = (h["start"] - now).total_seconds() / 60
                tags = []
                if h.get("stack", 1) >= STACK_MIN:
                    tags.append(f"stacked x{h['stack']} ({m0(h['stack_stake'])} on this game)")
                if h.get("stack_dog", h["dog"]):
                    tags.append("underdog")
                if h["fresh"]:
                    tags.append("entered at current price")
                print(f"[{h['conv']:.0f}] {m0(h['stake'])} on {h['side']}  {am(h['entry'])}")
                print(f"     {h['league']} · {h['game']} · {h['start'].astimezone():%-I:%M %p}"
                      f" (in {int(mins//60)}h{int(mins%60):02d}m)")
                print(f"     {h['market']}")
                print(f"     {h['user']} #{h['rank']} · {h['hold']:.1f}% hold · "
                      f"{m0(h['pnl'])} lifetime P/L")
                if tags:
                    print(f"     >> {' | '.join(tags)}")
                print()
            print("Informational only — what anonymous traders hold. Not advice.")
        if alert_json:
            # Structured copy of exactly what was printed, so a delivery layer
            # can split by sport and attach unit sizing without re-parsing the
            # human-readable text. Detection stays here; formatting does not.
            try:
                with open(alert_json, "w") as jf:
                    json.dump([{k: (v.isoformat() if hasattr(v, "isoformat") else v)
                                for k, v in h.items()} for h in fresh], jf)
            except Exception as e:
                print(f"(could not write {alert_json}: {e})", file=sys.stderr)
        log_signals(good, "alert", now)
        save(hits, now, snap_file)
        return 0

    hdr = (f"CHECK · {now.astimezone():%a %-I:%M %p %Z} · {len(slate)} games left today · "
           f"{len(pool)-len(bad)}/{len(pool)} wallets"
           + (f" [STRICT: {min_hold}% hold, {m0(min_pnl)} lifetime, {m0(min_stake)} stake, "
              f"entry {lo:.2f}-{hi:.2f}, score {min_score:.0f}+]" if strict
              else f" [{min_hold}% hold, {m0(min_pnl)} lifetime, {m0(min_stake)} stake]"))
    print(hdr)
    if bad:
        print(f"!! {len(bad)} wallet lookups failed — list is PARTIAL, not a clean read")
    n_new = sum(1 for h in good if h["delta"] == "NEW")
    n_up = sum(1 for h in good if h["delta"].startswith("+"))
    line = f"{len(good)} high-conviction from {len(hits)} positions >$1,000"
    if prev_at:
        when = datetime.fromisoformat(prev_at).astimezone().strftime("%-I:%M %p")
        line += f" · vs {when}: {n_new} new, {n_up} increased"
    else:
        line += " · (first run, no baseline to diff)"
    print(line + "\n")

    if not good:
        print("Nothing clears the bar on today's remaining games.")
        alt = sorted([h for h in hits if not h["decided"] and not h["chalk"]],
                     key=lambda x: -x["stake"])[:3]
        if alt:
            print("\nBiggest money anyway (does NOT clear quality):")
            for h in alt:
                print(f"  {m0(h['stake']):>8} {h['side'][:18]:20} {am(h['entry']):>7}  "
                      f"{h['user']} #{h['rank']} hold {h['hold']:.2f}%  · {h['league']} {h['game'][:28]}")
        log_signals(good, "check", now)
        save(hits, now, snap_file)
        return 0

    for h in good[:10]:
        mins = (h["start"] - now).total_seconds() / 60
        tags = []
        if h.get("stack", 1) >= STACK_MIN:
            tags.append(f"stacked x{h['stack']} ({m0(h['stack_stake'])})")
        if h.get("stack_dog", h["dog"]):
            tags.append("underdog")
        if h["fresh"]:
            tags.append("fresh")
        mark = f" <<{h['delta']}" if h["delta"] else ""
        print(f"[{h['conv']:5.1f}] {m0(h['stake']):>8} {h['side'][:20]:22} {am(h['entry']):>7}"
              f"   {h['league']} · {h['game'][:30]} · {h['start'].astimezone():%-I:%M %p}"
              + (" LIVE" if mins < 0 else f" (in {int(mins//60)}h{int(mins%60):02d}m)") + mark)
        print(f"         {h['user']} #{h['rank']} · {h['hold']:.1f}% hold · {m0(h['pnl'])} lifetime"
              + (f" · {', '.join(tags)}" if tags else ""))
        print(f"         {h['market'][:62]}")
    if len(good) > 10:
        print(f"\n(+{len(good)-10} more below the top 10)")
    if gone:
        print(f"\nCLOSED since last check ({len(gone)}):")
        for k, v in sorted(gone, key=lambda kv: -kv[1])[:5]:
            w, sl, mk, sd = k.split("|", 3)
            print(f"  {m0(v):>8} {sd[:20]:22} {mk[:38]}")
    log_signals(good, "check", now)
    save(hits, now, snap_file)
    return 0


def save(hits, now, snap_file=SNAP):
    try:
        with open(snap_file, "w") as f:
            json.dump({"at": now.isoformat(),
                       "positions": {f"{h['wallet']}|{h['slug']}|{h['market']}|{h['side']}": h["stake"]
                                     for h in hits}}, f)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
