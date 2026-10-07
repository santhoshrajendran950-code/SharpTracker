# Why per-trader overrides stop working, and what is robust instead

*2026-10-07. Script: `research/trader_persist.py`. Data: every scored
position in the 99 research sport-months plus the live ledger — 440,300
settled positions, 9,520 traders, 1,283 with 60+ settled.*

The overrides worked for the two weeks they were set on and failed for the
two weeks after. That is not a timing problem. It is what selecting on a
small sample does, and the full history shows it directly.

## 1. A trader's ROI does not persist. At all.

Split every trader's record chronologically in half.

| | Half 1 | Half 2 |
|---|---|---|
| Rank correlation of ROI, half 1 vs half 2, 1,283 traders | **−0.00** | |
| Top quartile by half-1 ROI | +13.0% (n=23,840) | **−4.9%** (n=23,997) |
| Bottom quartile by half-1 ROI | −17.1% (n=26,584) | −4.2% (n=26,748) |

Both quartiles land on the pool mean (−2.9%). The same holds **inside every
price band**, which is exactly the override question:

| Band | Traders with 30+ in band | Rank corr | "Licensed" on half 1 (ROI > +10%) | …their half 2 |
|---|---|---|---|---|
| dog | 1,162 | −0.03 | 311 | −7.5% |
| pick'em | 434 | −0.01 | 72 | −3.8% |
| window −122..−180 | 492 | +0.01 | 83 | −2.0% |
| short −180..−300 | 346 | +0.03 | 74 | +3.2% |
| very short | 347 | +0.02 | 36 | −3.5% |

Empirical-Bayes on win rate inside the window band: the observed spread of
trader win rates (0.074) is *smaller* than the sampling noise expected if
every trader were identical (0.080). **Implied true-skill spread: zero.** The
correct shrinkage of any trader's own band record toward the pool is 100%,
at any n in this data. The named traders show the pattern one by one:
Kch-Temp pick'em +13% → −23%; justaluckydude short +41% → −38%; Soarin22
window +14% → −10%; primm pick'em −4% → −24%.

**Conclusion:** no override built on a trader's win rate or ROI in a price
band can be robust, because there is nothing there to be robust about. The
`price_min` retirement today was right; `price_max` rests on the same kind of
sample and should go the same way.

## 2. What does persist: CLV

| | Rank corr half 1 → half 2 |
|---|---|
| Trader CLV (close − entry) | **+0.60** |
| Trader CLV half 1 → trader ROI half 2 | +0.06 |

The market reliably keeps moving toward the same traders' positions after
they bet. Top-CLV quartile: +1.14 pts in half 1, +0.74 in half 2; bottom:
−1.10 → −0.90. That is a real, persistent trader trait — the first one found
in this project. It also explains the 90-day hold gate: hold predicts CLV
(1.5–3% hold → +0.29 pts) and nothing else.

But it **does not turn into ROI at T-60**: the top-CLV quartile still lost
3.5% in half 2. +0.7 pts of line movement is smaller than the 1.5 pts of vig,
and by T-60 most of the move has already happened. The three override
traders with the best win rates on short favourites (justaluckydude,
CandleHammerDrums) have CLV of **−15 to −21 pts** in that band: they buy
favourites far above where the market closes. Their wins are outcomes, not
edge, and the sign says a follower is paying the worst price in the market.

## 3. The robust design

Replace "bets on people" with one measured, self-updating trader property.

**A. No per-trader price windows.** Retire `price_max`. The price window,
market type, contested rule and league are population rules tested on 99
windows; trader identity stays out of the gate.

**B. A CLV license, not an ROI license.** Each tracked wallet carries a
rolling signal-CLV (last 100 settled signals, pooled research + live, n ≥ 60).
It is recomputed weekly by `trader_persist.py` and written to
`clv_license.json` with tier = top / middle / bottom quartile. It never
changes *which* price is allowed; it changes **urgency and price discipline**:

| Tier | Alert | Pick |
|---|---|---|
| top quartile CLV | first in the email, pushed immediately | play only at a book price **≤ sharp's entry + 1 pt** (the move is coming; late entries pay it) |
| middle | normal | normal pick rule |
| bottom quartile (CLV < −1) | shown, marked "market fades this wallet" | **no pick** — the market has been right to fade them |

**C. Pre-registered, out of sample.** EXP-029 tests B before it is installed:
on the research windows, apply "top-CLV tier at a price ≤ entry + 1 pt vs
everyone at T-60". Pass: CLV-tier picks ≥ +0 ROI with the interval excluding
the pool mean in ≥ 3 sports. Fail: no separation → CLV is informational only.

**D. Anything else about a person expires.** If a trader is ever given a
special rule again, it is written as an experiment with an n-based stop
(EXP-020's 40-game bar) and the review date in `trusted.json`. The record now
says such a rule should not be expected to survive.

## 4. What this does to the current trusted.json

| Trader | Entry today | Verdict from the full record |
|---|---|---|
| Kch-Temp | size bump, `price_min` retired | pick'em +13% → −23%; CLV +0.3–0.5 (middle). Keep the bump only if the bump itself is re-tested; no price exception. |
| BrotherObama | `price_min` retired | dog −14% → −25%, pick'em +6% → −40%, CLV negative. Remove from trusted entirely. |
| justaluckydude | `price_max` 0.75 | short +41% → −38%, CLV −15 to −21 on favourites. Retire `price_max`; bottom-tier CLV on favourites. |
| CandleHammerDrums | `price_max` 0.75 | very short 7-0 → 6-1 but CLV −15/−21. Same. |
| Soarin22 | size bump | 1,113 settled, every band regresses; CLV +0.0–1.5 (top-ish). Candidate for the CLV tier, not a size bump. |
