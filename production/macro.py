#!/usr/bin/env python3
"""
"macro board" — sharp money on liquid Rates/Macro, Crypto and Politics markets.

    python3 macro.py                 # all four categories, qualifying plays
    python3 macro.py econ politics   # shrink the slate (econ|politics|crypto|finance)
    python3 macro.py --all           # every live position, ranked, * = qualifies
    python3 macro.py --days 30       # only markets resolving within 30 days (default 120)

How this differs from check.py (sports) — every change below is deliberate:

  * Skill is CATEGORY-SPECIFIC. A wallet only counts on an event if it ranks on
    that event's own leaderboard (a sports sharp's Fed bet is not signal).
  * FRESHNESS is measured, not inferred. Sports markets end at kickoff, so
    entry≈current meant "fresh". These run for months: a position bought in March
    can sit at today's price by coincidence. We pull the trade history for every
    candidate and count dollars actually bought in the last FRESH_DAYS.
  * HEDGES are excluded up front (check.py defect #1). Opposite sides of one
    market, or YES on two mutually-exclusive outcomes of a neg-risk event, is a
    book not a view — unless one leg is >= DOMINANT of the money, in which case
    that leg is kept and flagged "hedged".
  * No stack bonus. CLV on the sports ledger said stack ≈ +0.6 pts, dog +3.5.
  * "dog" is judged at the CURRENT price — that's what Santhosh would pay.

Polymarket prices are REFERENCE ONLY. Santhosh can bet any CFTC DCM except
Polymarket and Kalshi: Robinhood, DraftKings Predictions (Railbird), Crypto.com,
IBKR ForecastEx, PredictIt.
"""
import errno, json, math, os, statistics, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

CONCURRENCY, RETRIES, BACKOFF, PAGE = 24, 5, 0.4, 50

# leaderboard category -> gamma tag slugs that belong to it
CATS = {
    "ECONOMICS": ["economy", "fed-rates", "economic-policy"],
    "POLITICS":  ["politics", "geopolitics"],
    "CRYPTO":    ["crypto"],
    "FINANCE":   ["finance"],
}
PRIORITY = ["ECONOMICS", "CRYPTO", "FINANCE", "POLITICS"]
ALIAS = {"econ": "ECONOMICS", "economics": "ECONOMICS", "macro": "ECONOMICS", "rates": "ECONOMICS",
         "fed": "ECONOMICS", "politics": "POLITICS", "pol": "POLITICS",
         "crypto": "CRYPTO", "finance": "FINANCE"}
# Leaderboard depth per category. Probed 2026-09-14: POLITICS #500 = $96K
# lifetime, CRYPTO #500 = $70K, ECONOMICS #300 ≈ $20K, FINANCE #200 = $20K.
POOL = {"POLITICS": 500, "CRYPTO": 500, "ECONOMICS": 300, "FINANCE": 200}
# Economics/finance leaderboards are an order of magnitude thinner than politics
# (#50 is $138K vs $762K), so one P/L floor would empty them.
MIN_PNL = {"POLITICS": 250_000, "CRYPTO": 250_000, "ECONOMICS": 50_000, "FINANCE": 50_000}
PNL_FULL = {"POLITICS": 10e6, "CRYPTO": 3e6, "ECONOMICS": 2e6, "FINANCE": 1e6}

MIN_EVENT_LIQ = 250_000     # "high liquidity" — event-level
MIN_MKT_LIQ = 25_000        # the specific outcome must be tradeable too
MAX_DAYS = 120
MIN_HOLD = 1.5              # lifetime P/L ÷ volume, %, in-category
MM_HOLD = 0.5               # below this it's a market maker — never signal
MIN_STAKE = 5_000
MIN_NOW, MAX_NOW = 0.03, 0.90
DECIDED_PX = 0.97
DOG_PRICE = 0.45
FRESH_DAYS, FRESH_SHARE = 7, 0.50   # >= half the stake bought in the last week
STALE_DAYS = 45             # nothing bought in this long = old position
CHASE = 0.30                # price ran >= 30% past their entry: you'd be chasing
DOMINANT = 0.75
LIVE_PX = 0.03             # an outcome priced outside 3–97% is effectively dead
MIN_SCORE = 60.0

LOCK = "/tmp/.bigboard.lock"   # shared with bigboard: same positions API, never
                               # run two sweeps at once (one overlap = 261 failures)
LEDGER = "macro-signals.jsonl"
SNAP = "macro-snapshot.json"
STATS = {"ok": 0, "fail": 0, "unreachable": 0}

# Where to look first, not a guarantee the contract is listed — venue menus differ.
VENUES = {
    "ECONOMICS": "Robinhood · ForecastEx · Crypto.com · DK Predictions",
    "POLITICS": "PredictIt · Robinhood · ForecastEx",
    "CRYPTO": "Crypto.com · Robinhood · DK Predictions",
    "FINANCE": "Robinhood · Crypto.com · ForecastEx · DK Predictions",
}


def get(url, retries=RETRIES):
    for a in range(retries + 1):
        p = subprocess.run(["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}", url],
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


def fnum(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def am(p):
    if not p or p <= 0 or p >= 1:
        return "—"
    return f"-{round(100 * p / (1 - p)):,}" if p >= 0.5 else f"+{round(100 * (1 - p) / p):,}"


def m0(n):
    return f"${n/1e6:.1f}M" if abs(n) >= 1e6 else f"${n/1e3:.0f}K" if abs(n) >= 1e4 else f"${n:,.0f}"


def short(n, w):
    if not n or n.lower().startswith("0x"):
        return (w or n)[:10] + "…"
    return n if len(n) <= 18 else n[:17] + "…"


def take_lock():
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
        try:
            pid = int(open(LOCK).read().strip())
            os.kill(pid, 0)
            print(f"another sweep is already running (pid {pid}) — wait for it or: "
                  f"kill {pid} && rm {LOCK}")
            return False
        except (ValueError, ProcessLookupError, OSError):
            os.unlink(LOCK)
            open(LOCK, "w").write(str(os.getpid()))
    import atexit
    atexit.register(lambda: os.path.exists(LOCK) and os.unlink(LOCK))
    return True


# ---------------------------------------------------------------- slate
def load_slate(cats, now, max_days):
    """conditionId -> market info, for liquid open events in the chosen categories."""
    events = {}
    for cat in cats:
        for tag in CATS[cat]:
            for e in get_all(f"https://gamma-api.polymarket.com/events?closed=false&tag_slug={tag}"):
                events.setdefault(e["slug"], e)
    markets = {}
    n_events = 0
    for slug, e in events.items():
        tags = {t.get("slug") for t in (e.get("tags") or [])}
        # ONE category per event, most specific first: gamma tags "politics" on
        # nearly everything (the Fed decision, the crypto Clarity Act), and letting
        # a politics bettor's record vouch for a Fed bet defeats the point.
        primary = next((c for c in PRIORITY if tags & set(CATS[c])), None)
        if primary not in cats:
            continue
        ecats = [primary]
        liq = fnum(e.get("liquidity")) or sum(fnum(m.get("liquidityNum")) for m in e.get("markets") or [])
        if liq < MIN_EVENT_LIQ:
            continue
        try:
            end = datetime.fromisoformat((e.get("endDate") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        days = (end - now).total_seconds() / 86400
        if days < -2 or days > max_days:     # small grace: resolution often lags endDate
            continue
        live = 0
        for m in e.get("markets") or []:
            if m.get("closed") or not m.get("conditionId"):
                continue
            live += 1
            markets[m["conditionId"]] = {
                "event": slug, "title": e.get("title") or slug, "cats": ecats,
                "neg": bool(e.get("negRisk")), "end": end, "days": days, "event_liq": liq,
                "mkt_liq": fnum(m.get("liquidityNum")),
                "outcome_name": m.get("groupItemTitle") or m.get("question") or "",
                "question": m.get("question") or "",
                "yes_px": yes_price(m),
            }
        n_events += bool(live)
    return markets, n_events


def yes_price(m):
    try:
        return float(json.loads(m.get("outcomePrices") or "[]")[0])
    except (ValueError, IndexError, TypeError):
        return None


def direction(h, markets):
    """Canonical view a position expresses, so equivalent bets group together.
    In a neg-risk event with exactly two live outcomes, NO on one == YES on the
    other (Fed: 'NO: No change' is the '25 bps increase' bet)."""
    if h["neg"] and h["side"].lower() == "no":
        live = [c for c, mk in markets.items() if mk["event"] == h["event"]
                and mk["yes_px"] is not None and LIVE_PX < mk["yes_px"] < 1 - LIVE_PX]
        if len(live) == 2 and h["cond"] in live:
            other = live[0] if live[1] == h["cond"] else live[1]
            mk = markets[other]
            return (h["event"], other, "yes"), mk["outcome_name"] or mk["question"], mk["yes_px"]
    return (h["event"], h["cond"], h["side"].lower()), h["play"], h["now"]


# ---------------------------------------------------------------- traders
def load_pool(cats):
    """wallet -> {cat: {rank, pnl, vol, hold}} — a wallet can rank in several."""
    pool = {}
    for cat in cats:
        for off in range(0, POOL[cat], PAGE):
            rows = get("https://data-api.polymarket.com/v1/leaderboard"
                       f"?category={cat}&timePeriod=ALL&orderBy=PNL&limit={PAGE}&offset={off}")
            if not rows:
                break
            for r in rows:
                vol, pnl = fnum(r.get("vol")), fnum(r.get("pnl"))
                w = pool.setdefault(r["proxyWallet"], {"name": short(r.get("userName"), r["proxyWallet"]),
                                                        "cats": {}})
                w["cats"][cat] = {"rank": int(r["rank"]), "pnl": pnl, "vol": vol,
                                  "hold": pnl / vol * 100 if vol else 0.0}
    return pool


def best_skill(wallet, ecats):
    """The trader's record on THIS event's category. None = not a specialist here."""
    opts = [(c, wallet["cats"][c]) for c in ecats if c in wallet["cats"]]
    if not opts:
        return None, None
    return max(opts, key=lambda kv: kv[1]["pnl"])


# ---------------------------------------------------------------- hedges
def resolve_hedges(legs, neg):
    """legs: positions one wallet holds in one event. Returns (kept, flag)."""
    by_mkt = defaultdict(set)
    for l in legs:
        by_mkt[l["cond"]].add(l["side"].lower())
    conflict = any(len(s) > 1 for s in by_mkt.values())
    if neg:
        yes_mkts = {l["cond"] for l in legs if l["side"].lower() == "yes"}
        conflict = conflict or len(yes_mkts) > 1
    if not conflict:
        return legs, ""
    total = sum(l["stake"] for l in legs)
    top = max(legs, key=lambda l: l["stake"])
    if total and top["stake"] / total >= DOMINANT:
        return [top], "hedged"
    for l in legs:
        l["hedge"] = True
    return legs, "HEDGE"


# ---------------------------------------------------------------- freshness
def trade_history(h, now):
    """Dollars bought/sold on this outcome, last FRESH_DAYS, and days since last buy."""
    rows = get("https://data-api.polymarket.com/activity"
               f"?user={h['wallet']}&market={h['cond']}&type=TRADE&limit=500")
    if not isinstance(rows, list):
        h["hist_ok"] = False
        return
    cut = now.timestamp() - FRESH_DAYS * 86400
    mine = [r for r in rows if str(r.get("asset")) == str(h["asset"])]
    buys = [r for r in mine if r.get("side") == "BUY"]
    net7 = sum(fnum(r.get("usdcSize")) * (1 if r.get("side") == "BUY" else -1)
               for r in mine if fnum(r.get("timestamp")) >= cut)
    last_buy = max((fnum(r["timestamp"]) for r in buys), default=0)
    first_buy = min((fnum(r["timestamp"]) for r in buys), default=0)
    h["hist_ok"] = True
    h["new7"] = max(net7, 0.0)
    h["fresh_share"] = min(h["new7"] / h["stake"], 1.0) if h["stake"] else 0.0
    h["since_buy"] = (now.timestamp() - last_buy) / 86400 if last_buy else None
    # 500 rows all inside the window means older history was cut off — age unknown
    h["age"] = None if len(rows) >= 500 or not first_buy else (now.timestamp() - first_buy) / 86400


# ---------------------------------------------------------------- scoring
def score(h):
    """Geometric blend (a zero on any axis can't be bought off) × trait multipliers.
    Multipliers are UNVALIDATED starting guesses — the ledger exists to test them."""
    hold = max(0.0, min(h["hold"] / 5.0, 1.0))
    floor = MIN_PNL[h["cat"]] / 5
    pnl = max(0.0, min(math.log10(max(h["pnl"], 1) / floor) /
                       math.log10(PNL_FULL[h["cat"]] / floor), 1.0))
    stake = max(0.0, min(math.log10(max(h["stake"], 1) / 1e3) / math.log10(200), 1.0))
    base = ((hold + 0.05) * (pnl + 0.05) * (stake + 0.05)) ** (1 / 3.0)
    m, why = 1.0, []
    if h.get("fresh_share", 0) >= FRESH_SHARE:
        m *= 1.25; why.append(f"new money {m0(h['new7'])}/7d")
    elif h.get("since_buy") is not None and h["since_buy"] > STALE_DAYS:
        m *= 0.80; why.append(f"stale {h['since_buy']:.0f}d")
    if h["dog"]:
        m *= 1.20; why.append("dog")
    if h.get("vs_avg") and h["vs_avg"] >= 3:
        m *= 1.10; why.append(f"{h['vs_avg']:.0f}x usual")
    if h["chase"]:
        m *= 0.80; why.append(f"ran {h['entry']:.2f}→{h['now']:.2f}")
    if h["hedge_flag"]:
        why.append(h["hedge_flag"])
    h["why"] = why
    # normalise by the best achievable multiplier so 100 means "maxed on every
    # axis" — an uncapped product saturated half the board at 100
    return min(base * m / M_MAX * 100, 100)


M_MAX = 1.25 * 1.20 * 1.10


def tier(h):
    if h.get("fresh_share", 0) >= FRESH_SHARE and h["stake"] >= 10_000:
        return "NEW$"
    if h["stake"] >= 100_000:
        return "SIZE"
    return "EDGE"


# ---------------------------------------------------------------- main
def main():
    argv = sys.argv[1:]
    show_all = "--all" in argv
    max_days = float(argv[argv.index("--days") + 1]) if "--days" in argv else MAX_DAYS
    cats = []
    for a in argv:
        c = ALIAS.get(a.lower())
        if c and c not in cats:
            cats.append(c)
    cats = cats or list(CATS)
    if not take_lock():
        return 1

    now = datetime.now(timezone.utc)
    t0 = time.time()
    markets, n_events = load_slate(cats, now, max_days)
    if not markets:
        if STATS["ok"] == 0:
            print("NETWORK FAILURE — no data retrieved. This is not 'no markets'.")
            return 2
        print(f"No open events >= {m0(MIN_EVENT_LIQ)} liquidity resolving within {max_days:.0f}d.")
        return 0

    pool = load_pool(cats)
    if not pool:
        print("NETWORK FAILURE — leaderboard returned nothing. This is not 'no signal'.")
        return 2
    wallets = list(pool)
    raw, bad, medians = [], [], {}

    def sweep(w):
        rs = get(f"https://data-api.polymarket.com/positions?user={w}&limit=100"
                 "&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(rs, list):
            bad.append(w)
            return
        sizes = [fnum(p.get("initialValue")) for p in rs if fnum(p.get("currentValue")) > 0]
        if len(sizes) >= 5:
            medians[w] = (statistics.median(sizes), len(sizes))
        for p in rs:
            mk = markets.get(p.get("conditionId"))
            if not mk or fnum(p.get("currentValue")) <= 0:
                continue
            cat, sk = best_skill(pool[w], mk["cats"])
            if not sk:
                continue
            stake = fnum(p.get("initialValue"))
            if stake < 1000:
                continue
            avg, cur = fnum(p.get("avgPrice")), fnum(p.get("curPrice"))
            raw.append({
                "wallet": w, "user": pool[w]["name"], "cat": cat, "rank": sk["rank"],
                "pnl": sk["pnl"], "vol": sk["vol"], "hold": sk["hold"],
                "cond": p["conditionId"], "asset": p.get("asset"), "event": mk["event"],
                "title": mk["title"], "question": mk["question"],
                "outcome_name": mk["outcome_name"], "side": p.get("outcome") or "",
                "neg": mk["neg"], "days": mk["days"], "mkt_liq": mk["mkt_liq"],
                "event_liq": mk["event_liq"],
                "stake": stake, "entry": avg, "now": cur,
                "dog": 0 < cur < DOG_PRICE,
                "decided": cur >= DECIDED_PX or cur <= 1 - DECIDED_PX,
                "chase": avg > 0 and (cur - avg) / avg >= CHASE,
            })

    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(sweep, wallets))

    # hedges: one wallet, one event
    groups = defaultdict(list)
    for h in raw:
        groups[(h["wallet"], h["event"])].append(h)
    hits, n_hedge = [], 0
    for legs in groups.values():
        kept, flag = resolve_hedges(legs, legs[0]["neg"])
        if flag == "HEDGE":
            n_hedge += 1
        for h in kept:
            h["hedge_flag"] = flag
            label = h["outcome_name"] or h["question"]
            h["play"] = label if h["side"].lower() == "yes" else f"{h['side'].upper()}: {label}"
            med = medians.get(h["wallet"])
            h["vs_avg"] = h["stake"] / med[0] if med and med[0] else None
            h["vs_n"] = med[1] if med else 0
            hits.append(h)

    def gate(h):
        return (h["hedge_flag"] != "HEDGE" and not h["decided"]
                and h["hold"] >= max(MIN_HOLD, MM_HOLD) and h["pnl"] >= MIN_PNL[h["cat"]]
                and h["stake"] >= MIN_STAKE and MIN_NOW <= h["now"] <= MAX_NOW
                and h["mkt_liq"] >= MIN_MKT_LIQ)

    # trade history only for survivors of the cheap gates — keeps request count sane
    cands = [h for h in hits if gate(h)] if not show_all else \
            [h for h in hits if not h["decided"] and h["hedge_flag"] != "HEDGE"]
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        list(ex.map(lambda h: trade_history(h, now), cands))
    hist_fail = sum(1 for h in cands if h.get("hist_ok") is False)
    for h in hits:
        h["conv"] = score(h)
        h["tier"] = tier(h)
    passes = lambda h: gate(h) and h["conv"] >= MIN_SCORE
    good = sorted([h for h in cands if passes(h)], key=lambda h: -h["conv"])

    # diff vs last run
    key = lambda h: f"{h['wallet']}|{h['cond']}|{h['side']}"
    try:
        prev = json.load(open(SNAP)).get("positions", {})
    except Exception:
        prev = {}
    for h in hits:
        k = key(h)
        h["delta"] = "" if not prev else "NEW" if k not in prev else \
            (f"+{m0(h['stake'] - prev[k])}" if h["stake"] > prev[k] * 1.02 else "")

    secs = time.time() - t0
    print(f"MACRO BOARD · {now.astimezone():%a %b %-d %-I:%M %p %Z} · {', '.join(c.title() for c in cats)}")
    print(f"{n_events} events >= {m0(MIN_EVENT_LIQ)} liq, resolving <= {max_days:.0f}d · "
          f"{len(wallets)-len(bad)}/{len(wallets)} wallets · {secs:.0f}s")
    if bad:
        print(f"!! {len(bad)} wallet lookups FAILED — board is PARTIAL, not a clean read")
    if hist_fail:
        print(f"!! {hist_fail} trade-history lookups failed — freshness unknown on those rows")
    print(f"{len(hits)} specialist positions · {n_hedge} hedged books excluded · "
          f"{len(good)} clear the bar (score {MIN_SCORE:.0f}+)\n")

    if show_all:
        rows = sorted(cands, key=lambda h: -h["conv"])
        for h in rows[:60]:
            q = "*" if passes(h) else " "
            print(f"{q}[{h['conv']:4.0f}] {h['tier']:4} {m0(h['stake']):>7} on {h['play'][:34]:34} "
                  f"now {h['now']:.2f} ({am(h['now'])})  {max(h['days'], 0):.0f}d · "
                  f"{h['user']} #{h['rank']} {h['cat'][:4].title()} {h['hold']:.1f}%"
                  + (f" · {', '.join(h['why'])}" if h["why"] else "") + f"  | {h['title'][:40]}")
    else:
        # one row per (event, view): the lead specialist plus everyone agreeing
        views = defaultdict(list)
        for h in good:
            k, label, px = direction(h, markets)
            h["view"], h["view_label"], h["view_px"] = k, label, px
            views[k].append(h)
        def opposes(h, o):
            """Only a DIFFERENT trader on a conflicting view counts as contest. Neg-risk
            outcomes are mutually exclusive; ladder markets ("BTC hit 70K", "75K") are
            not, so there only the other side of the same market conflicts."""
            if o["wallet"] == h["wallet"] or o["view"] == h["view"]:
                return False
            if h["neg"]:
                return True
            return o["view"][1] == h["view"][1]
        groups = sorted(views.values(), key=lambda hs: (-max(h["conv"] for h in hs),
                                                        -sum(h["stake"] for h in hs)))
        if not groups:
            print("Nothing clears the bar.")
        for hs in groups[:15]:
            hs.sort(key=lambda h: -h["conv"])
            lead = hs[0]
            total = sum(h["stake"] for h in hs)
            new7 = sum(h.get("new7", 0) for h in hs)
            opp = [o for o in good if o["event"] == lead["event"] and opposes(lead, o)]
            against, n_against = sum(o["stake"] for o in opp), len({o["wallet"] for o in opp})
            px = lead["view_px"]
            marks = [h["delta"] for h in hs if h["delta"]]
            print(f"[{lead['conv']:3.0f}] {m0(total):>7} · {len(hs)} specialist{'s' if len(hs) > 1 else ' '} "
                  f"on {lead['view_label'][:36]:36} now {px:.2f} ({am(px)}) · resolves {max(lead['days'], 0):.0f}d"
                  + (f"  <<{len(marks)} new/added" if marks else ""))
            print(f"      {lead['title'][:74]}")
            line = f"      new money {m0(new7)} in 7d" if new7 >= 1000 else "      no new money this week"
            if against:
                line += f" · CONTESTED: {m0(against)} from {n_against} specialist(s) on other outcomes"
            print(line)
            age = f", held {lead['age']:.0f}d" if lead.get("age") else ""
            print(f"      lead: {lead['user']} #{lead['rank']} {lead['cat'].title()} · {lead['hold']:.1f}% hold · "
                  f"{m0(lead['pnl'])} lifetime · {m0(lead['stake'])} @ {lead['entry']:.2f}{age}"
                  + (f" · {', '.join(lead['why'])}" if lead["why"] else ""))
            if len(hs) > 1:
                print("      with: " + ", ".join(f"{h['user']} {m0(h['stake'])}" for h in hs[1:5])
                      + (f" +{len(hs) - 5} more" if len(hs) > 5 else ""))
            print(f"      look on: {VENUES[lead['cat']]}   (Polymarket price = reference only)\n")
        if len(groups) > 15:
            print(f"(+{len(groups) - 15} more views below the top 15 — --all for everything)\n")

    # ledger — every qualifier, with the token id so price drift can be measured later
    seen = set()
    try:
        for line in open(LEDGER):
            r = json.loads(line)
            seen.add(f"{r['wallet']}|{r['cond']}|{r['side']}")
    except FileNotFoundError:
        pass
    with open(LEDGER, "a") as f:
        for h in good:
            if key(h) in seen:
                continue
            f.write(json.dumps({
                "at": now.isoformat(), "wallet": h["wallet"], "user": h["user"], "cat": h["cat"],
                "event": h["event"], "title": h["title"], "cond": h["cond"], "asset": h["asset"],
                "side": h["side"], "play": h["play"], "stake": h["stake"], "entry": h["entry"],
                "price_at_flag": h["now"], "conv": round(h["conv"], 1), "tier": h["tier"],
                "dog": h["dog"], "fresh_share": round(h.get("fresh_share", 0), 2),
                "hold": round(h["hold"], 2), "pnl": h["pnl"], "days_to_resolve": round(h["days"], 1),
            }) + "\n")
    try:
        json.dump({"at": now.isoformat(), "positions": {key(h): h["stake"] for h in hits}}, open(SNAP, "w"))
    except Exception:
        pass
    print("Informational only — what anonymous traders hold. Not advice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
