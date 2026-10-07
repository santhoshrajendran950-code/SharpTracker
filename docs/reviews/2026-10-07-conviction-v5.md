# Can the conviction score be made predictive of ROI? — out-of-sample study

*2026-10-07. Script: `research/score_v5.py` (numpy only). Data: every
cut-A-gated row in the 99 research sport-months — 18,124 positions with the
T-60 price, outcome, and close. Chronological split: TRAIN = 37 windows up to
2026-03 (9,753 rows), TEST = 36 windows from 2026-04 (8,371 rows: esports
3,428, MLB 1,984, soccer 1,287, NBA 954, football 428, WNBA 153, NHL 115).
Everything scored is known at T-60; no close, CLV, month P/L or leaderboard
rank is used.*

## The short answer

**No.** Nothing available at bet time orders ROI out of sample — not the
bettor's profile, not the bet's shape, not a fitted model of them, and not
v4. What *can* be predicted is the **line move** (signal CLV), and a score
built for that is robust across the split. It is not an ROI score and should
not be presented as one.

## 1. Univariate: bins set on train, graded on test

Every v4 input and every candidate feature, quintile-binned on train:

| Feature | Train ordering | Test | Verdict |
|---|---|---|---|
| prior hold | not monotone | **flips** | the 45-point v4 component orders nothing |
| prior P/L | not monotone | same direction, flat | nothing |
| stake | not monotone | **flips** | the 45-point v4 component orders nothing |
| size vs typical | not monotone | same direction | nothing (s1 refuted again) |
| open positions | not monotone | **flips** | nothing |
| fresh | monotone | same direction | stale −6.6% → −9.8%, fresh −1.4% → −4.5% (n=329 stale). **Opposite sign to the live window** (stale +4.5%). Not robust. |
| stack ≥ 3 | monotone | same direction | +3.6% → +7.3%, **n = 75**. A lead at best. |
| consensus | not monotone | **flips** | nothing (fourth sample) |
| drift toward since entry | not monotone | same direction | −7.0% … −0.0% on test; see §4 |
| trader prior CLV | not monotone | same direction | top bin +4.6% → +0.4% |
| **v4 score** | not monotone | **flips** | <60 −9.3%, 60–70 −3.3%, 70–75 −3.4%, 75–90 −4.0%, 90+ −1.1% |

Inside the −122..−180 window the picture is the same with more noise.
**v4 bands on test, inside the window:** 65–69 −1.6%, **70–79 +2.2%**,
**80–89 −12.5% [−23, −2]**, 90+ −0.0%. The 80–89 band is now negative with an
interval excluding zero in the research test set *and* the live window
(−19.3% [−38, −1]); 70–79 is the best band in both. The score is not
monotone, and the one band it singles out for the biggest size is the worst.

## 2. A fitted ROI model does not survive the split

Ridge logistic regression on *won*, with the market price as an offset so
the model has to beat the price rather than reproduce it. Train quintiles of
predicted edge order ROI (Q5 +4.4% [+0, +9] all rows; +11.9% [+2, +22] in the
window). **Test:** Q5 −2.6% all rows, −1.4% in the window; rows with
predicted edge > +3 pts: −3.9% [−17, +8] in the window. The coefficients are
all within ±0.06 sd except stack (+0.14 in the window). Same result as the
logistic and stump fits on W1/W2 in September, now on 37 → 36 windows.

## 3. The trader's prior CLV, alone

Point-in-time (positions that kicked off before the window, n ≥ 20):
test, inside the window — < −1: −4.6%; −1..0: +3.5%; 0..+1: −1.8%; > +1:
−4.3%; no history: −4.6%. It predicts the trader's *next* CLV (+0.35 vs
−0.25) and not their ROI. Consistent with the persistence study.

## 4. What is predictable: the line move

Ridge regression on signal CLV (close − entry, pts) with the same features.

| Test quintile by predicted CLV | actual CLV | ROI at T-60 | ROI at the sharp's entry |
|---|---|---|---|
| Q1 | −3.15 | −7.3% [−12, −2] | −12.3% |
| Q2 | −0.85 | −7.4% [−12, −3] | −9.0% |
| Q3 | −0.25 | −6.6% [−11, −2] | −7.0% |
| Q4 | +0.47 | −1.1% [−5, +3] | −0.1% |
| Q5 | +2.55 | −1.3% [−6, +5] | +3.7% |

Monotone in CLV, monotone in ROI at both prices. One feature carries it:
**drift toward the bet between entry and T-60** (+2.8 pts per sd); everything
else is ≈ 0. In words: a line that has already started moving toward the
sharp keeps moving; a line that has moved away keeps moving away. That is
known at T-60 and is cheap to compute (it is `drift` in `check.py`).

Its value is **exclusion, not selection**. The bottom 60% by predicted move
lose 6–7% at T-60 in every quintile; the top 40% are ≈ −1.2%, i.e. the
closing price minus vig. Inside the window the CLV separation holds (+2.21
vs −0.90) but ROI does not (−3.0% vs −3.2%), and by sport it flips (MLB
top-40 +13%, esports/soccer top-40 worse). So: a robust CLV score, not a
robust ROI score — and the only ROI it buys is by *not* following the
bottom 60% at any price.

## 5. Recommendation

1. **Stop presenting the score as an ROI ranking.** v4 stays as the
   trader-quality **gate** (hold, P/L, stake) that decides whether a
   position is shown at all; sizing is already flat. Rename the column from
   *Conv* to *Profile* so nobody reads 92 as "better than 74". The 80–89 band
   finding (EXP-022) is now three-for-three.
2. **Add the line-move tier as a second column** (`research/linemove_v5.json`:
   coefficients, standardisation, quintile cuts): *moving toward / flat /
   moving away*. Rows in the bottom 60% ("moving away") are alerts only, never
   picks, at any price. Rows in the top 40% are ordered first in the email.
3. **Pre-register the forward test (EXP-030)** before any of this touches a
   gate: on live signals, the "moving away" tier must show CLV < 0 and the
   "moving toward" tier CLV > +1 over 300 signals, and the pick-rule ROI of
   the kept rows must not be worse than today's. If it fails, the column
   stays informational.
4. **Do not fit anything else to ROI.** This is the third attempt on the
   third dataset. The honest finding is that at T-60 the follower is at the
   closing price, and no bettor characteristic changes that.

Nothing here is installed. The scorer is ready to wire into `check.py`
behind a flag when Santhosh says so.
