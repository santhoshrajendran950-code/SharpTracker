#!/usr/bin/env python3
"""
Recommended stake, in units, for a flagged position.

THE PRIMARY INPUT IS `vs_typical` — how large this bet is against the SHARP'S
OWN median open position, not against the dollar amount. A wallet at 40x its
normal size on $22K is saying more than one making a routine $51K bet, and the
porting doc lists that as the metric which repeatedly surfaced signal raw
dollars missed.

Conviction is deliberately NOT the driver. The 552-game out-of-sample backtest
found it does not order outcomes — the 80-100 tier returned -12.6% while 40-60
returned -1.6% — so sizing on it would stake most on the worst tier. It is kept
only as a gate (below MIN_CONV nothing is recommended) and as a mild tiebreak.

Adjustments come from the traits the backtest could actually separate:
  fresh vs stale   +39.1% ROI, CI [+6.9%, +67.7%]   -> SUPPORTED, so stale cuts
  hold >= 3%       -5.7% vs -11.6% for hold < 3%    -> weak but consistent
  underdog          -25.9%, CI [-46.5%, -0.7%]      -> CONTRADICTED, no bonus
  stacked           CI spans 69 points              -> UNDETERMINED, no bonus

TWO GUARDS, both learned from real rows on this board:
  * A median built from too few positions means nothing. Soarin22 held 2 open
    positions, so its "1.0x typical" was just its own bet compared to itself.
  * A median of a few dollars inflates everything. Feromont's median open
    position was $24 across 100 positions, so a routine $10,000 bet read as
    414x "typical" and would have scored maximum size off pure noise.
Either case caps the recommendation rather than trusting the multiple.

S2, 2026-09-21 — TWO INPUTS TAKEN FROM HOW SANTHOSH ACTUALLY SIZES. His own
ledger for the week of 2026-09-14 (130 tickets = 65 distinct positions once the
same bet across books is combined) returned +28% as staked against +14% flat,
and his stake did NOT track anything this ladder used: rank correlation with
conviction +0.23, with sharp dollars +0.11, with hold -0.01. By his account, and
in the data, the big positions shared two things instead:

  consensus   many sharps leaning the same way (check.mark_consensus). His 4+
              sharp sides went 10-2 at his largest average stake; independently,
              the bot's ledger had 3+ sharp sides 10-7 (+13%) vs 24-27 (-14.5%)
              for exactly two. So 3 sharps is +1 step, 4+ is +2, and TWO IS
              NOTHING — two was the worst bucket in both samples.
  who         a bettor he trusts (form.py: trusted.json, plus the bot's own
              ledger once a bettor has 20+ wallet-games).

HONEST STATUS: every interval on that week contains zero — as staked [-4.8,
+56.3], the sizing lift alone [-3.1, +29.9] — and 9 positions made 79% of the
profit. The 552-game backtest cannot check either input: it has no NFL rows and
8 stacked sides. These are his priors, automated so they can be MEASURED, not
findings. Every published pick is stamped with SIZING_VERSION so s1 and s2 can
be split apart in a month and the loser deleted.

Both bumps apply on top of the untrusted-baseline cap rather than under it: the
cap exists because ONE wallet's size multiple cannot be believed, and four
wallets agreeing is a different kind of evidence that a dust median says nothing
about.

S3, 2026-09-21 (same day) — S2 CORRECTED AGAINST HIS OWN SHEET. s2 read his big
stakes as "consensus + Kch-Temp". A regression of his stake on everything at once
(64 positions, each factor holding the others fixed) says that was mostly
confounding: Kch-Temp bets football, and football is what he sizes up.

    NFL                  x2.31  [1.37, 3.99]   clear   (NCAAF x1.06 — it is NFL,
    totals               x0.68  [0.49, 0.97]   clear    not "football")
    long dog, +200 up    x0.56  [0.32, 0.98]   clear
    Kch-Temp on it       x0.93  [0.49, 1.87]   nothing
    3+ sharps            x0.80  [0.47, 1.35]   nothing — inside NFL his 3+ sharp
                                               sides were his SMALLER stakes
    Soarin22             on 4 of his 14 largest positions, 0 of his 30 smallest

So s3 sizes the way he demonstrably does: SPORT_MULT, TOTAL_MULT, LONGDOG_MULT,
and trusted.json. The multipliers are shrunk toward 1 from the point estimates
because 64 positions is one week. The consensus step stays, at +1 only, because
he names it as part of how he thinks — but neither his sheet nor bt_sizing.py
(consensus>=2: -28.7% out of sample) supports it, and it is the first thing to
delete if a month of s3 picks does not back it.

THIS DESCRIBES HIS SIZING; IT DOES NOT SHOW HIS SIZING WINS. bt_sizing.py found no
sizing rule with a positive out-of-sample interval, and its window holds 8 NFL
rows, so the NFL multiplier specifically is untested rather than refuted.

S4, 2026-09-22 — CONVICTIONS BIG, EVERYTHING ELSE SMALL. Week one of his sheet,
re-checked: 55% of tickets won but 63% of staked dollars did, because his
average winner was staked 1.39x his average loser; his top 10 tickets were 76%
of single-bet profit; +150-and-longer dogs lost $692 while everything at +149
or shorter made $4,156. The ladder above sizes every play on a continuum. s4
makes it two-tier instead:

  CORE   a play the record says he backs big: score >= CORE_CONV, price at or
         shorter than CORE_MAX_DOG, and at least one of NFL / trusted bettor /
         3+ sharp consensus. Gets the full ladder (up to 5u).
  REST   everything else that still qualifies. Capped at REST_CAP units, so it
         stays on the record without being able to wreck a week.

Long dogs are handled in picks.py as a SELECTION gate (not published at all
beyond PICK_MAX_DOG), because a bet that loses money at every size is not a
sizing problem. LONGDOG_MULT below now only governs the board's display sizing
for rows that sit between the two thresholds.

Same caveat as s2/s3, louder: one week. The winner/loser stake gap could be
deliberate or could be that his big bets happened to win. The tier is tagged so
a month of picks can answer that.

S5, 2026-09-23 — SIZE FROM THE SCORE BAND, NOT THE SIZE MULTIPLE. With v3 the
score is a rank of expected ROI, so it is the right thing to size on. Tested
cross-window on the pick set (v3 >= 70, -150 or better): a band table beat flat
in both directions (+10.8% vs +8.2%; +22.0% vs +16.0%) with equal or lower
drawdown, while a fitted Kelly calibration flipped between "all 0.5u" and
"1-3u" depending on which week it was fit on — too unstable to ship.

    v3 65-69   0.5u   board / alert only, never a graded pick
    v3 70-79   1u
    v3 80+     2u     (re-checked on the -180 scale: 90+ at 3u was worse in W1)

then the s3/s4 multipliers (NFL x1.5, trusted +1 step, totals x0.75, long dogs
x0.6) and snap to {0.5, 1, 2, 3, 4}. Four units is reachable only by an
80+ play that is BOTH NFL and from a trusted name.

vs_typical (the trader's bet against their own median) no longer moves the
size. Both windows found no dose-response in it and its 5-15x band was the
worst bucket (-22% CI excluding zero). It is still computed and printed.

A unit is whatever the reader's unit is. UNIT_USD exists ONLY to convert this
ledger's dollar stakes into units for reporting — it is never rendered. Nothing
published anywhere shows a dollar figure for Santhosh's own money.
"""
UNIT_USD = 200.0         # $ per unit, for converting the ledger to units only
SIZING_VERSION = "s6"    # s1 ladder; s2 +consensus; s3 his sheet; s4 tiers; s5 score bands; s6 FLAT
FLAT = True              # s6 (2026-10-07): 1u on every graded pick. Sized trailed flat live
                         # (-17% vs +2.4%), in all 7 OOS football months and the Sep rebuild
                         # (-27.3% vs -22.0%); the 90+ band took the biggest units and went
                         # 19-26. Set False to replay s5 in backtests. EXP-022.
OVERRIDE_UNITS = 0.5     # s6: a pick that publishes through a price_max override (a bet on
                         # a person, not a validated rule) risks half a unit until 40 games.
CORE_CONV = 80.0         # v3 80-89: +6.4% / +23.7% in the two windows; 70-79 is +3% in both
CORE_MAX_DOG = 0.50      # core = favourites/pick'em only. Both backtest windows
                         # (2026-09-22): dogs -20% / -17%, favourites -0.4% / +10%.
                         # Dogs to +150 still publish, as rest-tier 1u.
REST_CAP = 1.0           # ceiling for anything that is not core
TIERS = True             # s4 core/rest tiering; backtests flip this to replay s1-s3
USE_FORM = True          # trusted.json / ledger form bumps; same purpose
CORE_MIN, CORE_MAX = 2.0, 4.0   # his sheet at $200/u: 75th pct 1u, 90th 2u, max 4u.
                                # 5u only when the size multiple is extreme (below)
CORE_5U_VT = 15.0        # ... and the baseline is trusted
CONSENSUS_STEPS = [(3, 1)]           # (distinct sharps leaning this way >=, steps)
SPORT_MULT = {"NFL": 1.5}            # his sheet: x2.31, but NFL also makes a play core (s4), so shrunk
TOTAL_MULT = 0.75                    # his sheet: x0.68
LONGDOG_MULT = 0.6                   # his sheet: x0.56, entry below LONGDOG_PX
LONGDOG_PX = 1 / 3.0                 # +200 or longer

MIN_CONV = 65.0          # matches the alert gate; below it nothing is sized
# Set to 0 on 2026-09-21 at Santhosh's request: X and Discord must send the same
# plays. These were guards on the DENOMINATOR, not on play quality — units are
# stake / median-open-position, so a wallet holding a hundred $20 dust positions
# makes an ordinary bet read as 400x. Feromont ($24 median -> "414x") and
# AvocadosFromMexico ($16 -> "657x") are the shape they existed to stop.
#
# With them off, the cap below is the only thing standing between a dust median
# and a 5u public call, so it does the work instead: a play whose baseline is
# untrustworthy is still published, but never above CAP_UNTRUSTED units.
MIN_POSITIONS = 0
MIN_TYPICAL = 0.0
TRUST_POSITIONS = 10     # below this, the median is too few samples to believe
TRUST_TYPICAL = 250.0    # below this, the median is real but trivially small
CAP_UNTRUSTED = 1.0      # hard ceiling when the baseline cannot be trusted
SMALL_FOR_HIM = 0.25     # s5.1: below this x his median, one rung down
TINY_FOR_HIM = 0.10      # s5.1: below this, an alert, not a pick (picks.py)

LADDER = [               # (vs_typical at least, units)
    (40.0, 5.0),
    (15.0, 4.0),
    (5.0,  3.0),
    (2.0,  2.0),
    (1.0,  1.0),
    (0.0,  0.5),
]
STEPS = [0.5, 1.0, 2.0, 3.0, 4.0]      # s5: 5u retired
BANDS = [(80.0, 2.0), (70.0, 1.0), (65.0, 0.5)]   # (v3 at least, base units)


def _step(u, delta):
    """Move `delta` rungs along the ladder, clamped."""
    i = min(range(len(STEPS)), key=lambda k: abs(STEPS[k] - u))
    return STEPS[max(0, min(len(STEPS) - 1, i + delta))]


def recommend(h):
    """(units, reason, flags).

    `flags` are only the things that MOVED the recommendation — a cap, a stale
    entry, a thin hold. The raw inputs (hold, size-vs-average, P/L) are printed
    separately by the caller, so repeating them here would just crowd the line.
    """
    conv = h.get("conv") or 0
    if conv < MIN_CONV:
        return 0.0, f"score {conv:.0f} below {MIN_CONV:.0f}", ["below gate"]

    if FLAT:
        notes = ["core" if conv >= CORE_CONV else "rest"]
        if not h.get("fresh"):
            notes.append("stale")                       # reported, not sized
        u = 1.0
        try:
            import form
            tr = form._trust(h.get("user"))
            if tr and "price_max" in tr and (h.get("entry") or 0) > 0.643:
                u = OVERRIDE_UNITS
                notes.append("override")
        except Exception:
            pass
        return u, f"v4 {conv:.0f} · flat", notes

    # s5 — base size from the score band. The size multiple is reported, not used.
    u = 0.0
    for lo, base in BANDS:
        if conv >= lo:
            u = base
            break
    why = f"v3 {conv:.0f}"
    notes = []
    vt = h.get("vs_typical")
    if vt:
        why += f" · {vt:.1f}x their normal"
    if not h.get("fresh"):
        u = _step(u, -1)
        notes.append("stale")
    capped = False

    # s2 — consensus between sharps, and who is on it. See the header.
    n_sharps = h.get("consensus") or 1
    for need, steps in CONSENSUS_STEPS:
        if n_sharps >= need:
            u = _step(u, steps)
            notes.append(f"{n_sharps}sharps")
            break
    step, flag = 0, ""
    if USE_FORM:
        try:
            import form
            step, flag = form.adjust(h.get("user"))
        except Exception:
            step, flag = 0, ""
    if step:
        u = _step(u, step)
        notes.append(flag)

    # s5.1 (2026-09-28) — a bet that is tiny FOR THIS TRADER does not earn his
    # full size. Kch-Temp's $10.9K Over 41.5 (0.23x his NFL median; his 3rd-
    # smallest NFL bet of 35) went out at 3u on the strength of his trusted
    # step alone. Only applies when his median is believable (size_trusted).
    if vt and vt < SMALL_FOR_HIM and (h.get("n_open") or 0) >= TRUST_POSITIONS \
            and (h.get("typical") or 0) >= TRUST_TYPICAL:
        u = _step(u, -1)
        notes.append(f"{vt:.2f}x his normal")

    # s3 — scale by what he demonstrably sizes on, then snap back to the ladder
    mult = SPORT_MULT.get((h.get("league") or "").upper(), 1.0)
    if mult != 1.0:
        notes.append((h.get("league") or "").upper())
    mkt_title, side = (h.get("market") or ""), (h.get("side") or "")
    if side in ("Over", "Under") or "O/U" in mkt_title:
        mult *= TOTAL_MULT
        notes.append("total")
    if 0 < (h.get("entry") or 0) < LONGDOG_PX:
        mult *= LONGDOG_MULT
        notes.append("longdog")
    if mult != 1.0 and u:
        u = min(STEPS, key=lambda st: abs(st - u * mult))

    # s4 tiers are implied by the bands now: 80+ is "core", 70-79 is "rest".
    if u:
        notes.append("core" if conv >= CORE_CONV else "rest")
    u = min(u, 4.0)

    return u, why + (" · " + ", ".join(notes) if notes else ""), notes


def is_core(h):
    """The plays his record says he backs big — see the s4 header."""
    if (h.get("conv") or 0) < CORE_CONV:
        return False
    if not (0 < (h.get("entry") or 0)) or h["entry"] < CORE_MAX_DOG:
        return False
    # 2026-09-23: under v3 the score band IS the evidence (80-89: +6.4% with no
    # NFL in window 1, +23.7% in window 2). The old NFL / trusted / 3-sharps
    # requirement was an s4 read of his sheet, not an outcome finding; NFL keeps
    # its multiplier and trusted keeps its step, but neither gates the tier.
    return True


def _ladder(vt):
    for lo, u in LADDER:
        if vt >= lo:
            return u
    return 0.5


def fmt(u):
    if not u:
        return "—"
    return f"{u:g}u"


# dollars() was removed deliberately. Posts carry units and ROI and nothing
# else; a helper that renders Santhosh's stake in dollars is a footgun sitting
# next to the composers, so it does not exist rather than going unused.


def public_size(h):
    """(units, label) as every public surface must show it.

    Conviction v4 rates the bettor and ignores the odds, so a 100 on a +140 dog
    is real information but NOT a play. Inside picks.py's price window and past
    the pick gate: the units (locked to the published size if already out).
    Past the alert gate but outside the window: no units, "alert". Else nothing.
    Used by the X card, the X caption, the Discord board and the terminal board.
    """
    import picks
    ok, _ = picks.qualifies(h)
    if ok:
        u = picks.locked_units(h, recommend(h)[0])
        return (u, fmt(u)) if u else (0.0, "alert")
    if (h.get("conv") or 0) >= 65:
        return 0.0, "alert"
    return 0.0, "—"
