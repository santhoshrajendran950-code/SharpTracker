#!/usr/bin/env python3
"""
Which bettors earn a size bump, and which get a cut.

    python3 form.py            # the table units.py is reading from

Santhosh's own sizing (week of 2026-09-14, 65 positions) tracked two things the
unit ladder knew nothing about: agreement between sharps (check.mark_consensus)
and WHO was on the play. This file is the second one.

Two sources, deliberately kept apart:

  trusted.json   his judgment. A short hand-kept list of names he sizes up on.
                 It is a file and not a constant so he can edit it without
                 touching code, and so the reason sits next to each name.

  the ledger     the bot's own settled signals (ledgers/*.json), counted as
                 WALLET-GAMES, not legs — three legs on one final score are one
                 outcome. Nobody is promoted or cut on fewer than MIN_GAMES,
                 because at 12 games the standard error on flat ROI is about 30
                 points and any rule firing there is reacting to noise. Until a
                 bettor gets there the ledger simply has no opinion.

Returns +1 (size up a step), -1 (size down a step) or 0.
"""
import glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRUSTED = os.path.join(HERE, "trusted.json")
MIN_GAMES = 20           # wallet-games before the ledger may speak
HOT_ROI, COLD_ROI = 15.0, -15.0

_cache = {"key": None, "rec": {}, "trusted": {}}


def _key():
    fs = sorted(glob.glob(os.path.join(HERE, "ledgers", "*.json"))) + [TRUSTED]
    return tuple((f, os.path.getmtime(f)) for f in fs if os.path.exists(f))


def _load():
    k = _key()
    if _cache["key"] == k:
        return
    games = {}
    for f in sorted(glob.glob(os.path.join(HERE, "ledgers", "*.json"))):
        try:
            sigs = json.load(open(f)).get("signals", [])
        except (ValueError, OSError):
            continue
        for s in sigs:
            if s.get("won") is None or not (0 < (s.get("entry") or 0) < 1):
                continue
            # one outcome per wallet-game: the largest leg stands for the game
            g = (s.get("user"), s.get("slug"))
            if g not in games or (s.get("stake") or 0) > (games[g].get("stake") or 0):
                games[g] = s
    rec = {}
    for (user, _), s in games.items():
        r = rec.setdefault(user, [0, 0, 0.0])
        r[0] += 1
        r[1] += bool(s["won"])
        r[2] += (1 / s["entry"] - 1) if s["won"] else -1
    try:
        trusted = json.load(open(TRUSTED))
    except (FileNotFoundError, ValueError):
        trusted = {}
    _cache.update(key=k, rec=rec, trusted=trusted)


def record(user):
    """(games, wins, flat_roi_pct) from the bot's ledger, or (0, 0, None)."""
    _load()
    n, w, ret = _cache["rec"].get(user, (0, 0, 0.0))
    return n, w, (ret / n * 100 if n else None)


def _trust(user):
    """Raw trusted.json entry for `user`, normalized to a dict, or None.
    Legacy entries are a bare string (note only). A dict entry may also carry
    price_min to widen this one trader's pick price window — see
    price_window() below."""
    _load()
    v = _cache["trusted"].get(user)
    if v is None:
        return None
    return {"note": v} if isinstance(v, str) else v


def adjust(user):
    """(step, flag). Trusted list first; the ledger can still CUT a trusted
    name that has gone cold over a real sample, but never double-bumps one."""
    _load()
    n, w, roi = record(user)
    if n >= MIN_GAMES and roi is not None and roi <= COLD_ROI:
        return -1, f"cold {w}-{n-w}"
    if _trust(user) is not None:
        return +1, "trusted"
    if n >= MIN_GAMES and roi is not None and roi >= HOT_ROI:
        return +1, f"hot {w}-{n-w}"
    return 0, ""


def price_window(user, lo, hi):
    """(lo, hi) entry-PROBABILITY bounds for the pick price gate — widened
    only if trusted.json names an explicit price_min for this trader, and
    only DOWNWARD (toward pick'em / a bigger dog), never past what's
    configured and never by raising `hi`.

    Added 2026-09-23 for Kch-Temp (price_min, widens toward pick'em) after
    Santhosh asked for the exception; extended the same day, on his ask to
    check other top-ROI/high-P/L bettors, with price_max (widens toward
    heavier favourites) for justaluckydude and CandleHammerDrums, whose
    records run the OPPOSITE way — strong on short favourites, weak on dogs.
    Every one of these is the SAME small, non-independent sample used to
    rank the bettor in the first place: a bet on the person, not a validated
    rule. Checked and explicitly declined: Soarin22 (n=6 total signals) and
    GrizzliesSuck (n=10) — both too thin to act on despite good-looking ROI;
    Soarin22 was Santhosh's own example and still isn't enough data. Every
    pick that publishes through an override is tagged so its CLV can be
    judged apart from the default -122..-180 picks.
    """
    t = _trust(user)
    if not t:
        return lo, hi
    new_lo = min(lo, t["price_min"]) if "price_min" in t else lo
    new_hi = max(hi, t["price_max"]) if "price_max" in t else hi
    return new_lo, new_hi


if __name__ == "__main__":
    _load()
    print(f"trusted.json: {', '.join(_cache['trusted']) or '(empty)'}")
    print(f"ledger needs {MIN_GAMES}+ wallet-games to speak\n")
    print(f"{'bettor':16} {'games':>5} {'W-L':>7} {'flat ROI':>9}  step")
    for u, (n, w, ret) in sorted(_cache["rec"].items(), key=lambda kv: -kv[1][0])[:25]:
        st, fl = adjust(u)
        print(f"{str(u)[:16]:16} {n:5} {w:3}-{n-w:<3} {ret/n*100:+8.1f}%  {st:+d} {fl}")
