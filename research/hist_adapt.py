#!/usr/bin/env python3
"""
Stage 3.5 — write the frozen rebuild/score stages' inputs for one window.

    python3 hist_adapt.py 2025-10 [--min-pnl 50000] [--min-vol 500000]

Produces windows/<ym>/bt_slate.json and bt_pool.json in exactly the shape
backtest/bt_rebuild.py and bt_score.py expect, and copies those frozen stages
(plus a btlib pointed at lib/) into the window dir so their outputs land there.

THE HISTORICAL GATE IS NOT THE PRODUCTION GATE. Production requires $250K
lifetime P/L from the leaderboard; the prior here is 90 days of REALISED P/L
from activity, so the bar is scaled: hold >= 1.5%, P/L >= MIN_PNL, volume >=
MIN_VOL. Both numbers are stamped into bt_pool.json's `bars` for the report.
"""
import json, os, shutil, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import settle as S


def main():
    a = sys.argv[1:]
    ym = a[0]
    min_pnl = float(a[a.index("--min-pnl") + 1]) if "--min-pnl" in a else 50_000
    min_vol = float(a[a.index("--min-vol") + 1]) if "--min-vol" in a else 500_000
    d = os.path.join(HERE, "windows", *ym.split("/"))   # "2025-10" (football) or "mlb/2025-10"
    slate = json.load(open(os.path.join(d, "slate.json")))
    prior = json.load(open(os.path.join(d, "prior.json")))
    wallets = json.load(open(os.path.join(d, "wallets.json")))
    sl = {}
    for g in slate["games"]:
        kick = datetime.fromisoformat(g["start"])
        sl[g["slug"]] = {"league": g["league"], "title": g["title"], "kickoff": g["start"],
                         "kickoff_ts": int(kick.timestamp()), "tag": g["league"].lower(),
                         "markets": [{"question": m["question"], "outcomes": S.plist(m["outcomes"]),
                                      "prices": [float(x) for x in S.plist(m["outcomePrices"])],
                                      "tokens": S.plist(m["clobTokenIds"]), "closed": bool(m.get("closed"))}
                                     for m in g["markets"]]}
    pool = []
    for w, p in prior.items():
        if p.get("partial"):
            continue
        ok = p["hold"] >= 1.5 and p["pnl"] >= min_pnl and p["vol"] >= min_vol
        # in_sample is informational here: every wallet with a prior is scored,
        # and hist_cuts.py applies the gates afterwards so several can be compared
        pool.append({"wallet": w, "user": wallets.get(w, {}).get("name") or w[:10], "rank": None,
                     "prior_pnl": p["pnl"], "prior_vol": p["vol"], "prior_hold": p["hold"],
                     "all_pnl": p["pnl"], "all_vol": p["vol"], "month_pnl": wallets.get(w, {}).get("usd", 0),
                     "month_vol": 0.0, "in_sample": not ok})
    n_ok = sum(1 for x in pool if not x["in_sample"])
    json.dump({"built": datetime.now(timezone.utc).isoformat(), "window_days": None, "lo": slate["start"],
               "hi": slate["end"], "capped_tags": [], "events_seen": len(sl), "slate": sl}, open(os.path.join(d, "bt_slate.json"), "w"))
    json.dump({"built": datetime.now(timezone.utc).isoformat(), "model": "v4-frozen", "depth": None, "month_depth": None,
               "bars": {"min_hold": 1.5, "min_pnl": min_pnl, "min_vol": min_vol, "prior": "90d realised from /activity"},
               "biases": ["prior is realised P/L over 90 days, not the leaderboard's lifetime mark"],
               "counts": {"wallets": len(pool), "gate_ok": n_ok}, "requests": {}, "pool": pool}, open(os.path.join(d, "bt_pool.json"), "w"))
    # sidecar: conditionId -> {market_type, league, slug} for later breakdowns.
    # The frozen bt_slate.json/bt_pool.json shapes have no room for this field,
    # so it lives alongside them rather than inside them.
    meta = {}
    for g in slate["games"]:
        for m in g["markets"]:
            meta[f"{g['slug']}|||{m['question']}"] = {"market_type": m.get("market_type", "moneyline"),
                                                       "league": g["league"]}
    json.dump(meta, open(os.path.join(d, "market_meta.json"), "w"))

    for f in ("bt_rebuild.py", "bt_score.py"):
        shutil.copy(os.path.join(HERE, "lib", f), os.path.join(d, f))
    # Absolute, not relative: a football window is windows/<ym>/, a sport
    # window is windows/<sport>/<ym>/ — different depths, so a dirname-chain
    # trick breaks one or the other. hist_adapt.py already knows HERE.
    lib_dir = os.path.join(HERE, "lib")
    bl = open(os.path.join(lib_dir, "btlib.py")).read().replace(
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # nl-research: frozen copies in lib/',
        f'sys.path.insert(0, {lib_dir!r})   # absolute: written by hist_adapt.py')
    open(os.path.join(d, "btlib.py"), "w").write(bl)
    print(f"{ym}: slate {len(sl)} games · pool {len(pool)} wallets, {n_ok} clear the historical gate (hold>=1.5%, P/L>=${min_pnl:,.0f}, vol>=${min_vol:,.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
