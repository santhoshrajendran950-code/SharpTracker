#!/usr/bin/env python3
"""
Catalyst alert — new specialist money on US economic-release markets.

    python3 catalyst.py              # the board: every release <= 45d, specialist consensus
    python3 catalyst.py --alert      # print ONLY views with new specialist money since the
                                     # last --alert run; silence = nothing new (for alert-catalyst.sh)
    python3 catalyst.py --days 14    # tighter window

Why this exists (2026-09-14): the macro board is long-dated. The short-term,
information-driven, bettable overlap is scheduled US releases — Fed decisions,
CPI, jobs, PCE, PPI, GDP, ISM — which Robinhood, FanDuel Predicts and DraftKings
Predictions all list. The Fed September hike showed the problem with a daily
board: specialists bought 0.49–0.58 and moved it to 0.85 before anyone looked.
Value is catching the money as it arrives, so this diffs every 15 minutes.

Liquidity reality, probed 2026-09-14: Fed decisions are deep ($12.9M Sep, $785K
Oct); every other release market is thin ($5K–$30K). So there is no liquidity
floor — the trigger is dollars added, and each alert prints the market's depth.

Prices: Polymarket is REFERENCE ONLY. Kalshi's public book is shown as the proxy
for Robinhood, which routes much of its event-contract flow there (Robinhood adds
its own per-contract fee). FanDuel Predicts (CME) and DK Predictions have no
public price feed — check those by hand.
"""
import json, re, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import macro
from macro import get, get_all, fnum, m0, am

CAT = "ECONOMICS"
POOL = 300
HORIZON_DAYS = 45            # covers the next print of every monthly release + next FOMC
ADD_MIN_WALLET = 5_000       # one specialist must add at least this to count
ADD_MIN_VIEW = 10_000        # total added on one view to fire an alert
MAX_ALERT_PX = 0.92          # already-chalk outcomes don't fire — nothing left to bet
SNAP = "catalyst-alert-snapshot.json"
BOARD_SNAP = "catalyst-snapshot.json"
LEDGER = "catalyst-signals.jsonl"
BET_ON = "Robinhood · FanDuel Predicts · DK Predictions"

# (title pattern, Kalshi series). Only events matching one of these are in scope:
# this list IS the definition of "a US release you can bet on a retail DCM".
CATALYSTS = [
    (r"^Fed Decision in (\w+)", "KXFEDDECISION"),
    (r"Inflation US - Monthly", "KXCPI"),
    (r"Inflation US - Annual", "KXCPIYOY"),
    (r"^Core CPI MoM", "KXCPICORE"),
    (r"^Core CPI YoY", "KXCPICOREYOY"),
    (r"jobs added in", "KXPAYROLLS"),
    (r"^(\w+) Unemployment Rate$", "KXU3"),
    (r"^Core PCE MoM", "KXPCECORE"),
    (r"^Core PCE YoY", None),
    (r"^PPI YoY", "KXUSPPIYOY"),
    (r"^PPI MoM", "KXUSPPI"),
    (r"^US GDP|^GDP growth in Q", "KXGDP"),
    (r"^ISM Manufacturing PMI", "KXISMPMI"),
    (r"^ISM Services PMI", "KXUSISMSERV"),
    (r"^Fed rate hike by", None),
]
NON_US = {"canada", "china", "argentina", "south-korea", "brazil", "japan", "united-kingdom",
          "eurozone", "europe", "mexico", "colombia", "india", "israel", "russia", "australia"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


# ---------------------------------------------------------------- slate
def load_slate(now, horizon):
    events = {}
    for tag in ["economy", "fed-rates", "fomc", "cpi", "inflation", "jobs-report", "pce",
                "gdp", "unemployment", "ppi", "ism"]:
        for e in get_all(f"https://gamma-api.polymarket.com/events?closed=false&tag_slug={tag}"):
            events.setdefault(e["slug"], e)
    markets, evinfo = {}, {}
    for slug, e in events.items():
        title = e.get("title") or ""
        hit = next(((p, s) for p, s in CATALYSTS if re.search(p, title, re.I)), None)
        tags = {t.get("slug") for t in e.get("tags") or []}
        if not hit or tags & NON_US:
            continue
        try:
            end = datetime.fromisoformat((e.get("endDate") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        days = (end - now).total_seconds() / 86400
        if days < -1 or days > horizon:
            continue
        evinfo[slug] = {"title": title, "end": end, "days": days, "series": hit[1],
                        "liq": fnum(e.get("liquidity")), "neg": bool(e.get("negRisk"))}
        for m in e.get("markets") or []:
            if m.get("closed") or not m.get("conditionId"):
                continue
            markets[m["conditionId"]] = {
                "event": slug, "title": title, "cats": [CAT], "neg": bool(e.get("negRisk")),
                "end": end, "days": days, "event_liq": evinfo[slug]["liq"],
                "mkt_liq": fnum(m.get("liquidityNum")),
                "outcome_name": m.get("groupItemTitle") or m.get("question") or "",
                "question": m.get("question") or "", "yes_px": macro.yes_price(m),
            }
    return markets, evinfo


# ---------------------------------------------------------------- Kalshi (≈ Robinhood)
_kalshi = {}


def kalshi_event(series, title, end):
    """The Kalshi event for the same release month, or None. Tickers end -YYMON."""
    if not series:
        return None
    low = title.lower()
    month = next((i + 1 for i, m in enumerate(MONTHS) if m in low), None)
    if not month:
        return None
    year = end.year if month <= end.month else end.year - 1   # "December CPI" prints in January
    suffix = f"-{year % 100:02d}{MONTHS[month - 1][:3].upper()}"
    if series not in _kalshi:
        d = get("https://api.elections.kalshi.com/trade-api/v2/events"
                f"?series_ticker={series}&status=open&with_nested_markets=true&limit=20", retries=2)
        _kalshi[series] = (d or {}).get("events") or []
    hit = next((ev for ev in _kalshi[series] if ev["event_ticker"].endswith(suffix)), None)
    if hit:
        return hit
    # some series key on RELEASE date instead (KXUSPPI-26OCT15): match within 3 days
    for ev in _kalshi[series]:
        mm = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", ev["event_ticker"])
        if not mm or mm.group(2).lower() not in [x[:3] for x in MONTHS]:
            continue
        d = datetime(2000 + int(mm.group(1)), [x[:3] for x in MONTHS].index(mm.group(2).lower()) + 1,
                     int(mm.group(3)), tzinfo=timezone.utc)
        if abs((d - end).total_seconds()) <= 3 * 86400:
            return ev
    return None


def fed_key(label):
    """Normalise both venues' Fed outcome labels to cut50/cut25/hold/hike25/hike50."""
    s = label.lower()
    if "maintain" in s or "no change" in s or "0bps" in s.replace(" ", ""):
        return "hold"
    big = ">25" in s.replace(" ", "") or "50+" in s.replace(" ", "")
    if "cut" in s or "decrease" in s:
        return "cut50" if big else "cut25"
    if "hike" in s or "increase" in s:
        return "hike50" if big else "hike25"
    return None


def kalshi_line(ev_info, view_label, side_yes=True):
    ev = kalshi_event(ev_info["series"], ev_info["title"], ev_info["end"])
    if not ev:
        return "Kalshi: no matching market found" if ev_info["series"] else ""
    mk = ev.get("markets") or []
    if ev_info["series"] == "KXFEDDECISION":
        k = fed_key(view_label)
        m = next((x for x in mk if fed_key(x.get("yes_sub_title") or "") == k), None)
        if m:
            bid, ask = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
            if not side_yes:
                bid, ask = 1 - ask, 1 - bid
            return (f"Kalshi {'YES' if side_yes else 'NO'} {m.get('yes_sub_title')}: "
                    f"{bid:.2f} bid / {ask:.2f} ask ({am(ask)})  ≈ Robinhood")
    # threshold ladders: show the contested strikes so buckets can be compared by eye
    live = [x for x in mk if 0.05 <= fnum(x.get("yes_ask_dollars")) <= 0.95]
    if not live:
        return f"Kalshi {ev['event_ticker']}: no contested strikes"
    parts = [f"{x.get('yes_sub_title')} {fnum(x.get('yes_ask_dollars')):.2f}" for x in live[:6]]
    return f"Kalshi ladder (YES ask, ≈ Robinhood): " + " · ".join(parts)


# ---------------------------------------------------------------- core sweep
def sweep(now, horizon):
    markets, evinfo = load_slate(now, horizon)
    if not markets:
        return None
    pool = {}
    for off in range(0, POOL, macro.PAGE):
        rows = get("https://data-api.polymarket.com/v1/leaderboard"
                   f"?category={CAT}&timePeriod=ALL&orderBy=PNL&limit={macro.PAGE}&offset={off}")
        if not rows:
            break
        for r in rows:
            vol, pnl = fnum(r.get("vol")), fnum(r.get("pnl"))
            pool[r["proxyWallet"]] = {"name": macro.short(r.get("userName"), r["proxyWallet"]),
                                      "cats": {CAT: {"rank": int(r["rank"]), "pnl": pnl, "vol": vol,
                                                     "hold": pnl / vol * 100 if vol else 0.0}}}
    raw, bad = [], set()

    def one(w):
        rs = get(f"https://data-api.polymarket.com/positions?user={w}&limit=100"
                 "&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(rs, list):
            bad.add(w)
            return
        sk = pool[w]["cats"][CAT]
        for p in rs:
            mk = markets.get(p.get("conditionId"))
            if not mk or fnum(p.get("currentValue")) <= 0 or fnum(p.get("initialValue")) < 500:
                continue
            cur = fnum(p.get("curPrice"))
            label = mk["outcome_name"] or mk["question"]
            side = p.get("outcome") or ""
            raw.append({
                "wallet": w, "user": pool[w]["name"], "cat": CAT, "rank": sk["rank"],
                "pnl": sk["pnl"], "vol": sk["vol"], "hold": sk["hold"],
                "cond": p["conditionId"], "asset": p.get("asset"), "event": mk["event"],
                "title": mk["title"], "question": mk["question"], "outcome_name": mk["outcome_name"],
                "side": side, "neg": mk["neg"], "days": mk["days"], "mkt_liq": mk["mkt_liq"],
                "stake": fnum(p.get("initialValue")), "entry": fnum(p.get("avgPrice")), "now": cur,
                "decided": cur >= macro.DECIDED_PX or cur <= 1 - macro.DECIDED_PX,
                "play": label if side.lower() == "yes" else f"{side.upper()}: {label}",
            })

    import check as chk
    # Same endpoint, same bucket as bigboard/check/windows: hold the sweep lock
    # instead of the old pgrep wait, which let a board start mid-catalyst and
    # cost it 20-30 wallets (2026-10-13).
    rel = chk.sweep_lock("catalyst", wait=300)
    try:
        with ThreadPoolExecutor(macro.CONCURRENCY) as ex:
            list(ex.map(one, pool))
    finally:
        (rel or (lambda: None))()

    groups = defaultdict(list)
    for h in raw:
        groups[(h["wallet"], h["event"])].append(h)
    hits, n_hedge = [], 0
    for legs in groups.values():
        kept, flag = macro.resolve_hedges(legs, legs[0]["neg"])
        if flag == "HEDGE":
            n_hedge += 1
            continue
        for h in kept:
            h["hedge_flag"] = flag
            h["view"], h["view_label"], h["view_px"] = macro.direction(h, markets)
            h["view_yes"] = h["view"][2] == "yes"
            if h["view"][1] == h["cond"]:          # not remapped: plain label, side kept separately
                h["view_label"] = h["outcome_name"] or h["question"]
            hits.append(h)
    quality = [h for h in hits if not h["decided"] and h["hold"] >= macro.MIN_HOLD
               and h["pnl"] >= macro.MIN_PNL[CAT] and h["stake"] >= 1_000]
    return {"markets": markets, "evinfo": evinfo, "pool": pool, "bad": bad,
            "hits": hits, "quality": quality, "n_hedge": n_hedge}


def recent_buys(h, since_ts):
    """Dollars bought on this outcome since since_ts, and their average price."""
    rows = get("https://data-api.polymarket.com/activity"
               f"?user={h['wallet']}&market={h['cond']}&type=TRADE&limit=200")
    if not isinstance(rows, list):
        return None, None
    buys = [r for r in rows if str(r.get("asset")) == str(h["asset"]) and r.get("side") == "BUY"
            and fnum(r.get("timestamp")) >= since_ts]
    usd = sum(fnum(r.get("usdcSize")) for r in buys)
    shares = sum(fnum(r.get("size")) for r in buys)
    return usd, (usd / shares if shares else None)


def key(h):
    return f"{h['wallet']}|{h['cond']}|{h['side']}"


def consensus(quality, event):
    by = defaultdict(lambda: [0.0, set()])
    for h in quality:
        if h["event"] == event:
            by[(h["view_label"], h["view_yes"])][0] += h["stake"]
            by[(h["view_label"], h["view_yes"])][1].add(h["wallet"])
    return sorted(((lab, yes, usd, len(ws)) for (lab, yes), (usd, ws) in by.items()), key=lambda r: -r[2])


def fmt_view(label, yes):
    return label if yes else f"NO: {label}"


def load_snap(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_snap(path, now, res, prev):
    """Carry forward entries for wallets whose lookup FAILED this run — otherwise a
    flaky 429 makes their whole book look NEW next time and fires a false alert."""
    pos = {key(h): h["stake"] for h in res["hits"]}
    for k, v in (prev.get("positions") or {}).items():
        if k.split("|", 1)[0] in res["bad"] and k not in pos:
            pos[k] = v
    with open(path, "w") as f:
        json.dump({"at": now.isoformat(), "positions": pos}, f)


# ---------------------------------------------------------------- modes
def run_alert(now, horizon):
    prev = load_snap(SNAP)
    res = sweep(now, horizon)
    if res is None:
        if macro.STATS["ok"] == 0:
            print("NETWORK FAILURE — no data retrieved. This is not 'nothing new'.", file=sys.stderr)
            return 2
        return 0
    if not res["pool"]:
        print("NETWORK FAILURE — leaderboard empty.", file=sys.stderr)
        return 2
    if len(res["bad"]) > len(res["pool"]) * 0.5:
        print(f"{len(res['bad'])}/{len(res['pool'])} wallet lookups failed — skipping diff, "
              "snapshot kept.", file=sys.stderr)
        return 3
    if not prev:
        save_snap(SNAP, now, res, prev)       # first run: baseline only, nothing is "new"
        return 0

    prev_pos = prev.get("positions") or {}
    prev_at = datetime.fromisoformat(prev["at"])
    # if the Mac slept for hours, don't dump a day of drift as one "new money" alert
    since = max(prev_at.timestamp(), now.timestamp() - 6 * 3600)
    adds = []
    for h in res["quality"]:
        add = h["stake"] - prev_pos.get(key(h), 0.0)
        if add >= ADD_MIN_WALLET and h["view_px"] <= MAX_ALERT_PX:
            h["add"], h["is_new"] = add, key(h) not in prev_pos
            adds.append(h)
    views = defaultdict(list)
    for h in adds:
        views[h["view"]].append(h)
    fire = {k: hs for k, hs in views.items() if sum(h["add"] for h in hs) >= ADD_MIN_VIEW}

    if fire:
        for hs in fire.values():
            for h in hs:
                h["recent_usd"], h["recent_px"] = recent_buys(h, since)
        out = []
        tot = sum(h["add"] for hs in fire.values() for h in hs)
        out.append(f"{len(fire)} catalyst view(s) with new specialist money · {m0(tot)} added · "
                   f"{now.astimezone():%a %b %-d %-I:%M %p %Z}")
        out.append(f"(since {prev_at.astimezone():%-I:%M %p} · "
                   f"{len(res['pool']) - len(res['bad'])}/{len(res['pool'])} Economics specialists read"
                   + (f" · {len(res['bad'])} lookups FAILED, read is partial" if res["bad"] else "") + ")")
        out.append("")
        for k, hs in sorted(fire.items(), key=lambda kv: -sum(h["add"] for h in kv[1])):
            hs.sort(key=lambda h: -h["add"])
            lead = hs[0]
            ev = res["evinfo"][lead["event"]]
            added = sum(h["add"] for h in hs)
            out.append(f"== {ev['title']}  ·  resolves {ev['end']:%a %b %-d} "
                       f"({max(ev['days'], 0):.0f}d)  ·  Polymarket depth {m0(ev['liq'])}")
            out.append(f"   +{m0(added)} on {fmt_view(lead['view_label'], lead['view_yes'])} "
                       f"from {len(hs)} specialist(s)")
            out.append(f"   Polymarket now {lead['view_px']:.2f} ({am(lead['view_px'])})  — reference only")
            kl = kalshi_line(ev, lead["view_label"], lead["view_yes"])
            if kl:
                out.append(f"   {kl}")
            for h in hs[:6]:
                paid = f" @ {h['recent_px']:.2f}" if h.get("recent_px") else ""
                out.append(f"     {'NEW ' if h['is_new'] else 'ADD '} +{m0(h['add'])}{paid}  "
                           f"{h['user']} #{h['rank']} · {h['hold']:.1f}% hold · {m0(h['pnl'])} lifetime · "
                           f"now holds {m0(h['stake'])} on {h['play'][:30]}")
            cons = consensus(res["quality"], lead["event"])
            if cons:
                out.append("   All specialist money on this release: " + " | ".join(
                    f"{fmt_view(l, y)} {m0(u)} ({n})" for l, y, u, n in cons[:4]))
            out.append(f"   Bet on: {BET_ON}")
            out.append("")
            with open(LEDGER, "a") as f:
                f.write(json.dumps({
                    "at": now.isoformat(), "event": lead["event"], "title": ev["title"],
                    "view": fmt_view(lead["view_label"], lead["view_yes"]),
                    "cond": lead["cond"], "asset": lead["asset"], "side": lead["side"],
                    "poly_px": lead["now"], "view_px": lead["view_px"], "added": added,
                    "n_wallets": len(hs), "wallets": [h["wallet"] for h in hs],
                    "resolves": ev["end"].isoformat(), "kalshi": kl,
                }) + "\n")
        out.append("Informational only — what anonymous traders hold. Not advice.")
        print("\n".join(out))
    save_snap(SNAP, now, res, prev)
    return 0


def run_board(now, horizon):
    t0 = time.time()
    prev = load_snap(BOARD_SNAP)
    res = sweep(now, horizon)
    if res is None:
        if macro.STATS["ok"] == 0:
            print("NETWORK FAILURE — no data retrieved. This is not 'no catalysts'.")
            return 2
        print(f"No US release markets resolving within {horizon:.0f}d.")
        return 0
    print(f"CATALYST BOARD · {now.astimezone():%a %b %-d %-I:%M %p %Z} · "
          f"{len(res['pool']) - len(res['bad'])}/{len(res['pool'])} Economics specialists · "
          f"{time.time() - t0:.0f}s")
    if res["bad"]:
        print(f"!! {len(res['bad'])} wallet lookups FAILED — board is PARTIAL")
    print(f"US releases resolving <= {horizon:.0f}d · {res['n_hedge']} hedged books excluded · "
          f"Polymarket = reference only · bet on {BET_ON}\n")
    prev_pos = prev.get("positions") or {}
    by_event = defaultdict(list)
    for h in res["quality"]:
        by_event[h["event"]].append(h)
    shown = sorted(res["evinfo"].items(), key=lambda kv: kv[1]["days"])
    quiet = []
    for slug, ev in shown:
        hs = by_event.get(slug) or []
        if not hs:
            quiet.append(ev)
            continue
        print(f"{ev['title']}  ·  resolves {ev['end']:%a %b %-d} ({max(ev['days'], 0):.0f}d)"
              f"  ·  Polymarket depth {m0(ev['liq'])}")
        views = defaultdict(list)
        for h in hs:
            views[h["view"]].append(h)
        for k, vs in sorted(views.items(), key=lambda kv: -sum(h["stake"] for h in kv[1]))[:4]:
            lead = max(vs, key=lambda h: h["stake"])
            usd = sum(h["stake"] for h in vs)
            chg = sum(h["stake"] - prev_pos.get(key(h), 0.0) for h in vs) if prev_pos else 0
            label = fmt_view(lead["view_label"], lead["view_yes"])
            print(f"   {label[:30]:30} {m0(usd):>7} · {len(vs):>2} specialist(s) · "
                  f"PM {lead['view_px']:.2f} ({am(lead['view_px'])})"
                  + (f" · {'+' if chg >= 0 else ''}{m0(chg)} since last look" if prev_pos and abs(chg) >= 1000 else ""))
            print(f"   {'':30} top: {lead['user']} #{lead['rank']} {lead['hold']:.1f}% hold, "
                  f"{m0(lead['stake'])} @ {lead['entry']:.2f}")
        top = max(views.values(), key=lambda vs: sum(h["stake"] for h in vs))[0]
        kl = kalshi_line(ev, top["view_label"], top["view_yes"])
        if kl:
            print(f"   {kl}")
        print()
    if quiet:
        print("No specialist money yet: " + " · ".join(
            f"{e['title'][:40]} ({max(e['days'], 0):.0f}d, {m0(e['liq'])})" for e in quiet))
    save_snap(BOARD_SNAP, now, res, prev)
    return 0


def main():
    argv = sys.argv[1:]
    horizon = float(argv[argv.index("--days") + 1]) if "--days" in argv else HORIZON_DAYS
    if not macro.take_lock():
        return 4 if "--alert" in argv else 1
    now = datetime.now(timezone.utc)
    return run_alert(now, horizon) if "--alert" in argv else run_board(now, horizon)


if __name__ == "__main__":
    sys.exit(main())
