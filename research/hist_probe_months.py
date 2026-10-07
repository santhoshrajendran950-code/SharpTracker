#!/usr/bin/env python3
"""
Cheap existence check: for every sport in sport_config.SPORTS, every month of
the past year, how many games/matches/fights actually exist? This IS stage 1
(hist_slate2.py) — gamma event listing only, no trade or activity pulls — so
running it for all 12 months of every sport is a few thousand light calls,
not a guess. Off-season months come back as a real, logged zero.

    python3 hist_probe_months.py                 # all sports, all 12 months
    python3 hist_probe_months.py mlb nba          # just these

Writes probe_results.json and prints a sport x month table plus a skip list
(sport-months with fewer than MIN_GAMES games — not run through the full
discover/prior/rebuild pipeline, logged with the count so "skipped" is never
silent).
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import sport_config as SC

MIN_GAMES = 8   # below this a month isn't worth the discover/prior cost


def months_past_year(today):
    out = []
    y, m = today.year, today.month
    for _ in range(12):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        out.append(f"{y}-{m:02d}")
    return list(reversed(out))


def month_bounds(ym):
    y, m = map(int, ym.split("-"))
    lo = datetime(y, m, 1, tzinfo=timezone.utc)
    hi = (datetime(y + 1, 1, 1, tzinfo=timezone.utc) if m == 12
          else datetime(y, m + 1, 1, tzinfo=timezone.utc)) - timedelta(days=1)
    return lo.date().isoformat(), hi.date().isoformat()


def main():
    a = sys.argv[1:]
    sports = [x for x in a if x in SC.SPORTS] or list(SC.SPORTS)
    today = datetime.now(timezone.utc)
    months = months_past_year(today) + [today.strftime("%Y-%m")]  # + partial current month
    results = {}
    if os.path.exists(os.path.join(HERE, "probe_results.json")):
        results = json.load(open(os.path.join(HERE, "probe_results.json")))
    for sport in sports:
        for ym in months:
            key = f"{sport}/{ym}"
            if key in results:
                continue
            lo, hi = month_bounds(ym)
            p = subprocess.run([sys.executable, os.path.join(HERE, "hist_slate2.py"), sport, lo, hi],
                               capture_output=True, text=True, timeout=300)
            line = (p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr) else ""
            slate_path = os.path.join(HERE, "windows", sport, ym, "slate.json")
            n = 0
            if os.path.exists(slate_path):
                n = len(json.load(open(slate_path)).get("games", []))
            results[key] = {"games": n, "line": line}
            print(f"  {key:22} {n:5} games   {line[:70]}", flush=True)
            tmp = os.path.join(HERE, "probe_results.json") + ".tmp"
            json.dump(results, open(tmp, "w"))
            os.replace(tmp, os.path.join(HERE, "probe_results.json"))

    print("\n" + "=" * 100)
    print(f"{'sport':10}" + "".join(f"{m[-2:]:>6}" for m in months))
    for sport in sports:
        print(f"{SC.SPORTS[sport][0]:10}" + "".join(
            f"{results.get(f'{sport}/{m}', {}).get('games', '?'):>6}" for m in months))
    skip = [(k, v["games"]) for k, v in results.items() if v["games"] < MIN_GAMES]
    keep = [(k, v["games"]) for k, v in results.items() if v["games"] >= MIN_GAMES]
    print(f"\nKEEP ({len(keep)} sport-months, >= {MIN_GAMES} games): running the full pipeline")
    print(f"SKIP ({len(skip)} sport-months, < {MIN_GAMES} games — off-season or genuinely thin, not assumed):")
    for k, n in sorted(skip):
        print(f"    {k:22} {n} games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
