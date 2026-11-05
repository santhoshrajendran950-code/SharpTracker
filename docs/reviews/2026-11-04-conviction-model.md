# A conviction score (0–100) and a bet / no-bet call — EXP-039

*2026-11-04. Asked by Santhosh: "a machine learning decision tree model to
identify conviction % out of 100", built from the bettor's profile, stacked
conviction (same-side sharps up, opposing sharps flagged) and the line move
from placement to posting; then a simple yes/no.*

Code: `research/conviction/` (`features.py` → `model.py` → `score.py` /
`evaluate_v6.py` / `live_check.py`). Full tables:
[`results/exp039-ml-walkforward.md`](../../results/exp039-ml-walkforward.md),
[`results/exp039-v6-research.md`](../../results/exp039-v6-research.md),
[`results/exp039-v6-live.md`](../../results/exp039-v6-live.md).

## The short answer

1. **The decision-tree and boosted-tree models do not work out of sample.**
   Every freely fitted model was *worse than the market price* on the
   walk-forward months, with intervals that exclude zero. The most constrained
   model (signs forced to the hypotheses, stumps only) only ties the price.
   Ten thousand plays is not enough signal to fit a tree to ROI. This is the
   fourth dataset where a fitted ROI model has failed (EXP-008, the 10-07
   study, EXP-029).
2. **Three hand-built gates do hold up, and they stack.** Bet only when a side
   has **two or more sized sharps** (hold > 3%, each above 2× their own career
   median), **no gated sharp on the other side**, and **the line has not moved
   2 points or more against them** since they entered. That's 348 plays on
   334 games, **+7.7% [−0.2, +15.7]** at the price a follower gets (T-60 + vig),
   65.8% hit, 9 of 14 months positive. The interval only just reaches zero.
3. **Conviction v6** is a points score built from those same pieces, plus
   small bonuses for extra sharps and the lead's record on big bets. It ranks
   plays in order: 0–40 −3.8%, 40–55 −4.2%, 55–70 −0.1%, 70–85 +0.6%,
   **85–100 +27.8%** (56 plays). It is in `research/conviction/score.py`, uses
   only the standard library, and runs on the host Python unchanged.
4. **What argues against it:** follower CLV on the bet is about zero
   (+0.08 pts). ROI is up but CLV is not, which is the same warning EXP-034
   carried. The design was chosen after EXP-030/034/036 had already been run
   on this corpus, so this is a consistency check, not a clean test. On the
   live ledger the bet fired **twice in eight weeks**, so live can't confirm or
   refute it yet. A forward test is pre-registered below.

## Data and leakage controls

- **Corpus:** every `bt_scored.jsonl` in `~/nl-research/windows` (football
  months plus the 10-sport run and NCAAB Nov–Feb). That's 460,943 positions
  rebuilt at T = kickoff − 60 min.
- **A play** is one (game, market, side) that at least one gated sharp holds
  at T-60. Gate = cut A: prior 90-day hold ≥ 1.5%, P/L ≥ $50K, volume ≥ $500K,
  stake ≥ $2.5K, pregame, not two-sided, price 0.05–0.95. That gives
  **12,356 plays on 6,898 games, Sep 2025 – Oct 2026.** Following every one
  returns −2.5% [−3.8, −1.3].
- **Kickoff time:** `t_ts = kickoff_ts − 3600` on 100% of rows, and the
  kickoff falls on the slug's US-Eastern date for 97.5% of rows (most of the
  rest are late games that cross midnight). So the T-60 snapshot is keyed to
  the game start, not gamma's market-open `startDate`. The windows built
  before the 10-20 hist_slate fix may be *missing* games, but the games they
  do have are timed correctly.
- **Never used as a feature:** the close, the result, `month_pnl` (it covers
  the window being scored), and the corpus `drift` field. That field is the
  *worst adverse move after T*, which is look-ahead. The line-move features
  here are computed fresh as T-60 price minus entry.
- **Wallet history is expanding.** An earlier position counts toward a
  wallet's median, trade count, ROI, CLV and big-bet record only once its game
  had kicked off before this play's T. Records are shrunk toward zero with
  20 pseudo-bets.
- **Grading:** *follow* = flat 1u at the T-60 price + 1.5 pts vig, which is
  what a subscriber could get. *Entry* = at the lead sharp's own price + vig,
  shown for reference only. CLV = close − T-60 price. Intervals are 90% and
  resample **games**.

## The features (the three asks)

| family | features (lead sharp = the biggest gated stake on the side) |
|---|---|
| **Profile** | prior hold, P/L, volume; stake; stake ÷ own career median; stake ÷ open-book median; stake ÷ prior volume (bankroll share); number of earlier positions; open positions; own earlier flat ROI and CLV; **own ROI and CLV on earlier bets > 1.5× their median**; hedged legs; stacked legs |
| **Stack** | gated sharps on the side; how many are > 1.5× and > 2× their median (with hold > 3%); their money; **gated sharps on another side of the market (opposition flag)**, how many of those are oversized, their share of the money; other $2.5K wallets opposite; the side's share of all $2.5K money |
| **Move** | T-60 price − lead entry; the same, money-weighted across the side's sharps; the same from the earliest entry (all in points, clipped ±20) |

## 1. The ML models: walk-forward by month

For each month from 2026-01 to 2026-10, each model is fit on the plays that
kicked off before that month, then scored on the month itself. That's 9,535
out-of-sample plays. The market price is an **offset**, not a feature: the
model starts at the implied probability and can only learn how far to move off
it, so it can't look good just by rediscovering that favourites win.
Conviction = the play's percentile of predicted edge among that fold's
training plays. The bet rule is predicted edge > 0 and conviction ≥ 80. The
settings were fixed before the first run: depth-2 trees, 300+ plays per leaf,
L2 10, and 250 rounds at 0.02.

| model | bet: plays | follow ROI [90%] | log-loss vs the price, per 1,000 plays [90%] |
|---|---:|---|---|
| Profile · boosted | 1,750 | +0.2% [−3.8, +4.4] | **−1.51 [−2.56, −0.45]** |
| Stack · boosted | 1,968 | −1.2% [−5.3, +2.7] | **−1.06 [−2.07, −0.15]** |
| Move · boosted | 1,970 | −3.3% [−7.8, +0.4] | **−1.29 [−2.20, −0.38]** |
| All · boosted | 1,727 | +0.1% [−4.0, +4.4] | **−1.53 [−2.56, −0.43]** |
| All · single depth-3 tree | 1,772 | +0.3% [−4.1, +5.0] | **−2.21 [−3.63, −0.84]** |
| Hypotheses, monotone stumps | 1,226 | −0.1% [−4.7, +5.1] | +0.14 [−0.33, +0.60] |

A negative log-loss gain means the model's probabilities are *worse* than the
market's. The All model's bet went **+6.3% in Jan–May and −7.8% in Jun–Oct**,
which is noise that looks like signal for half a year. The readable tree's
first split is the lead's CLV history, then the line move, then the lead's
big-bet ROI. Those are plausible splits, but they don't hold on new months.
**Verdict: no fitted model ships.**

## 2. Each component alone (all 14 months)

| component | cut | plays | follow ROI [90%] | months + |
|---|---|---:|---|---:|
| Profile | lead stake > 2× own median | 6,535 | −1.6% [−3.3, +0.2] | 6/14 |
| | lead stake ≤ 2× | 5,083 | −3.3% [−5.7, −1.0] | 3/14 |
| | lead's big-bet ROI ≥ +5% | 4,095 | −1.1% [−3.9, +1.6] | 4/14 |
| | lead's big-bet ROI ≤ −5% | 1,913 | −3.6% [−7.5, +0.2] | 4/14 |
| Stack | ≥ 2 sized sharps (any opposition) | 1,063 | −3.5% [−7.7, +0.7] | 7/14 |
| | 1 sized sharp | 4,713 | −1.7% [−3.7, +0.4] | 6/14 |
| Opposition | **gated sharp opposite** | 4,864 | **−3.3% [−4.3, −2.3]** | **1/14** |
| | ≥ 2 sized but contested | 685 | **−7.6% [−13.0, −2.5]** | 3/14 |
| Move | **≥ 2 pts against since entry** | 1,326 | **−7.7% [−13.5, −2.7]** | 4/14 |
| | −2 .. +2 | 9,684 | ≈ −2.3% | |
| | ≥ 2 pts toward | 1,346 | +1.0% [−3.2, +5.1] | 7/14 |

No single component *selects* winners on its own. Two of them reliably
*exclude* losers: **opposition** and **a line that ran against the sharps**.
Both match earlier findings (EXP-027 veto, EXP-030 "moving away").
Profile features only tilt results, and the tilts are inside the noise.
Stacking helps only when the stack is **sized** and **uncontested**.

## 3. Combined: the bet gate and the v6 score

| the bet, one gate removed at a time | plays | follow ROI [90%] | months + |
|---|---:|---|---:|
| **all three gates** | **348** | **+7.7% [−0.2, +15.7]** | **9/14** |
| without the move gate (= EXP-034's shape) | 378 | +4.1% [−3.4, +11.8] | 10/14 |
| without the opposition gate | 965 | −1.7% [−6.3, +2.7] | 8/14 |
| without the stack gate (≥ 1 sized sharp) | 2,830 | +0.9% [−2.1, +4.0] | 7/14 |
| with the gates, but ≥ 2 sized sharps and the line ran ≥ 2 away | 30 | −37.6% [−64, −12] | 2/11 |

Removing any one gate costs ROI. Removing the opposition gate costs the most.

**By period:** Sep–Dec 2025 −5.5% (97 plays), Jan–May 2026 +16.8% [+4.6, +30.5]
(143), Jun–Oct 2026 +7.3% (108). **By sport:** esports +19.7% (72),
soccer +16.0% (39), MLB +9.7% (49), NBA +7.6% (106), NCAAF +4.3% (22),
**NFL −14.0% (39)**, NHL −27.4% (16). Moneylines +9.5% [+0.5, +18.0]
(288 plays); spreads and totals are flat on about 30 plays each. Dropping the
best month leaves +7.0%.

**Conviction v6 (`score.py`).** Start at 50. Sized sharps on the side: 1 → +10,
2 → +25, 3+ → +30. Other gated sharps: +3 each, capped at +6. **Contested
(any gated sharp opposite): −30.** Line moved ≥ 2 pts against: −25; −0.5 to −2:
−5; ≥ 2 toward: +5. Lead's big-bet record ≥ +5%: +5; ≤ −5%: −5. Clip to 0–100.
The score comes back with its reasons
(`"2 sized sharps +25", "CONTESTED: 1 sharp(s) opposite -30"`), so a
subscriber can see why a play scored what it did. **BET = the three gates
pass.** BETs score 65 to 91, with a median of 78.

| v6 band | plays | avg price | hit | follow ROI [90%] |
|---|---:|---:|---:|---|
| 0–40 | 4,771 | 0.49 | 48.5% | −3.8% [−5.5, −2.1] |
| 40–55 | 3,380 | 0.53 | 52.0% | −4.2% [−6.7, −1.3] |
| 55–70 | 3,723 | 0.55 | 56.9% | −0.1% [−2.6, +2.5] |
| 70–85 | 426 | 0.59 | 60.6% | +0.6% [−6.2, +7.7] |
| 85–100 | 56 | 0.62 | 78.6% | +27.8% [+10.9, +46.8] |

Hit rate climbs in every band in all three periods, but most of that is
price: the average T-60 price climbs too, from 0.49 to 0.62. Only the 85+ band
wins clearly more often than its price says (78.6% against 62%). ROI goes up
with the band, except that 0–40 and 40–55 are the same.

## 4. The live ledger (independent months, real posting times)

`live_check.py` re-scores the settled signals in `~/.nightly-line/ledgers`
(Sep 10 – Nov 3): 1,452 plays on 742 games. Every play lost −4.1% at entry.
The live `move` field is about 1.04 × (posting price − entry), so it stands in
for placement-to-posting movement.

- **The bet fired twice (one game).** The live pool rarely has two sized
  sharps on one side. Live can't test the rule yet.
- **Plain stacking failed live:** ≥ 2 sharps on the side, uncontested, but
  *not* sized went **−17.8% [−32.9, −2.5]** on 143 plays. More sharps alone is
  not more confidence. Only stacks that are oversized for each bettor ever
  separated in research.
- Only 9 live plays had moved ≥ 2 pts against by the time they were logged.
  The live board posts within minutes of entry, so the move gate rarely
  triggers live.
- The shadow rule R1, the closest relative to this bet, is 14–11 (+4.1% at
  entry) since 10-22.

## 5. Recommendation

1. **Binary yes/no = the v6 gates.** Two or more sized sharps, uncontested,
   and the line not ≥ 2 pts against at posting. It is EXP-034 plus the
   EXP-030 move veto, and every piece of it was judged on evidence collected
   before this study.
2. **Conviction % = the v6 score with its reasons**, shown next to *Profile*
   on the board. Size flat (1u). Five sizing studies say a score band should
   not set the stake, and the 85+ band has only 56 plays.
3. **Don't ship a fitted tree.** Re-run `model.py` when the corpus has doubled.
   It is cheap, and the log-loss column says whether a fitted model has
   started to beat the price.
4. **Nothing here is live.** Wiring v6 into `shadow.py` (log only, next to
   EXP-034) is a small change, waiting on your word. The bigger gap is volume:
   about 25 bets a month in research, about 1 a month live. If the gate is
   adopted, the next experiment is widening the live sharp pool, not loosening
   the gate.

**Pre-registered (EXP-039 forward test):** log every v6 BET live, along with
v6 bands for every board play. After **100 BETs**, the rule passes if follow
ROI at the posted price is ≥ the EXP-034 benchmark over the same dates **and**
signal CLV ≥ 0. Separately, the 0–55 bands must be below the 70+ bands on CLV.
If either fails, v6 stays an informational column.
