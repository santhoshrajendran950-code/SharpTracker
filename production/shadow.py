#!/usr/bin/env python3
"""
Shadow rules: candidate pick rules scored live, beside the published one, and
never published. EXP-035 (Santhosh, 2026-10-22).

    python3 shadow.py --settle          # grade finished shadow plays (nightly)
    python3 shadow.py --report          # every rule side by side, flat ROI + CLV

bigboard.py calls log(hits, now) on every board built for the pick job, with the
FULL sweep (not just rows that pass the v4 gate), so a rule's opposition test
sees every tracked wallet. Each rule fires at most once per side of a game; the
first firing is what is graded, at the leading bettor's entry price then.

Rules (all pregame, never a decided market or a wallet's two-sided book):
  live    benchmark: EXP-034 (xg_ok) with no v4 sharp opposing (EXP-027); it skips
          the conv>=70 and price-window bars, so it fires more than the record
  r1      >=2 wallets with hold >2.5%, lifetime P/L >$100k, stake >1x career
          median on a side; no wallet clearing the same bars on the other side
  r2      any wallet with hold >5% and lifetime P/L >$250k
  r2s     r2, and the stake is >1x that wallet's career median
  r4      a v4-gate sharp with >2x career median, first seen by the sweep within
          3h of kickoff, no v4 sharp opposing
R3 (league specialist) needs graded per-league history the live rows do not
carry; it is measured in research/rules_v5.py only.

Caveat that matters for r1: the sweep only reads the ~1,080 wallets in the
pool (check.py), so a $100k-P/L wallet outside it is invisible here.

PRIVATE: shadow.jsonl and shadow-seen.json carry wallet names, same footing as
signals.jsonl. Nothing here posts, emails or touches picks.jsonl. A failure in
log() must never break the pick job, so bigboard.py wraps the call.
"""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check as chk

SHADOW = os.path.join(HERE, "shadow.jsonl")
SEEN = os.path.join(HERE, "shadow-seen.json")
LATE = timedelta(hours=3)
VIG = 0.015              # same flat haircut as research/xrule.py


def _rel(h):
    return (h.get("vs_hist") or 0) if h.get("hist_ok") else 0


def _clean(h, now):
    st = _start(h)
    return (not h.get("decided") and not h.get("two_sided")
            and st is not None and st > now and 0 < (h.get("entry") or 0) < 1)


def _start(h):
    st = h.get("start")
    if isinstance(st, str):
        try:
            st = datetime.fromisoformat(st.replace("Z", "+00:00"))
        except ValueError:
            return None
    return st


def _v4(h):
    return "conv" in h and chk.passes(h, min_score=0)


def r1_sharp(h):
    return (h.get("hold") or -99) > 2.5 and (h.get("pnl") or 0) > 100_000 and _rel(h) > 1.0


def r2_sharp(h):
    return (h.get("hold") or -99) > 5.0 and (h.get("pnl") or 0) > 250_000


def r2s_sharp(h):
    return r2_sharp(h) and _rel(h) > 1.0


def _fire(rows, sharp, need, veto):
    """{(slug, group, token): [rows on that side passing `sharp`]} for sides with
    >= need distinct wallets and no `veto` wallet on another token of the group."""
    on, opp = defaultdict(list), defaultdict(set)
    for h in rows:
        grp, tok = chk._lean(h)
        if sharp(h):
            on[(h["slug"], grp, tok)].append(h)
        if veto and veto(h):
            opp[(h["slug"], grp)].add((tok, h["wallet"]))
    out = {}
    for (slug, grp, tok), hs in on.items():
        if len({h["wallet"] for h in hs}) < need:
            continue
        mine = {h["wallet"] for h in hs}
        if veto and any(t != tok and w not in mine for t, w in opp.get((slug, grp), ())):
            continue
        out[(slug, grp, tok)] = hs
    return out


def _seen_state(rows, now):
    """First time the sweep saw each position. On the very first run every
    current position is stamped with the epoch, so nothing already on the board
    can count as late money."""
    first_run = not os.path.exists(SEEN)
    try:
        seen = json.load(open(SEEN))
    except (OSError, ValueError):
        seen = {}
    stamp = "1970-01-01T00:00:00+00:00" if first_run else now.isoformat()
    for h in rows:
        seen.setdefault("|".join(str(h.get(k)) for k in ("wallet", "slug", "market", "side")), stamp)
    tmp = SEEN + ".tmp"
    json.dump(seen, open(tmp, "w"))
    os.replace(tmp, SEEN)
    return seen


def fired(hits, now):
    rows = [h for h in hits if _clean(h, now)]
    seen = _seen_state(rows, now)

    def late(h):
        k = "|".join(str(h.get(x)) for x in ("wallet", "slug", "market", "side"))
        first = datetime.fromisoformat(seen.get(k, "1970-01-01T00:00:00+00:00"))
        return _v4(h) and _rel(h) > 2.0 and timedelta(0) <= _start(h) - first <= LATE

    return {
        "live": _fire(rows, lambda h: bool(h.get("xg_ok")) and _v4(h), 1, _v4),
        "r1": _fire(rows, r1_sharp, 2, r1_sharp),
        "r2": _fire(rows, r2_sharp, 1, None),
        "r2s": _fire(rows, r2s_sharp, 1, None),
        "r4": _fire(rows, late, 1, _v4),
    }


def load():
    try:
        return [json.loads(l) for l in open(SHADOW) if l.strip()]
    except FileNotFoundError:
        return []


def log(hits, now):
    """Append each rule's NEW firings. Returns {rule: n new}."""
    have = {(r["rule"], r["slug"], r["group"], r["token"]) for r in load()}
    import picks as PK
    new = defaultdict(int)
    with open(SHADOW, "a") as f:
        for rule, sides in fired(hits, now).items():
            for (slug, grp, tok), hs in sides.items():
                if (rule, slug, grp, tok) in have:
                    continue
                lead = max(hs, key=lambda h: h.get("stake") or 0)
                try:
                    pick = PK.settle_pick_text(lead)
                except Exception:
                    pick = lead.get("side")
                st = _start(lead)
                f.write(json.dumps({
                    "rule": rule, "fired_at": now.isoformat(timespec="seconds"),
                    "date": st.astimezone().date().isoformat(), "start": st.isoformat(),
                    "slug": slug, "group": grp, "token": tok, "league": lead.get("league"),
                    "game": PK.game_nick(lead.get("game")), "market": lead.get("market"),
                    "side": lead.get("side"), "pick": pick, "entry": lead.get("entry"),
                    "now": lead.get("now"), "n": len({h["wallet"] for h in hs}),
                    "users": sorted({h.get("user") for h in hs}),
                }) + "\n")
                new[rule] += 1
    return dict(new)


def settle():
    """Grade finished shadow plays with settle.resolve, as picks.settle_all does."""
    import settle as S
    rows = load()
    today = datetime.now().date().isoformat()
    todo = [r for r in rows if not r.get("result") and (r.get("date") or "9") < today]
    done = stuck = 0
    for d in sorted({r["date"] for r in todo}):
        ledger, cache, events = S.slugs_from_ledger(d), {}, []
        for r in (x for x in todo if x["date"] == d):
            won, why = S.resolve({"game": r["game"], "pick": r["pick"], "kind": "single"},
                                 events, d, ledger, cache)
            if won is None:
                stuck += 1
                continue
            r["result"], r["detail"] = ("won" if won else "lost"), why
            done += 1
    if done:
        tmp = SHADOW + ".tmp"
        with open(tmp, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, SHADOW)
    print(f"shadow: {done} graded, {stuck} unresolved")


def report():
    rows = load()
    print(f"{'rule':6} {'fired':>6} {'graded':>7} {'W-L':>9} {'flat ROI':>9}  (at the lead's entry + {VIG:.3f})")
    for rule in ("live", "r1", "r2", "r2s", "r4"):
        rs = [r for r in rows if r["rule"] == rule]
        g = [r for r in rs if r.get("result") in ("won", "lost") and 0 < (r.get("entry") or 0) < 1]
        w = sum(r["result"] == "won" for r in g)
        flat = [(1 / (r["entry"] + VIG) - 1) if r["result"] == "won" else -1.0 for r in g]
        roi = f"{sum(flat) / len(flat) * 100:+.1f}%" if flat else "—"
        print(f"{rule:6} {len(rs):>6} {len(g):>7} {w:>4}-{len(g) - w:<4} {roi:>9}")


if __name__ == "__main__":
    if "--settle" in sys.argv:
        settle()
    if "--report" in sys.argv or len(sys.argv) == 1:
        report()
