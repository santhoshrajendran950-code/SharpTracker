#!/usr/bin/env python3
"""
The PUBLIC record — every play recommended on X, at the unit size recommended.

    python3 picks.py --scan board.json     # log newly qualifying plays
    python3 picks.py --settle              # resolve yesterday's picks
    python3 picks.py --report [--day D]    # the daily scoreboard text
    python3 picks.py --open                # what is still running

Deliberately separate from bets.jsonl. That file is Santhosh's own money and
stays private; this one is what the account publicly called, timestamped at the
moment it was posted, and it has to stand on its own — including on days he does
not bet, and including when it loses. A record with gaps is worth nothing to
someone who might pay for it.

WHAT QUALIFIES (all four, no exceptions):
  conviction >= 60  (same bar as the Discord alert)
  a recommended size above zero
  the wallet holds >= MIN_POSITIONS open positions
  that wallet's median position is >= MIN_TYPICAL

The last two are the important ones. Size-vs-their-own-normal is what drives
the unit recommendation, and it is meaningless when the denominator is noise —
a wallet holding a hundred $8 positions makes an ordinary bet look like 757x.
Those rows score highly and would be the most embarrassing thing on a public
record, so they are filtered before anything is published, not after.

The price recorded is the best price a READER could actually have taken at post
time, not the sharp's entry and not Polymarket's. A public record priced at
numbers nobody could get is a lie by omission.
"""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anon, check as chk, postplays as P, settle as S, units as U

PICKS = os.path.join(HERE, "picks.jsonl")
# Matched to alert.sh's THRESHOLD so Discord and X select the same plays.
# The size-signal guards below are NOT a threshold — they are a data-validity
# check on the denominator the unit recommendation divides by — so they stay.
MIN_CONV = 70.0          # v3: the band where ROI crosses zero in BOTH windows (+3.2% / +3.7%)


def load():
    try:
        return [json.loads(l) for l in open(PICKS) if l.strip()]
    except FileNotFoundError:
        return []


def key(p):
    return (p.get("slug"), p.get("market"), p.get("side"))



def locked_units(h, computed):
    """The size a play was PUBLISHED at, if it has been — else `computed`.

    units.recommend() is re-run on the live row every sweep, and a score
    drifting from 81 to 79 drops the +score step: the Twins/Giants over went
    out as a 4u pick and then showed as 3u on the next board. A published size
    is a public call and the record is graded on it, so every later board has
    to repeat it, not re-derive it.
    """
    k = key(h)
    for q in reversed(load()):
        if key(q) == k and q.get("units"):
            return q["units"]
    return computed

def settle_pick_text(h):
    """Pick text in the form settle.parse_pick understands."""
    t = P.play_text(h)
    kind, _, _ = S.parse_pick(t)
    if kind is None:                     # a bare team name = moneyline
        return f"{t} ML"
    return t


def game_nick(title):
    """'LSU vs. Ole Miss' -> 'LSU/Ole Miss', the form settle matches on."""
    for sep in (" vs. ", " vs "):
        if sep in (title or ""):
            a, b = title.split(sep, 1)
            return f"{a.strip()}/{b.strip()}"
    return title or ""


def _has_price_override(user):
    """True if this pick published under a per-trader widened price window
    (form.price_window / trusted.json price_min) rather than the -122..-180
    default -- stamped on every pick so its CLV can be split out later."""
    if not user:
        return False
    try:
        import form
        t = form._trust(user)
        return bool(t and ("price_min" in t or "price_max" in t))
    except Exception:
        return False


PICK_MAX_DOG = -122      # PRICE IS A FILTER, NOT A SCORE (Santhosh, 2026-09-23).
PICK_MIN_ENTRY = 0.55    # Favourites only: -122 or shorter...
PICK_MAX_FAV = -180      # ...but nothing shorter than -180. Inside this window the
PICK_MAX_ENTRY = 0.643   # bettor-profile score orders ROI in both windows; outside
                         # it, it does not. Both the book price and the entry pass.
PRICE_WINDOW = False     # Santhosh 2026-10-27: "remove the rule for now". False = no
                         # price gate on entry or book price (form.py overrides moot);
                         # True restores -122..-180 exactly as before.
LINEMOVE_GATE = True     # EXP-030: picks only from the "toward" line-move tier. Forward test
                         # (300 live signals) is pre-registered in docs/experiments/LEDGER.md;
                         # flip to False to make the tier informational only.
MAX_REST_PER_DAY = 4     # non-core picks per day; core plays are never capped
                         # by count — "a few strong opinions a day", not volume


def contested(h):
    """A qualifying sharp is on the OTHER side of this lean in the same game
    (check.mark_consensus `opposed`). 2026-10-07: 18% of suggested markets in
    the Sep-Oct window had both sides shown and 39% of suggestions sat on one;
    a call that another tracked wallet is fading is not a call. Same logic
    passes() already applies to one wallet's two-sided book. EXP-027."""
    return (h.get("opposed") or 0) >= 1


def qualifies(h):
    if (h.get("conv") or 0) < MIN_CONV:
        return False, f"below {MIN_CONV:.0f}"
    import bigboard as B
    if B.period(h.get("market")):
        return False, "period market — cannot be settled"
    if contested(h):
        return False, f"contested — {h.get('opposed')} sharp(s) on the other side"
    if chk.XGATE:
        # EXP-034 (Santhosh, 2026-10-21): "2 sharps with >3% hold have a side
        # with >2x their career median, with no opposing sharp." The veto half
        # is contested() above; this is the consensus-plus-size half. It STACKS
        # on MIN_CONV at his direction -- 94% of the rule's backtested plays
        # were already conv>=70, and the stacked cut measured +11.29% flat
        # (90% CI [-1.02, +23.71]) against +8.78% for the rule alone.
        if "xg_sharps" not in h:
            # fail closed, and say why: a board built without
            # wallethist.annotate() cannot answer this question.
            return False, "x-gate unstamped (wallethist annotation missing)"
        if not h.get("xg_ok"):
            return False, (f"x-gate: {h.get('xg_sharps', 0)} of {chk.XG_NEED} sharps "
                           f">{chk.XG_HOLD:.0f}% hold and >{chk.XG_MULT:.0f}x career median")
    if LINEMOVE_GATE and not h.get("move_tier"):
        import linemove                      # a row that arrived unstamped is scored here, never waved through
        h["move_tier"], h["move"] = linemove.tier(h)
    if LINEMOVE_GATE and h.get("move_tier") in ("away", "flat"):
        # EXP-030 (2026-10-07): the bottom 60% by predicted line move lost 6-7%
        # in every out-of-sample quintile; only "toward" rows can be picks.
        return False, f"line not moving toward ({h.get('move_tier')}, {h.get('move', 0):+.1f} pts) — alert only"
    e = h.get("entry") or 0
    try:
        import form
        lo, hi = form.price_window(h.get("user"), PICK_MIN_ENTRY, PICK_MAX_ENTRY)
    except Exception:
        lo, hi = PICK_MIN_ENTRY, PICK_MAX_ENTRY
    if PRICE_WINDOW and 0 < e < lo:
        return False, f"longer than {PICK_MAX_DOG} at entry"
    if PRICE_WINDOW and e > hi:
        return False, f"shorter than {PICK_MAX_FAV} at entry"
    u, _, _ = U.recommend(h)
    if not u:
        return False, "no size"
    vt = h.get("vs_typical")
    if vt and vt < U.TINY_FOR_HIM and (h.get("n_open") or 0) >= U.TRUST_POSITIONS \
            and (h.get("typical") or 0) >= U.TRUST_TYPICAL:
        return False, f"{vt:.2f}x his normal bet — alert, not a pick"
    if (h.get("n_open") or 0) < U.MIN_POSITIONS:
        return False, f"only {h.get('n_open')} open positions"
    if (h.get("typical") or 0) < U.MIN_TYPICAL:
        return False, f"${h.get('typical', 0):,.0f} median is noise"
    return True, ""


def scan(board_path, key_odds=None):
    """New qualifying plays not already in the ledger."""
    rows = json.load(open(board_path))
    seen = {key(p) for p in load()}
    out, skipped = [], defaultdict(int)
    try:
        from oddskey import get_key
        okey = get_key()
    except Exception:
        okey = None
    for h in sorted(rows, key=lambda r: -(r.get("conv") or 0)):
        ok, why = qualifies(h)
        if not ok:
            skipped[why] += 1
            continue
        if key(h) in seen:
            skipped["already posted"] += 1
            continue
        # One call per game per family. 2026-09-27: Browns +4.5 AND +3.5 went
        # out (6u, same side, two lines), then Saints ML 4u AND Raiders +3.5 4u
        # (both sides of one game) -- two sharps, two overrides, no guard. A
        # second line on a side we already published adds nothing; a call on
        # the other side contradicts the first. Totals are their own family.
        fam = "total" if (h.get("side") in ("Over", "Under")) else "side"
        clash = [p for p in load() + out
                 if p.get("slug") == h.get("slug")
                 and ("total" if p.get("side") in ("Over", "Under") else "side") == fam]
        if clash:
            same = any(p.get("side") == h.get("side") for p in clash)
            skipped["same game, already have a " + ("call on this side" if same else "call on the OTHER side")] += 1
            continue
        u, _, _ = U.recommend(h)
        bettable, ref = P.prices(h, okey, want=2)
        best = bettable[0] if bettable else None
        if not best:
            # "shop it" is not a recommendation and cannot be scored honestly.
            skipped["no bettable price"] += 1
            continue
        try:
            import form
            _lo, _hi = form.price_window(h.get("user"), PICK_MIN_ENTRY, PICK_MAX_ENTRY)
        except Exception:
            _lo, _hi = PICK_MIN_ENTRY, PICK_MAX_ENTRY
        book_dog_cap = -round(100 * (1 - _lo) / _lo) if _lo < .5 else round(100 * _lo / (1 - _lo))
        book_fav_cap = -round(100 * _hi / (1 - _hi)) if _hi >= .5 else round(100 * (1 - _hi) / _hi)
        if PRICE_WINDOW and best[1] > book_dog_cap:
            # the sharp's entry passed but the book price is a long dog —
            # +150 and longer lost money at every size on his sheet
            skipped[f"book price longer than {book_dog_cap}"] += 1
            continue
        if PRICE_WINDOW and best[1] < book_fav_cap:
            # mirrors book_dog_cap on the other side: a price_max override
            # (justaluckydude, CandleHammerDrums, 2026-09-24) widens how
            # short a favourite the BOOK price is allowed to be, same as it
            # already widened the sharp's own entry above
            skipped[f"book price shorter than {book_fav_cap}"] += 1
            continue
        core = U.is_core(h)
        if not core:
            day = (datetime.fromisoformat(h["start"]).astimezone().date().isoformat()
                   if h.get("start") else None)
            n_rest = sum(1 for p in load() if p.get("date") == day and p.get("tier") == "rest")
            n_rest += sum(1 for p in out if p.get("date") == day and p.get("tier") == "rest")
            if n_rest >= MAX_REST_PER_DAY:
                skipped["rest-tier cap for the day"] += 1
                continue
        out.append({
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "slug": h.get("slug"), "league": h.get("league"),
            "game": game_nick(h.get("game")),
            "title": h.get("game"), "market": h.get("market"),
            "side": h.get("side"),
            "pick": settle_pick_text(h),
            "units": u, "conv": round(h.get("conv") or 0, 1),
            "sizing": U.SIZING_VERSION, "tier": "core" if core else "rest",
            "price_override": _has_price_override(h.get("user")),
            "consensus": h.get("consensus", 1), "opposed": h.get("opposed", 0),
            "move": h.get("move"), "move_tier": h.get("move_tier"),
            "book": best[0] if best else None,
            "odds": best[1] if best else None,
            "sharp_entry": h.get("entry"),
            "alias": anon.alias(h.get("user")),
            "hold": h.get("hold"), "stake": h.get("stake"),
            "vs_typical": h.get("vs_typical"),
            # whether that multiple means anything — a $24 median makes a
            # routine bet read as 414x, and the public posts must not print it
            "size_trusted": ((h.get("n_open") or 0) >= U.TRUST_POSITIONS
                             and (h.get("typical") or 0) >= U.TRUST_TYPICAL),
            "start": h.get("start"),
            "date": (datetime.fromisoformat(h["start"]).astimezone().date().isoformat()
                     if h.get("start") else None),
        })
        seen.add(key(h))
    return out, dict(skipped)


def append(rows):
    with open(PICKS, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def discord_text(p):
    """Richer block for Discord. Same play, same moment, more room — X is capped
    at 280 characters, Discord at 2000, so the compact form would be throwing
    away detail for no reason. The TRIGGER and the SELECTION are identical; only
    the rendering differs."""
    price = f"{p['odds']:+d} at {p['book']}" if p.get("odds") else "no bettable price"
    out = [f"{U.fmt(p['units']):>5}  {p['pick']}   {price}",
           f"       conv {p.get('conv', 0):.0f} · {p['alias']} · "
           f"{anon.pct(p.get('hold'))} hold · {anon.money(p.get('stake'))}"]
    vt = p.get("vs_typical")
    if vt:
        out[-1] += f" · {vt:.1f}x their normal"
    out.append(f"       {p.get('market') or p.get('title', '')}")
    out.append(f"       {_game_line(p)}")
    return "\n".join(out)


def caption(p):
    """The X caption when the pick card is attached: the play and the best
    price, nothing else. The card carries the trader, hold, wager and size, and
    repeating them above the image just pushes the image down the timeline."""
    price = f"{p['odds']:+d} at {p['book']}" if p.get("odds") else "shop it"
    return f"{p['pick']}   {price}\n{_game_line(p)}"


def _game_line(p):
    """'MLB · Rays/Yankees · Tue 1:05 PM EDT'. The time is not decoration: the
    Yankees played twice on 2026-09-22 and a pick without it names two games."""
    import xcard
    bits = [p.get("league") or "", p.get("game") or p.get("title") or ""]
    t = xcard.when_text(p.get("start")) if p.get("start") else ""
    if t:
        bits.append(t)
    return " · ".join(b for b in bits if b)


def post_text(p):
    """The full X post for one pick — used only when there is NO card (render
    or upload failed). Alias, blurred figures, units front and centre."""
    price = f"{p['odds']:+d} at {p['book']}" if p.get("odds") else "shop it"
    lines = [f"{p['pick']}   {price}",
             f"{U.fmt(p['units'])}",
             "",
             f"{p['alias']} · {anon.pct(p.get('hold'))} hold · "
             f"{anon.money(p.get('stake'))}"]
    vt = p.get("vs_typical")
    if vt and p.get("size_trusted", True):   # rows from before the flag: as-was
        lines.append(f"{vt:.0f}x their normal size")
    lines += ["", _game_line(p)]
    return "\n".join(x for x in lines if x is not None)


def settle_all(day=None):
    """Resolve picks whose games have finished. Reuses settle.py wholesale."""
    rows = load()
    todo = [p for p in rows if not p.get("result")
            and (day is None or p.get("date") == day)]
    if not todo:
        return 0, 0, []
    done, stuck = 0, []
    bydate = defaultdict(list)
    for p in todo:
        bydate[p.get("date")].append(p)
    for d, ps in bydate.items():
        if not d:
            continue
        ledger = S.slugs_from_ledger(d)
        cache, events = {}, []
        for p in ps:
            won, why = S.resolve({"game": p["game"], "pick": p["pick"],
                                  "kind": "single"}, events, d, ledger, cache)
            if won is None:
                stuck.append((p, why))
                continue
            p["result"] = "won" if won else "lost"
            p["detail"] = why
            done += 1
    if done:
        with open(PICKS, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return done, len(stuck), stuck


def pl(p):
    """Units won or lost, at the recommended size and the recorded price.

    A LOSS costs the stake whatever the price was, so it never needs odds — the
    first version returned None when odds were missing and the report summed
    `pl(p) or 0`, which rendered an 0-4 day as "+0.00u, ROI +0%". A public
    record that reports a losing day as break-even is worse than no record.
    A WIN genuinely cannot be valued without a price, so it returns None and
    the report refuses to publish rather than guessing.
    """
    u = p.get("units") or 0
    if not p.get("result"):
        return None
    if p["result"] == "lost":
        return -u
    o = p.get("odds")
    if not o:
        return None                      # won, but unpriceable — caller must flag
    return u * (o / 100.0 if o > 0 else 100.0 / -o)


def record(days=None):
    """The running ledger: every graded pick since day one, or the last N days.
    Units and ROI only — never dollars. Splits by tier and sizing version so a
    rule change can be judged on its own rows."""
    rows = [p for p in load() if p.get("result") in ("won", "lost")]
    if days:
        cut = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        rows = [p for p in rows if (p.get("date") or "") >= cut]
    if not rows:
        return "No graded picks settled yet."
    vals = [pl(p) for p in rows]
    ok = [(p, v) for p, v in zip(rows, vals) if v is not None]

    def block(label, prs):
        if not prs:
            return None
        w = sum(1 for p, _ in prs if p["result"] == "won")
        risk = sum(p["units"] for p, _ in prs)
        net = sum(v for _, v in prs)
        clv = [p["clv"] for p, _ in prs if p.get("clv") is not None]
        beat = sum(1 for c in clv if c > 0)
        games = len({p["slug"] for p, _ in prs})
        s = (f"  {label:22} {w:3}-{len(prs)-w:<3} {w/len(prs):4.0%}  {risk:6.1f}u risked  "
             f"{net:+7.2f}u  ROI {anon.roi(net/risk*100) if risk else '—':>5}  {games:3} games")
        if clv:
            s += f"  CLV {sum(clv)/len(clv):+.2f} pts, beat close {beat}/{len(clv)}"
        ce = [p["clv_entry"] for p, _ in prs if p.get("clv_entry") is not None]
        if ce:
            s += f"  signal CLV {sum(ce)/len(ce):+.2f}"
        return s

    out = [f"PICKS LEDGER · {'last %dd' % days if days else 'all time'} · {len(rows)} graded"]
    if len(ok) < len(rows):
        out.append(f"  ({len(rows)-len(ok)} won without a recorded price — excluded from units)")
    out.append(block("ALL", ok))
    out.append("by tier:")
    for t in ("core", "rest"):
        b = block(t, [(p, v) for p, v in ok if p.get("tier") == t])
        if b:
            out.append(b)
    out.append("by sizing version:")
    for sv in sorted({p.get("sizing") or "s1" for p, _ in ok}):
        b = block(sv, [(p, v) for p, v in ok if (p.get("sizing") or "s1") == sv])
        if b:
            out.append(b)
    out.append("by league:")
    for lg in sorted({p.get("league") or "?" for p, _ in ok}):
        b = block(lg, [(p, v) for p, v in ok if (p.get("league") or "?") == lg])
        if b:
            out.append(b)
    # streak and drawdown, in units, in start order
    bal = peak = dd = 0.0
    run = worst = 0
    for p, v in sorted(ok, key=lambda t: (t[0].get("start") or "")):
        bal += v
        peak = max(peak, bal)
        dd = max(dd, peak - bal)
        run = run + 1 if p["result"] == "lost" else 0
        worst = max(worst, run)
    out.append(f"  max drawdown {dd:.1f}u · longest losing run {worst}")
    return "\n".join(x for x in out if x)


def report(day):
    rows = [p for p in load() if p.get("date") == day and p.get("result")]
    if not rows:
        return f"{day}\nNo plays settled."
    w = sum(1 for p in rows if p["result"] == "won")
    staked = sum(p["units"] for p in rows)
    vals = [pl(p) for p in rows]
    missing = [r for r, v in zip(rows, vals) if v is None]
    if missing:
        return (f"RESULTS · {day}\n!! {len(missing)} settled pick(s) have no "
                f"recorded price and cannot be valued — not publishing a "
                f"partial figure.\n"
                + "\n".join(f"   {m['pick']} ({m['game']})" for m in missing))
    net = sum(vals)
    games = len({p["slug"] for p in rows})
    out = [f"RESULTS · {day}",
           f"{w}-{len(rows)-w} · {staked:g}u staked · {net:+.2f}u",
           f"ROI {anon.roi(net/staked*100)}" if staked else ""]
    if len(rows) / max(games, 1) >= 1.5:
        out.append(f"({len(rows)} plays over {games} games)")
    return "\n".join(x for x in out if x)


def main():
    a = sys.argv[1:]
    if "--record" in a:
        days = int(a[a.index("--days") + 1]) if "--days" in a else None
        print(record(days))
        return 0
    if "--scan" in a:
        rows, skipped = scan(a[a.index("--scan") + 1])
        print(f"{len(rows)} new qualifying play(s)"
              + (f" · skipped {skipped}" if skipped else ""))
        for r in rows:
            print("\n" + post_text(r))
        # NOTE: --log writes the ledger regardless of whether the X post then
        # succeeds. picks.jsonl is meant to be the record of what was PUBLISHED,
        # so the runner must only log what it managed to post — see run-picks.sh.
        if rows and "--log" in a:
            append(rows)
            print(f"\nlogged {len(rows)} to picks.jsonl")
        elif rows:
            print("\n(not logged — add --log)")
        return 0
    if "--settle" in a:
        day = a[a.index("--day") + 1] if "--day" in a else None
        done, n_stuck, stuck = settle_all(day)
        print(f"settled {done}, {n_stuck} unresolved")
        for p, why in stuck:
            print(f"  {p['pick'][:30]:32}{p['game'][:24]:26}{why}")
        return 0
    if "--report" in a:
        day = (a[a.index("--day") + 1] if "--day" in a
               else datetime.now().date().isoformat())
        print(report(day))
        return 0
    if "--open" in a:
        op = [p for p in load() if not p.get("result")]
        print(f"{len(op)} open · {sum(p['units'] for p in op):g}u staked")
        for p in op:
            print(f"  {U.fmt(p['units']):>5} {p['pick'][:26]:28}"
                  f"{(str(p.get('odds')) if p.get('odds') else '—'):>6}  {p['game']}")
        return 0
    print(__doc__.strip().split("\n\n")[1])
    return 1


if __name__ == "__main__":
    sys.exit(main())
