#!/usr/bin/env python3
"""
Closing-line value for PUBLISHED picks — the KPI that decides whether this is
a product.

    python3 pickclv.py            # fill `close`/`clv` on settled picks, print summary
    python3 pickclv.py --print    # summary only, no fetching

ROI needs thousands of games to separate a 3% edge from noise; CLV needs about
two hundred picks. Target set 2026-09-22: CLV >= 0 over 200 graded picks before
anything is charged for.

close  = last Polymarket trade on the pick's own token before kickoff
clv    = close - implied(book odds taken), in percentage points. The book price
         INCLUDES its vig, so a follower who merely matches the market's close
         shows slightly negative CLV here. That is the right accounting: it is
         what they actually paid.
clv_entry = close - the sharp's own entry price (2026-10-07). This is the
         SIGNAL's closing-line value; `clv` is the book's. Over Sep 10-Oct 6
         the picks' clv was +1.6 pts while clv_entry on the same games was
         about -2: the books were offering better prices than Polymarket's
         close, and the sharps' entries were not beating the close. Both are
         reported everywhere so the two are never confused again.
"""
import json, os, sys, statistics as st
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check as chk, picks, settle as S
from myclv import closing, implied

EV = "https://gamma-api.polymarket.com/events?slug={s}"


def token(p, cache):
    ev = cache.get(p["slug"])
    if ev is None:
        d = chk.get(EV.format(s=p["slug"]))
        ev = (d[0] if isinstance(d, list) and d else {}) or {}
        cache[p["slug"]] = ev
    for mk in ev.get("markets", []):
        if (mk.get("question") or "").strip() != (p.get("market") or "").strip():
            continue
        names, toks = S.plist(mk.get("outcomes")), S.plist(mk.get("clobTokenIds"))
        if p.get("side") in names and len(names) == len(toks):
            return toks[names.index(p["side"])]
    return None


def fill():
    rows = picks.load()
    cache, tcache, n = {}, {}, 0
    for p in rows:
        if p.get("result") not in ("won", "lost") or p.get("close") is not None or not p.get("odds"):
            continue
        try:
            kick = int(datetime.fromisoformat(p["start"]).timestamp())
            posted = int(datetime.fromisoformat(p["posted_at"]).timestamp())
        except (KeyError, ValueError, TypeError):
            continue
        if posted >= kick:
            # published in-game (before run-picks went --pre on 2026-09-22): the
            # pre-kickoff close is a different market state, so CLV is undefined
            p["close"] = False
            p["clv_note"] = "posted in-game"
            continue
        tok = token(p, cache)
        if not tok:
            p["close"] = False              # looked, not found — do not retry forever
            continue
        c = closing(tok, kick, tcache)
        if c is None:
            p["close"] = False
            continue
        p["close"] = round(c, 4)
        p["clv"] = round((c - implied(p["odds"])) * 100, 2)
        if p.get("sharp_entry"):
            p["clv_entry"] = round((c - p["sharp_entry"]) * 100, 2)
        n += 1
    # backfill clv_entry on rows that already have a close (no fetch needed)
    for p in rows:
        if p.get("close") and p.get("sharp_entry") and p.get("clv_entry") is None:
            p["clv_entry"] = round((p["close"] - p["sharp_entry"]) * 100, 2); n += 1
    if n:
        tmp = picks.PICKS + ".tmp"
        with open(tmp, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, picks.PICKS)
    return rows, n


def summary(rows):
    v = [p for p in rows if p.get("clv") is not None]
    if not v:
        return None
    c = [p["clv"] for p in v]
    beat = sum(1 for x in c if x > 0)
    core = [p["clv"] for p in v if p.get("tier") == "core"]
    e = [p["clv_entry"] for p in v if p.get("clv_entry") is not None]
    return {"n": len(v), "mean": st.mean(c), "beat": beat / len(v),
            "core_n": len(core), "core_mean": st.mean(core) if core else None,
            "entry_n": len(e), "entry_mean": st.mean(e) if e else None,
            "entry_beat": (sum(1 for x in e if x > 0) / len(e)) if e else None}


def line(s):
    if not s:
        return "CLV: no priced picks yet"
    out = f"CLV vs book {s['mean']:+.2f} pts over {s['n']} picks · beat close {s['beat']:.0%}"
    if s["core_n"]:
        out += f" · core {s['core_mean']:+.2f} ({s['core_n']})"
    if s.get("entry_n"):
        out += f" · signal CLV (vs sharp's entry) {s['entry_mean']:+.2f} pts, beat {s['entry_beat']:.0%} ({s['entry_n']})"
    return out


if __name__ == "__main__":
    rows, n = (picks.load(), 0) if "--print" in sys.argv else fill()
    print(f"filled {n} · " + line(summary(rows)))
