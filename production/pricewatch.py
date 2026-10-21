#!/usr/bin/env python3
"""Watch one Polymarket market and exit when its price crosses a threshold.

Built to be run in the background: it prints nothing until the condition is
met, then prints one alert line and exits 0. Every poll is appended to
pricewatch-<market>.log so the price path is recoverable afterwards.

  pricewatch.py 5386693 --below 0.52 --label "Fenerbahce -1.5"

Exit codes: 0 = triggered, 2 = deadline reached, 3 = market closed/resolved.
One gamma request per poll (default 45s) -- gamma is not the rate-limited
data-api, so this is cheap, and nothing else in the pipeline is touched.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://gamma-api.polymarket.com/markets/"


def fetch(mid):
    req = urllib.request.Request(API + str(mid), headers={"User-Agent": "nightly-line/pricewatch"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("market")
    ap.add_argument("--below", type=float)
    ap.add_argument("--above", type=float)
    ap.add_argument("--index", type=int, default=0, help="which outcome to track")
    ap.add_argument("--poll", type=int, default=45)
    ap.add_argument("--minutes", type=int, default=150, help="give up after this long")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    if a.below is None and a.above is None:
        sys.exit("need --below and/or --above")

    log = os.path.join(HERE, f"pricewatch-{a.market}.log")
    deadline = time.time() + a.minutes * 60
    label = a.label or a.market
    fails = 0

    def note(s):
        with open(log, "a") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {s}\n")

    note(f"START {label} market={a.market} below={a.below} above={a.above} poll={a.poll}s")

    while time.time() < deadline:
        try:
            m = fetch(a.market)
            fails = 0
        except Exception as e:                      # a blip must not kill the watch
            fails += 1
            note(f"fetch failed ({fails}): {e}")
            if fails >= 20:
                print(f"PRICEWATCH GAVE UP: {label} — 20 consecutive fetch failures")
                return 4
            time.sleep(a.poll)
            continue

        px = json.loads(m.get("outcomePrices") or "[]")
        if not px:
            note("no outcomePrices")
            time.sleep(a.poll)
            continue
        p = float(px[a.index])
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        note(f"{p:.4f} bid={bid} ask={ask} last={m.get('lastTradePrice')}")

        hit = ((a.below is not None and p <= a.below)
               or (a.above is not None and p >= a.above))
        if hit:
            side = json.loads(m.get("outcomes") or "[]")[a.index]
            print(f"PRICEWATCH HIT: {label} — {side} is now {p:.3f} "
                  f"({100*p:.1f}%), bid {bid} / ask {ask}, "
                  f"last trade {m.get('lastTradePrice')}")
            note(f"HIT at {p:.4f}")
            return 0

        if m.get("closed") or not m.get("active") or m.get("archived"):
            print(f"PRICEWATCH ENDED: {label} — market closed at {p:.3f} "
                  f"({100*p:.1f}%) without reaching the threshold")
            note(f"CLOSED at {p:.4f}")
            return 3

        time.sleep(a.poll)

    try:
        p = float(json.loads(fetch(a.market).get("outcomePrices"))[a.index])
        tail = f" (last seen {p:.3f})"
    except Exception:
        tail = ""
    print(f"PRICEWATCH TIMED OUT: {label} — {a.minutes} min elapsed, "
          f"never reached the threshold{tail}")
    note("DEADLINE")
    return 2


if __name__ == "__main__":
    sys.exit(main())
