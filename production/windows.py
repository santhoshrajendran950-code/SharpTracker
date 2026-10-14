#!/usr/bin/env python3
"""
Time-window alerts, to Santhosh only. Two jobs, one cheap check.

  PRE-SLATE   one hour before the day's first kickoff — the whole board at
              score >= THRESHOLD, once per day, while everything is bettable.
  CLOSING     fifteen minutes before each game — that game's qualifying plays,
              once per game, then change alerts until kickoff.

Run every few minutes. The expensive part (≈1,080 wallet lookups) only happens
when a window is actually open; the rest of the time this builds the slate from
gamma-api and exits, which is cheap.

    python3 windows.py            # act, and print what it would send
    python3 windows.py --dry-run  # never write state, never emit
    python3 windows.py --status   # show today's state and next windows
"""
import importlib.util
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("chk", os.path.join(HERE, "check.py"))
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)

THRESHOLD = 75.0          # "high conviction" for both windows
PRESLATE_LEAD = 60        # minutes before the day's first start
CLOSING_LEAD = 15         # minutes before an individual game
CLOSING_GRACE = 2         # still fire if we're a couple of minutes late
MOVE_PCT = 0.10           # stake move that counts as a change worth sending
STATE = os.path.join(HERE, "windows-state.json")
OUTBOX = os.path.join(HERE, "windows-outbox.json")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"preslate_date": None, "games": {}}


def save_state(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


def build_slate(now):
    """Every game from now to end of local day. gamma-api only — cheap."""
    end = now.astimezone().replace(hour=23, minute=59, second=59)
    horizon = max((end - now.astimezone()).total_seconds() / 3600, 0.5)
    slate = {}
    for tag in chk.TAGS:
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
            if mins < 0 or mins / 60 > horizon:
                continue
            slug = e["slug"]
            lg = chk.league_for(tag, slug)
            slate.setdefault(slug, {"league": lg, "title": e.get("title") or slug,
                                    "start": st})
    return slate


def sweep(slate):
    """The expensive half. Returns qualifying rows only."""
    hits, bad, pool_n = chk.scan_positions(slate) if hasattr(chk, "scan_positions") \
        else _inline_sweep(slate)
    return hits, bad, pool_n


def _inline_sweep(slate):
    from concurrent.futures import ThreadPoolExecutor
    pool = chk.build_pool()
    slugs = list(slate)
    hits, bad = [], []

    def one(tr):
        rs = chk.get("https://data-api.polymarket.com/positions"
                     f"?user={tr['proxyWallet']}&limit=100&sortBy=CURRENT&sortDirection=DESC")
        if not isinstance(rs, list):
            bad.append(1)
            return
        sizes = sorted(z.get("initialValue") or 0 for z in rs
                       if (z.get("initialValue") or 0) > 0)
        typ = sizes[len(sizes) // 2] if sizes else 0
        for p in rs:
            if (p.get("currentValue") or 0) <= 0:
                continue
            stake = p.get("initialValue") or 0
            if stake < 1000:
                continue
            es = p.get("eventSlug") or ""
            base = next((x for x in slugs if es == x or es.startswith(x + "-")), None)
            if not base:
                continue
            vol = tr.get("vol") or 0
            avg, cur = p.get("avgPrice") or 0, p.get("curPrice") or 0
            hits.append({
                "wallet": tr["proxyWallet"],
                "user": chk.short(tr.get("userName"), tr["proxyWallet"]),
                "rank": int(tr["rank"]), "pnl": tr.get("pnl") or 0, "vol": vol,
                "hold": (tr["pnl"] / vol * 100) if vol else 0.0,
                "month_pnl": tr.get("month_pnl"), "month_vol": tr.get("month_vol"),
                "slug": base, "league": slate[base]["league"],
                "game": slate[base]["title"], "start": slate[base]["start"],
                "market": p.get("title"), "side": p.get("outcome"),
                "stake": stake, "entry": avg, "now": cur,
                "dog": 0 < avg < chk.DOG_PRICE,
                "decided": cur >= chk.DECIDED_PX or cur <= (1 - chk.DECIDED_PX),
                "chalk": avg >= chk.MAX_ENTRY,
                "fresh": avg > 0 and abs(cur - avg) / avg <= chk.FRESH_DRIFT,
                "typical": typ,
                "vs_typical": (stake / typ) if typ else None,
            })

    with ThreadPoolExecutor(chk.CONCURRENCY) as ex:
        list(ex.map(one, pool))

    groups = defaultdict(list)
    for h in hits:
        groups[(h["wallet"], h["slug"])].append(h)
    for mem in groups.values():
        agree = chk.sides_agree(mem)
        anyd = any(m["dog"] for m in mem)
        tot = sum(m["stake"] for m in mem)
        book = chk.two_sided(mem)
        for m in mem:
            m["stack"] = len({x["market"] for x in mem}) if agree else 1
            m["stack_stake"] = tot if agree else m["stake"]
            m["stack_dog"] = anyd if agree else m["dog"]
            m["two_sided"] = book
    chk.annotate_record(hits)             # thin / cold, 2026-10-14
    for h in hits:
        h["conv"] = chk.conviction(h)
    return hits, len(bad), len(pool)


def qualifies(h):
    return (h["conv"] >= THRESHOLD and not h["decided"] and not h.get("two_sided")
            and h["hold"] >= chk.MIN_HOLD and h["pnl"] >= chk.MIN_PNL
            and h["stake"] >= chk.MIN_STAKE
            and chk.MIN_ENTRY <= h["entry"] <= chk.MAX_ENTRY)


def _k(n):
    """Compact money — keeps email lines phone-width."""
    if n is None:
        return "—"
    a = abs(n)
    return f"${n/1e6:.1f}M" if a >= 1e6 else (f"${n/1e3:.0f}K" if a >= 1e3 else f"${n:,.0f}")


def fmt_play(h, now):
    """One short paragraph per play. A wide table wraps unreadably on a phone."""
    mins = (h["start"] - now).total_seconds() / 60
    when = "LIVE" if mins < 0 else f"{int(mins//60)}h{int(mins%60):02d}m"
    vt = h.get("vs_typical")
    out = [f"[{h['conv']:.0f}]  {_k(h['stake'])} on {h['side']}  {chk.am(h['entry'])}",
           f"   {h['market'][:46]}",
           f"   {h['league']} · {h['start'].astimezone():%-I:%M %p} ({when})",
           f"   {h['user']} · {h['hold']:.1f}% hold"
           + (f" · {vt:.1f}x typical" if vt else ""),
           f"   P/L: {_k(h.get('month_pnl'))} month · {_k(h['pnl'])} lifetime"]
    if h.get("stack", 1) >= chk.STACK_MIN:
        out.append(f"   >> stacked x{h['stack']} ({_k(h['stack_stake'])} on this game)")
    if h.get("stack_dog", h["dog"]):
        out.append("   >> underdog")
    if h["fresh"]:
        out.append("   >> entered at current price")
    return "\n".join(out)


def main():
    global THRESHOLD
    global CLOSING_LEAD
    if "--threshold" in sys.argv:        # testing / tuning override
        THRESHOLD = float(sys.argv[sys.argv.index("--threshold") + 1])
    if "--lead" in sys.argv:             # testing: widen the closing window
        CLOSING_LEAD = float(sys.argv[sys.argv.index("--lead") + 1])
    dry = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    st = load_state()
    today = now.astimezone().strftime("%Y-%m-%d")

    slate = build_slate(now)
    if not slate:
        if "--status" in sys.argv:
            print("no upcoming games today")
        return 0

    first = min(s["start"] for s in slate.values())
    mins_to_first = (first - now).total_seconds() / 60
    preslate_due = (st.get("preslate_date") != today
                    and PRESLATE_LEAD - CLOSING_GRACE <= mins_to_first <= PRESLATE_LEAD + 5)

    closing = {sl: s for sl, s in slate.items()
               if -CLOSING_GRACE <= (s["start"] - now).total_seconds() / 60 <= CLOSING_LEAD}

    if "--status" in sys.argv:
        print(f"now {now.astimezone():%-I:%M %p} · {len(slate)} games left today")
        print(f"first kickoff {first.astimezone():%-I:%M %p} (in {mins_to_first:.0f}m)"
              f" · pre-slate {'DUE' if preslate_due else 'sent' if st.get('preslate_date')==today else 'pending'}")
        print(f"games inside the {CLOSING_LEAD}m closing window: {len(closing)}")
        for sl in closing:
            print(f"   {slate[sl]['title'][:44]} — t15_sent="
                  f"{st['games'].get(sl,{}).get('t15_sent', False)}")
        return 0

    if not preslate_due and not closing:
        return 0                      # nothing due — no sweep, no cost

    rel = chk.sweep_lock("windows", wait=90)
    if rel is None:
        print("another sweep is running — skipping this cycle")
        return 0
    try:
        hits, bad, pool_n = _inline_sweep(slate)
    finally:
        rel()
    good = [h for h in hits if qualifies(h)]
    good.sort(key=lambda h: -h["conv"])
    emails = []

    if preslate_due:
        rows = good
        body = [f"PRE-SLATE BOARD · {now.astimezone():%a %-I:%M %p %Z}",
                f"first kickoff {first.astimezone():%-I:%M %p} (in {mins_to_first:.0f} min)",
                f"{len(slate)} games today · {pool_n-bad}/{pool_n} wallets"
                + (f" · !! {bad} FAILED, PARTIAL" if bad else ""),
                f"{len(rows)} plays scoring {THRESHOLD:.0f}+", ""]
        body += [fmt_play(h, now) + "\n" for h in rows] or ["Nothing clears the bar yet.\n"]
        body.append("Informational only — what anonymous traders hold. Not advice.")
        emails.append((f"Pre-Slate Board — {len(rows)} play(s) {THRESHOLD:.0f}+",
                       "\n".join(body)))
        if not dry:
            st["preslate_date"] = today

    for sl, g in closing.items():
        rows = [h for h in good if h["slug"] == sl]
        gs = st["games"].setdefault(sl, {"t15_sent": False, "positions": {}})
        cur = {f"{h['wallet']}|{h['market']}|{h['side']}": h["stake"] for h in rows}
        mins = (g["start"] - now).total_seconds() / 60

        if not gs["t15_sent"]:
            if not rows:
                if not dry:
                    gs["t15_sent"] = True      # nothing to say; don't keep checking
                continue
            body = [f"CLOSING LOOK · {g['title']}",
                    f"starts {g['start'].astimezone():%-I:%M %p %Z} (in {mins:.0f} min)",
                    f"{len(rows)} play(s) scoring {THRESHOLD:.0f}+", ""]
            body += [fmt_play(h, now) + "\n" for h in rows]
            body.append("Informational only — what anonymous traders hold. Not advice.")
            emails.append((f"Closing Look — {g['title']}", "\n".join(body)))
            if not dry:
                gs["t15_sent"] = True
                gs["positions"] = cur
            continue

        prev = gs.get("positions", {})
        changes = []
        for k, v in cur.items():
            p = prev.get(k)
            if p is None:
                changes.append(f"NEW  {k.split('|')[-1]}  {chk.m0(v)}")
            elif p > 0 and abs(v - p) / p >= MOVE_PCT:
                changes.append(f"{'UP  ' if v > p else 'DOWN'} {k.split('|')[-1]}  "
                               f"{chk.m0(p)} -> {chk.m0(v)}")
        for k, v in prev.items():
            if k not in cur:
                changes.append(f"GONE {k.split('|')[-1]}  was {chk.m0(v)}")
        if changes:
            body = [f"CHANGE · {g['title']}",
                    f"starts {g['start'].astimezone():%-I:%M %p %Z} (in {mins:.0f} min)", ""]
            body += changes + ["", "Current board for this game:", ""]
            body += [fmt_play(h, now) + "\n" for h in rows]
            emails.append((f"Closing Change — {g['title']}", "\n".join(body)))
        if not dry:
            gs["positions"] = cur

    if not dry:
        save_state(st)
    # Write the queue as JSON for the shell runner rather than making it parse
    # stdout — one file, one contract, nothing to misparse.
    with open(OUTBOX, "w") as f:
        json.dump([{"subject": s_, "body": b_} for s_, b_ in emails], f, indent=1)
    for subj, body in emails:
        print(f"=== {subj} ===")
        print(body)
        print()
    print(f"[{len(emails)} email(s) queued in {OUTBOX}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
