#!/usr/bin/env python3
"""
Format flagged plays and route them to the right Discord channel.

    python3 postplays.py alert-plays.json            # post, split by sport
    python3 postplays.py alert-plays.json --dry      # print, post nothing
    python3 postplays.py alert-plays.json --base board

Reads the structured rows check.py writes with --alert-json, groups them by
sport, and posts each group to #sharp-nfl / -cfb / -mlb / -misc. Channels that
do not exist yet fall back to #sharp-plays, so this is safe to run before any of
them are created.

Each row carries three things the text output never had:

  UNITS    recommended stake from units.py, driven by how large the bet is
           against that wallet's OWN normal size — not by the conviction score,
           which the 552-game backtest showed does not order outcomes.
  BOOK     the best price actually available to Santhosh, with the exchanges and
           Polymarket/Kalshi shown as reference when they beat every book.
  EDGE     that price against the sharp's own entry, because following a signal
           at a worse number than the trader got is most of how edge disappears.

Prices are fetched for QUALIFYING rows only. books.py caches per sport+market
within a run, so a mixed slate costs a couple of credits rather than one a row.
"""
import json, os, re, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check as chk
import discordsink as D
import units

SPORT_ORDER = ["NFL", "NCAAF", "MLB", "NBA", "NCAAB", "SOCCER", "ATP", "WTA", "PGA"]


def k(n):
    """Compact money: $9.6K, $65K, $1.2M. chk.m0 spells out $64,781, which is
    six characters this line cannot spare — rows were wrapping in Discord and
    the price was landing on the next line where it reads as a separate play."""
    n = abs(n or 0)
    if n >= 1_000_000:
        return f"${n/1e6:.1f}M"
    if n >= 10_000:
        return f"${n/1e3:.0f}K"
    if n >= 1_000:
        return f"${n/1e3:.1f}K"
    return f"${n:.0f}"


def prices(h, key, want=2):
    """(bettable, reference) — up to `want` prices Santhosh can actually use,
    plus the best PM/Kalshi quote for context.

    The bettable list is what matters. Kalshi and Polymarket are frequently the
    best number on the screen and are the two he CANNOT bet, so showing only the
    top price would routinely name a venue he has no account at.
    """
    if not key:
        return [], None
    mkt = (h.get("market") or "")
    # The Odds API's `totals` market is GOALS/RUNS/POINTS. A title like
    # "O/U 9.5 Total Corners" still yields 9.5, which then matches a goals line
    # and prints a confident, wrong price. Exotic totals are refused.
    # Whole words only: "card" as a substring refused every St. Louis
    # CARDinals / Arizona CARDinals total (found 2026-09-26, Over 7.5 STL/MIL
    # sat unpublished as "no bettable price").
    if re.search(r"\b(corners?|cards?|bookings?|shots|offsides?|fouls?)\b",
                 mkt, re.I):
        return [], None
    h = dict(h)
    if isinstance(h.get("start"), str):
        try:
            h["start"] = datetime.fromisoformat(h["start"])
        except ValueError:
            return [], None
    try:
        import books
        px = books.prices_for(h, key)
    except Exception:
        return [], None
    if not px:
        return [], None
    bettable = [(l, o) for l, o in px if l not in books.REFS_LABELS][:want]
    ref = next(((l, o) for l, o in px if l in books.REFS_LABELS), None)
    return bettable, ref


def play_text(h):
    """Human-readable play. `side` alone is ambiguous and, for spreads, can be
    actively misleading: a position on "Virginia" inside the market
    "Spread: West Virginia (-0.5)" is Virginia +0.5, not Virginia -0.5. Pairing
    the side with the line lifted off the market title flips the sign whenever
    the bettor took the OTHER team.
    """
    side = (h.get("side") or "").strip()
    mkt = (h.get("market") or "").strip()
    import bigboard as _B
    per = _B.period(mkt)
    if per:
        return f"{per} {play_text({**h, 'market': _B.PERIOD.sub('', mkt).replace('  ', ' ')})}"
    m = re.match(r"^(?:(\dH|\dQ)\s+)?Spread:\s*(.+?)\s*\(([+-][\d.]+)\)\s*$", mkt)
    if m:
        favourite, line = m.group(2).strip(), float(m.group(3))
        if side and side.lower() != favourite.lower():
            line = -line                      # bettor is on the dog side
        return f"{side} {line:+g}"
    if side in ("Over", "Under") and "O/U" in mkt:
        return f"{side} {mkt.rsplit('O/U', 1)[-1].strip()}"
    return side


def render(rows, key):
    out = []
    for h in sorted(rows, key=lambda x: -(x.get("conv") or 0)):
        u, _why, flags = units.recommend(h)
        try:
            u, size_label = units.public_size(h)   # units inside the pick window, else "alert"
        except Exception:
            size_label = units.fmt(u)
        bettable, ref = prices(h, key)
        entry = h.get("entry") or 0
        if bettable:
            where = " ".join(f"{l}{o:+d}" for l, o in bettable)
            if ref and ref[1] > bettable[0][1]:
                where += f" ({ref[0]}{ref[1]:+d})"
        elif ref:
            where = f"{ref[0]}{ref[1]:+d} ref-only"
        else:
            where = "-"
        out.append(f"{h.get('conv', 0):>3.0f} {size_label:<5} "
                   f"{k(h['stake']):>6} {play_text(h)[:16]:17} "
                   f"{chk.am(entry):>6}  {where}")
        # Bettor line: who they are and the four numbers that qualify them —
        # hold, this bet against their own average, trailing month, lifetime.
        # Flags follow only when something actually moved the unit size.
        bits = [str(h.get("user") or "").replace("…", "")[:10]]
        if h.get("hold") is not None:
            bits.append(f"{h['hold']:.1f}% hold")
        if h.get("vs_typical"):
            bits.append(f"{h['vs_typical']:.1f}x avg")
        mo = h.get("month_pnl")
        bits.append(f"{k(mo)} mo" if mo else "— mo")
        bits.append(f"{k(h.get('pnl'))} life")
        line2 = "     " + " · ".join(bits)
        if flags:
            line2 += "  [" + " ".join(flags) + "]"
        out.append(line2)
        # the exact market question, so "Yes +285" is never ambiguous
        if h.get("market"):
            out.append(f"     {h['market'][:72]}")
        out.append("")
    return out


def main():
    a = sys.argv[1:]
    dry = "--dry" in a
    base = a[a.index("--base") + 1] if "--base" in a else "sharp"
    files = [x for x in a if not x.startswith("--") and x != base]
    if not files:
        print("usage: postplays.py <plays.json> [--base sharp|board] [--dry]")
        return 1
    try:
        rows = json.load(open(files[0]))
    except (FileNotFoundError, ValueError):
        return 0
    if not rows:
        return 0

    try:
        from oddskey import get_key
        key = get_key()
    except Exception:
        key = None
        print("(no odds key — posting without book prices)", file=sys.stderr)

    groups = {}
    for h in rows:
        groups.setdefault((h.get("league") or "MISC").upper(), []).append(h)

    sent = failed = 0
    for lg in sorted(groups, key=lambda x: (SPORT_ORDER.index(x)
                                            if x in SPORT_ORDER else 99, x)):
        rs = groups[lg]
        ch = D.channel_for(base, lg)
        body = [f"{lg} · {len(rs)} new play(s)", "",
                f"{'CV':>3} {'SZ':<4} {'WAGER':>6} {'PLAY':17} {'ODDS':>6}  WHERE"]
        body += render(rs, key)
        body += ["", "Informational only — what anonymous traders hold. Not advice.",
                 "Unit sizing is still in testing."]
        text = "\n".join(body)
        if dry:
            print(f"--- would post to #{ch} ---\n{text}\n")
            continue
        ok, err = D.send(ch, f"{lg} sharp plays", text)
        sent += ok
        failed += err
    if not dry:
        print(f"[{sent} chunk(s) sent" + (f", {failed} FAILED" if failed else "")
              + f" across {len(groups)} sport(s)]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
