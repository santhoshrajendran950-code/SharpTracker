#!/usr/bin/env python3
"""
Did the market follow the catalyst alerts? Price drift after each signal.

    python3 catalyst-drift.py

For every row in catalyst-signals.jsonl: the price of the alerted view at +1h,
+24h, and now (or at settlement), in points relative to the Polymarket price at
alert time. Positive = the market moved toward the specialists. This is the
short-horizon stand-in for CLV — it doesn't wait for a release to print, so the
sample grows at alert speed, not at FOMC speed.

Drift is measured on the outcome token the lead specialist held. For a
remapped view (NO on one of two live outcomes) that token's price already
expresses the view, so no sign flip is needed.
"""
import json, sys
from datetime import datetime, timezone
from statistics import mean

from macro import get

LEDGER = "catalyst-signals.jsonl"


def history(asset):
    d = get(f"https://clob.polymarket.com/prices-history?market={asset}&interval=max&fidelity=10")
    return sorted(((int(p["t"]), float(p["p"])) for p in (d or {}).get("history") or []))


def price_at(hist, ts):
    before = [p for t, p in hist if t <= ts]
    return before[-1] if before else None


def main():
    try:
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    except FileNotFoundError:
        print("No catalyst signals logged yet.")
        return 0
    now = datetime.now(timezone.utc).timestamp()
    out, failed = [], 0
    for r in rows:
        at = datetime.fromisoformat(r["at"]).timestamp()
        hist = history(r["asset"])
        if not hist:
            failed += 1
            continue
        base = r["poly_px"]
        d = {}
        for label, dt in (("1h", 3600), ("24h", 86400)):
            p = price_at(hist, at + dt) if now >= at + dt else None
            d[label] = (p - base) * 100 if p is not None else None
        d["now"] = (hist[-1][1] - base) * 100
        out.append((r, d, hist[-1][1]))

    print(f"CATALYST DRIFT · {len(rows)} signals · points moved toward the view "
          f"(+ = market followed the specialists)")
    if failed:
        print(f"!! {failed} price-history lookups FAILED — summary is PARTIAL")
    print(f"\n{'ALERTED':12} {'VIEW':30} {'ADDED':>7} {'AT':>5} {'+1h':>6} {'+24h':>6} {'NOW':>6}  EVENT")
    for r, d, last in out:
        f = lambda v: "   —" if v is None else f"{v:+5.1f}"
        when = datetime.fromisoformat(r["at"]).astimezone().strftime("%b %d %H:%M")
        print(f"{when:12} {r['view'][:30]:30} {'$' + format(round(r['added']), ','):>7} "
              f"{r['poly_px']:5.2f} {f(d['1h']):>6} {f(d['24h']):>6} {f(d['now']):>6}  {r['title'][:34]}")
    for label in ("1h", "24h", "now"):
        vals = [d[label] for _, d, _ in out if d[label] is not None]
        if vals:
            print(f"\n{label:>4}: mean {mean(vals):+.2f} pts · followed {sum(v > 0 for v in vals)}/{len(vals)}", end="")
    print()
    if len(out) < 30:
        print(f"\nn={len(out)} — far too few to call this an edge either way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
