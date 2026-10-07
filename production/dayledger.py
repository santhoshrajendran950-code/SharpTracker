#!/usr/bin/env python3
"""
Daily settled ledger — freeze one day's signals and bets once results are in.

    python3 dayledger.py                 # yesterday
    python3 dayledger.py --date 2026-09-14
    python3 dayledger.py --date 2026-09-14 --stdout   # don't write files

Writes ledgers/<date>.md (readable) and ledgers/<date>.json (machine-readable).

PRIVATE. The bets section contains Santhosh's own stakes and P/L. These files
are local only and must never be emailed or shared — the alert emails go to
other recipients.
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SIGNALS = os.path.join(HERE, "signals.jsonl")
BETS = os.path.join(HERE, "bets.jsonl")
OUTDIR = os.path.join(HERE, "ledgers")
# ~/.nightly-line is hidden (leading dot), so Finder never shows it. Mirror every
# ledger somewhere visible — this doubles as the only backup of these files.
MIRROR = os.path.expanduser("~/Documents/Claude/Projects/nightly-line/ledgers")
EVENT = "https://gamma-api.polymarket.com/events?slug={s}"
HIST = "https://clob.polymarket.com/prices-history?market={t}&interval=max&fidelity=60"

# scoring changed mid-afternoon 2026-09-14; MODEL_VERSION was only added later,
# so era must be derived from the timestamp, not the stamp
V2_FROM = "2026-09-14T20:00"


def get(url):
    p = subprocess.run(["curl", "-s", "--max-time", "30", url],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def plist(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def outcome(slug, market, side, cache):
    """True / False / None (unsettled) for one signal."""
    if slug not in cache:
        d = get(EVENT.format(s=slug))
        ev = d[0] if isinstance(d, list) and d else None
        m = {}
        if ev:
            for mk in ev.get("markets", []):
                n = plist(mk.get("outcomes"))
                p = [float(x) for x in plist(mk.get("outcomePrices")) or []]
                if len(n) == len(p) and p:
                    m[mk.get("question") or ""] = dict(zip(n, p))
        cache[slug] = {"closed": bool(ev.get("closed")) if ev else False, "m": m}
    e = cache[slug]
    pr = e["m"].get(market)
    if pr is None:
        for q, v in e["m"].items():
            if market and (market in q or q in market):
                pr = v
                break
    if not pr or side not in pr or max(pr.values()) < 0.9:
        return None
    return pr[side] >= 0.9


def closing_price(slug, market, side, ecache, hcache, start_iso):
    """Price at kickoff for the backed side — the closing line.

    CLV is what separates a bad read from bad luck: a loser whose price moved
    TOWARD it was a good bet that lost; a loser whose price moved away was
    wrong. Without this the post-mortem is just narrating outcomes.
    """
    if slug not in ecache:
        d = get(EVENT.format(s=slug))
        ecache[slug] = d[0] if isinstance(d, list) and d else None
    ev = ecache[slug]
    if not ev or not start_iso:
        return None
    tok = None
    for mk in ev.get("markets", []):
        q = mk.get("question") or ""
        if q == market or (market and (market in q or q in market)):
            names = plist(mk.get("outcomes"))
            toks = plist(mk.get("clobTokenIds"))
            if len(names) == len(toks) and side in names:
                tok = toks[names.index(side)]
                break
    if not tok:
        return None
    if tok not in hcache:
        d = get(HIST.format(t=tok))
        h = d.get("history") if isinstance(d, dict) else (d if isinstance(d, list) else None)
        hcache[tok] = h or []
    hist = hcache[tok]
    if not hist:
        return None
    try:
        st = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    before = [x for x in hist if x.get("t", 0) <= st]
    return float(before[-1]["p"]) if before else None


def reason_section(day, settled, bets):
    """Ask Claude to explain the day's misses from the actual numbers.

    Fed only structured facts — result, CLV, traits, who. The prompt forbids
    inventing causes it cannot see (injuries, weather, news) and requires it to
    lean on the CLV split, because "lost but the market agreed" and "lost and
    the market disagreed" are completely different failures.
    """
    losses = [s for s in settled if not s["won"]]
    payload = []
    for s in settled:
        payload.append({
            "won": s["won"], "verdict": s.get("verdict"),
            "conv": s.get("conv"), "tier": s.get("why"),
            "stake": round(s["stake"]), "entry": round(s.get("entry") or 0, 3),
            "close": round(s["close"], 3) if s.get("close") is not None else None,
            "clv_pts": round(s["clv"] * 100, 1) if s.get("clv") is not None else None,
            "user": s["user"], "hold": round(s.get("hold") or 0, 1),
            "dog": s.get("dog"), "fresh": s.get("fresh"), "stack": s.get("stack"),
            "side": s["side"], "market": s.get("market"), "game": s.get("game"),
        })
    facts = json.dumps({"date": day, "settled": payload}, default=str)
    open("/tmp/.dayledger-facts.json", "w").write(facts)

    prompt = f"""Below is one day of settled sports-betting signals from a system that
tracks large Polymarket positions held by high-performing traders. Write a short
post-mortem: WHY did the losing signals lose?

Ground every claim in the data in /tmp/.dayledger-facts.json. Rules:
- `clv_pts` is closing-line value in percentage points: positive means the market
  moved TOWARD the position after it was taken. Use the SAME cut-off the tables
  above use: `verdict` is already computed for you — "unlucky" means clv_pts > 1.0
  (a good bet that lost), "bad read" means clv_pts <= 1.0 (the market moved away
  or barely moved). Quote the verdict counts, do not recount from clv_pts with a
  different threshold, or your prose will contradict the tables.
  Lead with this distinction; it is the whole point.
- Do NOT invent causes you cannot see. You have no injury news, weather, lineups
  or scores. If a pattern has no visible explanation, say so.
- Call out sample size honestly. Many signals share one game, so they are not
  independent — say when a "pattern" is really one game's outcome repeated.
- Name specific traders and positions rather than generalising.
- 200 words maximum. Plain prose, no headings, no bullet lists.
- End with one sentence on what, if anything, this single day should change —
  including "nothing yet" if that is the honest answer."""

    try:
        r = subprocess.run(
            [os.path.expanduser("~/.local/bin/claude"), "-p", prompt,
             "--allowedTools", "Read", "--permission-prompts", "none",
             "--no-session-persistence", "--model", "claude-sonnet-5"],
            capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        body = (r.stdout or "").strip()
    except Exception as e:
        body = f"_(post-mortem unavailable: {e})_"
    if not body:
        body = "_(post-mortem produced no output)_"
    return ("\n## Why the misses missed\n\n"
            f"*Generated from the settled data above; {len(losses)} losses — "
            f"{sum(1 for x in losses if x.get('verdict')=='unlucky')} unlucky, "
            f"{sum(1 for x in losses if x.get('verdict')=='bad read')} bad read.*\n\n"
            + body + "\n")


def era(r):
    return "v2" if (r.get("model") == "v2" or r["logged"] >= V2_FROM) else "v1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--no-reason", action="store_true",
                    help="skip the LLM post-mortem section")
    a = ap.parse_args()
    day = a.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # ── signals ────────────────────────────────────────────────────────────
    rows = [json.loads(l) for l in open(SIGNALS)] if os.path.exists(SIGNALS) else []
    day_rows = [r for r in rows if r["logged"][:10] == day
                or (r.get("start") or "")[:10] == day]
    seen, sigs = set(), []
    for r in sorted(day_rows, key=lambda x: x["logged"]):
        k = (r["slug"], r["market"], r["side"], r["user"])
        if k in seen:
            continue
        seen.add(k)
        sigs.append(r)

    cache, rawcache, hcache = {}, {}, {}     # outcome() and closing_price()
    for s in sigs:                            # store different shapes — separate
        s["won"] = outcome(s["slug"], s["market"], s["side"], cache)
        s["era"] = era(s)
        cp = closing_price(s["slug"], s["market"], s["side"], rawcache, hcache, s.get("start"))
        s["close"] = cp
        s["clv"] = (cp - s["entry"]) if (cp is not None and s.get("entry")) else None
        if s["won"] is None or s["clv"] is None:
            s["verdict"] = None
        elif s["won"]:
            s["verdict"] = "won"
        else:
            s["verdict"] = "unlucky" if s["clv"] > 0.01 else "bad read"

    settled = [s for s in sigs if s["won"] is not None]
    pending = [s for s in sigs if s["won"] is None]

    def roi(rs):
        rs = [x for x in rs if 0 < (x.get("entry") or 0) < 1]
        if not rs:
            return None, None
        staked = 100.0 * len(rs)
        ret = sum(100.0 / x["entry"] for x in rs if x["won"])
        return ret - staked, (ret - staked) / staked * 100

    # ── bets ───────────────────────────────────────────────────────────────
    bets = [json.loads(l) for l in open(BETS)] if os.path.exists(BETS) else []
    day_bets = [b for b in bets if b.get("date") == day]
    bw = [b for b in day_bets if b.get("result") == "won"]
    bl = [b for b in day_bets if b.get("result") == "lost"]
    staked = sum(b["risk"] for b in day_bets)
    profit = sum(b["to_win"] for b in bw)
    lost = sum(b["risk"] for b in bl)

    L = []
    L.append(f"# Settled Ledger — {day}\n")
    L.append(f"*Frozen {datetime.now():%Y-%m-%d %H:%M}. Local and private — "
             f"contains personal stakes; never email or share.*\n")

    L.append("## Bets\n")
    if not day_bets:
        L.append("_none recorded_\n")
    else:
        L.append(f"**Staked ${staked:,.2f} across {len(day_bets)} tickets · "
                 f"won ${profit:,.2f} ({len(bw)}) · lost ${lost:,.2f} ({len(bl)}) · "
                 f"NET ${profit-lost:+,.2f} ({(profit-lost)/staked*100:+.1f}%)**\n")
        L.append("| Result | Pick | Odds | Risk | Return | Book |")
        L.append("|---|---|---|---|---|---|")
        for b in sorted(day_bets, key=lambda x: (x.get("result") != "won", -x["risk"])):
            ret = f"+${b['to_win']:,.2f}" if b.get("result") == "won" else \
                  (f"-${b['risk']:,.2f}" if b.get("result") == "lost" else "—")
            L.append(f"| {b.get('result','open')} | {b['pick'][:46]} | "
                     f"{b['odds']:+d} | ${b['risk']:,.2f} | {ret} | {b.get('book','—')} |")
        L.append("")

    L.append("## Signals\n")
    L.append(f"{len(sigs)} distinct · {len(settled)} settled · {len(pending)} pending · "
             f"{len({s['slug'] for s in sigs})} games\n")
    if settled:
        w = sum(1 for s in settled if s["won"])
        pl, pct = roi(settled)
        L.append(f"**Record {w}/{len(settled)} ({w/len(settled)*100:.0f}%) · "
                 f"flat-$100 P/L ${pl:+,.0f} ({pct:+.1f}%)**\n")
        L.append("### By tier")
        L.append("| Tier | Record | Flat-$100 | Games |")
        L.append("|---|---|---|---|")
        for t in ("CONVICTION", "SIZE", "EDGE", "BOARD"):
            rs = [s for s in settled if s.get("why") == t]
            if not rs:
                continue
            ww = sum(1 for s in rs if s["won"])
            pl2, pct2 = roi(rs)
            g = len({s["slug"] for s in rs})
            L.append(f"| {t} | {ww}/{len(rs)} ({ww/len(rs)*100:.0f}%) | "
                     + (f"${pl2:+,.0f} ({pct2:+.1f}%)" if pl2 is not None else "—")
                     + f" | {g} |")
        L.append("")
        L.append("### By trait")
        L.append("| Trait | Record | Flat-$100 |")
        L.append("|---|---|---|")
        traits = [("underdog", lambda s: s.get("dog")),
                  ("favourite", lambda s: not s.get("dog")),
                  ("stacked", lambda s: (s.get("stack") or 1) >= 2),
                  ("single market", lambda s: (s.get("stack") or 1) < 2),
                  ("fresh entry", lambda s: s.get("fresh")),
                  ("hold >= 3%", lambda s: (s.get("hold") or 0) >= 3),
                  ("hold < 3%", lambda s: (s.get("hold") or 0) < 3),
                  ("model v2", lambda s: s["era"] == "v2"),
                  ("model v1", lambda s: s["era"] == "v1")]
        for label, fn in traits:
            rs = [s for s in settled if fn(s)]
            if not rs:
                continue
            ww = sum(1 for s in rs if s["won"])
            pl2, pct2 = roi(rs)
            L.append(f"| {label} | {ww}/{len(rs)} ({ww/len(rs)*100:.0f}%) | "
                     + (f"${pl2:+,.0f} ({pct2:+.1f}%)" if pl2 is not None else "—") + " |")
        withclv = [s for s in settled if s.get("clv") is not None]
        if withclv:
            avg = sum(s["clv"] for s in withclv) / len(withclv)
            beat = sum(1 for s in withclv if s["clv"] > 0)
            unlucky = sum(1 for s in withclv if s.get("verdict") == "unlucky")
            badread = sum(1 for s in withclv if s.get("verdict") == "bad read")
            L.append("### Closing-line value\n")
            L.append(f"{len(withclv)} priced · avg CLV **{avg*100:+.2f} pts** · "
                     f"beat the close {beat}/{len(withclv)}\n")
            L.append(f"Losses split: **{unlucky} unlucky** (market moved toward them) · "
                     f"**{badread} bad read** (market moved away)\n")
        L.append("")
        L.append("### Every settled signal")
        L.append("| ✓ | Conv | Wager | Play | Odds | Bettor | Hold | Game |")
        L.append("|---|---|---|---|---|---|---|---|")
        def am(p):
            if not p or p <= 0 or p >= 1:
                return "—"
            return f"-{round(100*p/(1-p)):,}" if p >= 0.5 else f"+{round(100*(1-p)/p):,}"
        for s in sorted(settled, key=lambda x: -(x.get("conv") or 0)):
            L.append(f"| {'W' if s['won'] else 'L'} | {s.get('conv','—')} | "
                     f"${s['stake']:,.0f} | {s['side'][:20]} | {am(s.get('entry'))} | "
                     f"{s['user'][:18]} | {s.get('hold',0):.1f}% | {s['game'][:30]} |")
        L.append("")
    if pending:
        L.append(f"### Pending ({len(pending)})\n")
        for s in pending[:20]:
            L.append(f"- {s['side'][:20]} · {s['game'][:34]} · ${s['stake']:,.0f}")
        L.append("")

    text = "\n".join(L)

    if not a.no_reason and settled:
        text += "\n" + reason_section(day, settled, day_bets)

    if a.stdout:
        print(text)
        return 0
    os.makedirs(OUTDIR, exist_ok=True)
    md = os.path.join(OUTDIR, f"{day}.md")
    js = os.path.join(OUTDIR, f"{day}.json")
    open(md, "w").write(text)
    json.dump({"date": day,
               "bets": day_bets,
               "signals": [{k: v for k, v in s.items()} for s in sigs]},
              open(js, "w"), indent=1, default=str)
    os.chmod(md, 0o600)
    os.chmod(js, 0o600)
    print(f"wrote {md}\nwrote {js}")
    try:
        os.makedirs(MIRROR, exist_ok=True)
        for src in (md, js):
            dst = os.path.join(MIRROR, os.path.basename(src))
            open(dst, "w").write(open(src).read())
            os.chmod(dst, 0o600)
        print(f"mirrored to {MIRROR}")
    except Exception as e:
        print(f"mirror failed (not fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
