# Experiment ledger

Everything tested on the alert/pick rules, in order, with its verdict. New
entries follow [`TEMPLATE.md`](TEMPLATE.md). The current rules this ledger
justifies are in [`../alert-rules.md`](../alert-rules.md). Rejected ideas stay
here so they are not re-run without new evidence.

Windows referenced: **W1** = Aug 16–Sep 15 2026 rebuild (8,271 positions,
4,789 games); **W2** = Sep 14–21 2026 live ledger; **football OOS** = seven
NFL/NCAAF months Sep 2025–Sep 2026, each discovering its own sharps;
**all-sports** = 99 sport-months, 10 sports, finished 2026-10-02
(`results/sports/cross_sport_snapshot.txt`); **live** = `picks.jsonl`, cut by
`research/review.py` into `results/live/`.

| # | Date | Hypothesis | Verdict | Evidence |
|---|---|---|---|---|
| 001 | 09-11 | The "Kch-Temp shape" (stacked + quality + **underdog** + fresh) is the pattern to chase | **superseded** | n=2 when adopted; the underdog trait was refuted by 003/010 — dogs −20% / −17% in W1/W2 |
| 002 | 09-14 | Conviction v2: reweight on 31 CLV observations (stack 1.15 × dog 1.45 × fresh 1.10) | **rejected** 09-23 | anti-monotone in both windows; top quintile −21% / −15% because the dog multiplier scored every big underdog 100 |
| 003 | 09-21 | s1: size from the trader's bet vs their own median (`vs_typical` ladder) | **rejected** | no dose-response; 5–15× band −22.0% [−36, −6]; s1 −21% on W1 test split (`bt_sizing.py`) |
| 004 | 09-21 | s2: consensus steps (2, 3+ sharps) and trusted names | **rejected** (consensus) | consensus == 2 the worst bucket in three samples; ≥ 3 contradictory (−14% W1, +10% W2 on 22 games). Live 10-07: 2 → 7-8, 3+ → 0-3 |
| 005 | 09-21 | s3: the operator's own sheet as multipliers (NFL ×2.3, totals ×0.68, long dogs ×0.56) | **descriptive only** | describes his sizing; totals contradicted (−21% W1, +3% W2); NFL kept at ×1.5, totals ×0.75 as s5 inputs |
| 006 | 09-22 | s4: core/rest tiers + +150 gate + 4 rest picks/day | **partly adopted** | 37% less risked, shallowest drawdown — "loses less by betting less". Tiers and the daily cap survive in s5 |
| 007 | 09-21 | Kelly / calibration sizing | **rejected** | quarter-Kelly bet 26 of 3,309 rows; fitted calibration flipped between "all ½u" and "1–3u" with the fit week |
| 008 | 09-23 | Fitted scores: logistic, boosted stumps | **rejected** | inverted / non-monotone out of sample; stump importance (price 52%, wager 26%) just rediscovered v3. sklearn not installed anyway |
| 009 | 09-23 | Conviction v3/v3.1: price term 50 + 175·(p−0.5) + bonuses | **superseded same day** | ordered win rate perfectly but mixed price into the score; operator: "price should just be a filter" |
| 010 | 09-23 | **v4** bettor-only score + **−122..−180** price filter | **adopted** (live) | inside the window, score ≥ 80: +18.5% W1 (n=45), +28.1% W2 (n=17). Forward: football OOS −7.4% [−19, +4] on 238 picks, CLV +0.16; all-sports −2.7% [−7, +1] on 1,869; live 51 picks CLV +2.07 ± 0.88 |
| 011 | 09-23 | **s5** score-band sizing (½/1/2u + NFL/trusted/totals/stale) | **adopted, contradicted OOS** | beat flat on the pick set cross-window; trailed flat in all 7 football OOS months and 6 of 8 NBA months. Earns its place on drawdown only |
| 012 | 09-24 | Per-trader price overrides (Kch-Temp to pick'em; justaluckydude, CandleHammerDrums to −900) | **adopted as bets on people — contradicted live** | set from the same 32 / 22 / 20 signals that seeded trusted.json. Live 10-07: override picks 12-19, −26% [−54, +1]; pick'em band 10-19, −33% [−60, −6] on 29 games |
| 013 | 09-15 | Port the method to BTC/ETH daily markets | **rejected** | best 4 wallets +0.45% over 13 weeks; population is market makers |
| 014 | 09-30 | NHL trusted candidates from the 2025-26 windows | **parked** | nobody clears n≥20, CI>−10, CLV≥0; one paper-watch; re-grade after 40–50 settled NHL bets |
| 015 | 09-23 | Wallet discovery (`holders.py --discover`) adds signal the leaderboard misses | **adopted, unmeasured** | no `source` field on signals/picks, so discovered-wallet picks cannot be cut apart. See 024 |
| 016 | 09-28 | s5.1: a bet tiny for this trader (< 10% of own median) is an alert, not a pick; < 25% one rung down | **adopted, unmeasured** | rationale only; no cut yet |
| 017 | 09-24→10-02 | Tighter wallet gates (hold 3–5%, more settled games) beat the live gate | **rejected** | across 99 windows cuts C–E raise CLV and lower n; ROI unchanged or worse (Nov/Dec football worse) |
| 018 | 10-02 | Any sport carries the signal out of sample (all-sports screen) | **mostly rejected** | cricket: 1 pick/yr. NBA ML −14.6% [−27, −3] n=320 — the only cut whose CI excludes zero, and it is negative. Football −7.4%. **Esports +5.9% [−2, +14] n=461, CLV +0.20** — the one positive point estimate (see 023) |

## Weekly review 2026-10-07 (first; by hand, Claude)

**State.** 51 / 200 v4/s5 picks graded. Mean CLV +2.07 pts (± 0.88 at 90%),
38/51 beat the close — the KPI is on track. Flat ROI on those picks −1.5%
[−27, +22]; sized −12%. All-time ledger 32-33, −26.9u.

**Contradictions of adopted rules** (from `results/live/2026-10-07.md` and
the all-sports table):
1. **The pick'em override (012)** is the worst live cut with a CI excluding
   zero: pick'em band 10-19, −33% [−60, −6], 29 games. The default window is
   20-14, +28%. The override was set on the sample that seeded it.
2. **s5 sizing (011)** trails flat in every football OOS month and live
   (−12% vs −1.5%). Its only claim is drawdown.
3. **Totals ×0.75 (005)**: live totals 5-11, −41% [−76, −5] (n=16) but
   research totals are the best market type in NBA (+16.1% [−4, +33], n=72)
   and MLB (+12.5%, n=26). Conflicting; neither is ≥ 40 games.

### EXP-019 — Totals are not a down-weight; they may be the best market type
- **Status:** proposed · **Rule:** `units.TOTAL_MULT = 0.75` · **Prior:** NBA/MLB totals above, live totals the opposite
- **Hypothesis:** pooled across all research windows, totals have CLV ≥ moneylines and flat ROI with a CI excluding zero.
- **Test:** `cd ~/nl-research && python3 hist_report_all.py` → the `total` rows for every sport under cut A; pool by hand (n, W-L) across sports. Pass: ≥ 150 total games pooled, ROI CI > 0 and CLV ≥ moneyline CLV. Fail: CI includes 0 → leave ×0.75, re-check live at 40 total games.
- **Why first:** cheapest test; the data is on disk.

### EXP-020 — Retire or cap the pick'em override
- **Status:** proposed, **pre-registered** · **Rule:** `trusted.json` Kch-Temp `price_min 0.50` · **Prior:** live pick'em 10-19, −33% [−60, −6]
- **Hypothesis:** override picks are negative-expectation; the live cut will stay below zero at 40 games.
- **Test:** `python3 research/review.py` weekly; decision point when the *override* cut reaches 40 games (~2 weeks at the current rate). Pass (retire): flat ROI CI upper bound < 0 **or** CLV < 0. Fail (keep): CI includes 0 and CLV > 0 — then cap override picks at ½u instead and re-measure at 80.
- **Do not act before 40 games.** It is 29 today.

### EXP-021 — NBA moneylines should be alert-only
- **Status:** proposed · **Rule:** `picks.qualifies()` per-league market-type block · **Prior:** NBA ML −14.6% [−27, −3], n=320, 8 months
- **Hypothesis:** the result holds in two independent halves of the season.
- **Test (run 10-07, per-month cut A, flat):** Oct −8.5, Nov −42.6, Dec +10.2, Jan −2.3, Feb −1.3, Mar −19.3, Apr +3.5, May −25.2. Six of eight months negative; the two positive months are the two smallest. First half (Oct–Jan, n=209) and second half (Feb–May, n=256) both negative. **Pass on existing data.**
- **Proposed change:** when NBA returns (late Oct 2026), publish NBA moneylines as alerts only; NBA totals/spreads unchanged. Needs Santhosh's go-ahead; one-line change, tagged so it can be reversed.

### EXP-022 — Score 90+ is not better than 80–89
- **Status:** proposed · **Rule:** `units` 80+ → 2u (and 3–4u with steps) · **Prior:** live 90+ 19-26, −21% [−46, +3], 39 games; v2's ≥99 band was the worst in both build windows
- **Hypothesis:** under v4, 90+ (hold 5%+ and wager ≥ $25K) does not outperform 80–89 out of sample; the size steps that concentrate on it should stop at the 80–89 size.
- **Test:** add a `conv` band split (70–79 / 80–89 / 90+) to `research/hist_cuts.py` output and run over football + NBA + MLB windows. Pass (cap): 90+ ≤ 80–89 in ROI *and* CLV in ≥ 3 sports with n ≥ 40 each. Fail: 90+ best in ≥ 2 sports.

### EXP-023 — Esports is the first sport worth adding as alert-only
- **Status:** proposed · **Rule:** `check.TAGS` (no esports tag today) · **Prior:** +5.9% [−2, +14], n=461, hit 65%, CLV +0.20
- **Test (run 10-07, per-month cut A):** 9 of the 11 months with ≥ 6 picks are positive; Sep 2025–Mar 2026 and Apr–Sep 2026 both positive; the two negative months (Jun −4.2, Jul −5.1) are small. CI still includes zero. **Passes the "two halves" check, not the CI check.**
- **Proposed change:** add esports to the scan as **alert-only** (no graded picks) so live CLV accrues; promote to graded after 50 alerts with CLV ≥ 0. Needs a kickoff-time check first: esports markets on gamma must carry `gameStartTime` (the MMA/golf failure mode).

### EXP-024 — Measure wallet discovery
- **Status:** proposed (instrumentation) · **Rule:** none yet · **Prior:** 015 unmeasured
- **Change:** stamp every signal and pick with `source: leaderboard | month | discovered`; add the cut to `research/review.py`. Zero risk; without it the discovery job cannot be judged.

### EXP-025 — Earlier than T-60
- **Status:** parked · **Prior:** research CLV ≈ 0 at T-60 in W1; live CLV +2.07 at the sharp's entry time
- **Needs:** check whether `research/lib/bt_rebuild.py` takes a snapshot offset; if so rerun one football month at T-180 and compare CLV. The gap between research (T-60) and live (entry time) CLV is the most interesting unexplained number in the project.

### Not proposed, on purpose
Lowering any gate (65/70/80), widening the price window, any new sizing
formula, re-adding the dog multiplier, consensus as a signal. All refuted
above; none has new evidence.

## Window evaluation 2026-10-07 — Sep 10 → Oct 6 (see `docs/reviews/2026-10-07-sep-oct-window.md`)

1,401 settled suggestions: −3.2% [−8, +2], signal CLV −2.02 pts. Published
picks +2.4% flat, −17% sized. Research rebuild of Sep: football −22%, MLB −17%.

- **EXP-020 bar met:** pick'em band 58-66, −12.1% [−26, +2] on 86 games; override picks 12-19. Recommendation: retire `price_min`.
- **EXP-022 strengthened:** 80–89 band −19.3% [−38, −1] on 842 v4 rows; 90+ second worst; sized picks 19u behind flat. Recommendation: flat 1u.
- **New finding — pick CLV is mostly the book's.** `pickclv.py` measured book price vs Polymarket close (+1.58). Backfilled the same 60 picks against the sharp's entry: **+0.03 pts, 52% beat** — the signal sits at the close and the +1.6 is the book spread. (Across *all* 1,391 suggestions the signal CLV is −2.02.) Both are now recorded (`clv`, `clv_entry`); KPI moved to the signal CLV.

### EXP-026 — Fresh bonus and stale penalty have the wrong sign; $10–25K wager tier over-scored
- **Status:** proposed · **Rule:** v4 points (`check.py`: fresh +10; `units.py`: stale −1u; wager $10K tier 35 pts)
- **Prior:** live fresh −6.9% [−12, −1] n=945 vs stale +4.5% n=456; $10–25K −17.2% [−29, −6] n=254 vs <$10K ≈ 0
- **Test:** rerun `hist_cuts.py` across the 99 research windows with (a) fresh bonus removed, (b) $10–25K tier at 20 pts; compare pick-set ROI and CLV to the frozen v4. Pass: both changes ≥ v4 on CLV in ≥ 3 sports with n ≥ 40 and not worse on ROI. Fail: either flips sign across sports.

### EXP-027 — Contested markets: when both sides qualify, neither is a play
- **Status:** proposed (design) · **Prior:** 18% of suggested markets had both sides shown; 39% of suggestions sat on one
- **Test:** descriptive first — grade the one-sided vs two-sided suggestions in the live window (`eval_window.py` cut to add). If two-sided rows are no better than one-sided, suppress them from picks and boards and mark "contested".

### EXP-028 — MLB alert-only
- **Status:** proposed · **Prior:** live MLB −13.4% [−24, −2] n=219; picks 3-9; research Sep −17.1%, 13-month MLB −4.1%
- **Test:** one more live month; pass if the live interval still excludes zero.

## Adopted 2026-10-07 (Santhosh: "implement this logic within big board and the sends to X and email")

| EXP | Change | Where |
|---|---|---|
| 020 | `price_min` overrides retired (Kch-Temp, BrotherObama) | `trusted.json` → `price_min_retired`; `form.price_window` unchanged |
| 022 | **s6 flat**: 1u every graded pick, ½u through a `price_max` override | `units.py` `FLAT`, `OVERRIDE_UNITS`, `SIZING_VERSION="s6"` |
| 027 | Contested rule: `opposed ≥ 1` → no pick, not emailed, board shows `contested` + `CONTESTED:n` | `picks.contested/qualifies`, `check.py --alert`, `bigboard.py` |
| — | Dual CLV: `clv_entry` (vs sharp's entry) beside `clv` (vs book) on every pick; KPI = signal CLV | `pickclv.py`, `picks.py --record` (results card line left for Santhosh) |

Not installed, on purpose: EXP-026 (fresh/stale, $10–25K tier — research windows first), EXP-028 (MLB alert-only — one more month), trader share cap (design only).

## Trader-override post-mortem 2026-10-07 (see `docs/reviews/2026-10-07-trader-overrides.md`)

`research/trader_persist.py` over 440,300 settled positions / 9,520 traders:
per-trader ROI rank correlation between chronological halves **−0.00**, in
every price band ≈ 0; traders "licensed" on half-1 ROI > +10% regress to the
pool in half 2 in all five bands; empirical-Bayes true-skill spread in the
window band = **zero**. Trader **CLV** persists (**+0.60**) but does not
convert to ROI at T-60 (+0.06). Prior hold predicts CLV only.

### EXP-029 — CLV license: urgency and price discipline by trader CLV tier, instead of per-trader price windows
- **Status:** **rejected 2026-10-14** (weekly review; result below) · **Rule:** replaces `price_min`/`price_max`; adds `clv_license.json` (rolling last-100 signal CLV, n ≥ 60, quartile tier)
- **Prior:** trader CLV half-to-half rank corr +0.60; top quartile +1.14 → +0.74 pts; bottom −1.10 → −0.90
- **Test:** on the 99 research windows, grade top-tier traders' in-window positions at **min(T-60 price, sharp's entry + 1 pt)** — i.e. only when a follower could have got within 1 pt of the entry — vs the pool at T-60. Pass: top-tier ROI interval excludes the pool mean in ≥ 3 sports with n ≥ 40 each, and bottom-tier in-window picks ≤ pool. Fail: no separation → tiers become an informational flag on the board only.
- **Decision rule for `price_max`:** retire regardless (section 1 of the review); it is the same kind of sample as `price_min`.

## Conviction score study 2026-10-07 (see `docs/reviews/2026-10-07-conviction-v5.md`)

`research/score_v5.py`, 37 → 36 research windows chronological split, 18,124
gated rows. No bet-time feature orders ROI out of sample; a fitted ROI model
fails on test (Q5 −2.6%); v4 flips. **EXP-022 strengthened:** v4 80–89 is
−12.5% [−23, −2] on the research test set and −19.3% [−38, −1] live; 70–79
is the best band in both. **New:** a line-move model (dominant feature: drift
toward the bet since entry) orders signal CLV monotonically out of sample
(Q1 −3.15 → Q5 +2.55) and ROI at T-60 (−7.3% → −1.3%); bottom 60% lose 6–7%
in every quintile. Saved as `research/linemove_v5.json`.

### EXP-030 — Line-move tier: exclude "moving away", order alerts by expected move
- **Status:** **running** — installed 2026-10-07 (Santhosh: "let's wire it in"): `linemove.py` + `linemove_v5.json`, `check.LINEMOVE`, `picks.LINEMOVE_GATE`, board columns Profile | Size | Move; `move`/`move_tier` logged on signals and picks; cuts added to `review.py` and `eval_window.py`. Decision at 300 settled signals (~2 weeks). · **Rule:** new column beside the score; bottom-60% tier = alert only at any price; top-40% first in the email. v4 kept as the gate; column renamed *Profile*.
- **Prior:** research test, any price: top-40 −1.6% [−6, +2] vs bottom-60 −6.1% [−9, −3]; CLV +2.2 vs −0.9. Inside the window CLV separates, ROI does not (−3.0 vs −3.2) and flips by sport.
- **Test (forward, live signals):** over the next 300 settled signals, "moving away" CLV < 0 and "moving toward" CLV > +1; pick-rule ROI of kept rows ≥ today's. Pass → the exclusion becomes a gate. Fail → informational only.
- **Explicitly not proposed:** any new ROI-fitted score (third failure, third dataset).

## Within-bettor sizing check 2026-10-13 (`research/sizing_within_bettor.py`, backtest window win3)

Santhosh: "one of the bigger issues… is not unit sizing properly. Kch-Temp had
Arsenal −1.5 for ~20k but Bills +3.5 for ~230k." Test: does a bettor's stake
RELATIVE TO HIS OWN MEDIAN predict his result, within the seven sports wallets
Polycopy lists (Kch-Temp, BreakTheBank, omnibus-076daa, joseph00677, 00gringo00,
corkmaster420blaezit, UpTheBlues)? Fresh window Sep 15 → Oct 13 (28 d, 24,684
settled events), positions rebuilt at T = kickoff − 60 m, graded flat 1u at the
sharp's entry + 1.5 pt vig, game-clustered CIs. 34,708 scored positions.

| Bettor | positions | <2x own median | ≥2x own median |
|---|---|---|---|
| UpTheBlues | 19,136 | −6.1% | −2.4% [−6, +1] |
| omnibus-076daa | 4,708 | −6.1% | +0.4% [−5, +6] |
| Kch-Temp | 32 | −31.9% (27) | +3.4% (5) [−69, +76] |
| BreakTheBank | 9 | — | — |
| joseph00677 / 00gringo00 / corkmaster | 3 / 0 / 2 | — | — |

Pooled Spearman(log vs_typical, flat P/L) = +0.08. Only two wallets have depth;
both show a weak tilt where oversized bets lose less, but the ≥2x bucket is
breakeven-to-negative with CIs through zero, and the lift comes from dropping
$10–$40 noise positions the scanner already excludes (MIN_STAKE). Inside the
bets we would actually see (2–5x / 5–15x / 15x+) UpTheBlues is −1.3 / −3.3 /
−3.6% and omnibus −0.4 / −1.9 / +5.0%: flat. omnibus's 15x+ bucket across three
windows: −13.7%, +8%, +5% — sign-flipping, i.e. noise. Kch-Temp's five ≥2x bets
are 5 bets. **Verdict: no evidence that scaling units to the bettor's relative
stake fixes ROI; consistent with the 09-21 and 10-07 results.** Side finding:
BreakTheBank's live signals have a 21-minute median lead and 13% are in-game,
so a T-60 backtest barely sees him (9 positions in 28 days); joseph00677 and
00gringo00 have 1 signal each all-time — they do not hold pre-kickoff positions
at the moments we sweep.

### EXP-031 — (not proposed) relative-stake unit ladder
- **Status:** not proposed; recorded so the question is not re-asked without new data.
- **What would change the answer:** a within-bettor ≥2x bucket with CI above zero on ≥200 bets for a bettor we can actually see pre-kickoff.

## Track-record depth + form gate 2026-10-14 (raybanman / LASK Linz post-mortem)

See `docs/alert-rules.md` § "Track-record depth + form gate". raybanman: wallet
age 35 days, 18 settled losses (−$3.0M), prior hold 159% on $990k, month −4.6%
on $12.6M, then $2.0M on LASK/anti-Liverpool, lost. Pool scan 2026-10-14: 323
gate-passers → 42 thin, 11 cold, 1 both; active removals listed in alert-rules.

Research-window check (`~/nl-research/windows`, 7,234 qualifying settled rows):

| cut | rows | ROI | 95% CI |
|---|---|---|---|
| prior hold 1.5–3% | 1,011 | −1.4% | [−8.0, +4.9] |
| prior hold 3–5% | 2,182 | −1.6% | [−5.7, +2.9] |
| prior hold 5–10% | 2,256 | −1.5% | [−5.6, +2.9] |
| prior hold 10–20% | 1,172 | −7.3% | [−13.7, −0.8] |
| prior hold 20–50% | 524 | −3.4% | [−14.8, +7.5] |
| prior hold > 30% (implausible) | 297 | −1.4% | [−14.8, +12.3] |
| prior vol < $1M | 67 | −7.2% | [−28.0, +15.7] |
| stake / prior vol ≥ 2% | 291 | +2.6% | wide |
| month P/L < 0 | 0 | — | untestable (pool = MONTH top-500) |

### EXP-032 — thin / cold wallet gate
- **Status:** **running** — installed 2026-10-14 on Santhosh's instruction; decision 2026-12-13 (60 days).
- Rule: `passes()` refuses `thin` (prior P/L < $250k or prior vol < $2M) and
  `cold` (month hold < −3% on ≥ $1M). Per-user MONTH lookup for slate wallets
  lacking a month row (`check.annotate_record`).
- Basis: data integrity + 10-07 persistence result; no ROI backtest supports or
  refutes it (see table). Installed on Santhosh's instruction.
- Measure: signals.jsonl / picks.jsonl rows now carry `thin`, `cold`,
  `prior_pnl`, `prior_vol`, `month_hold` via the hit dict; after 60 days compare
  ROI and CLV of would-have-qualified thin/cold signals vs the qualifying set.
  Revert if the removed set is not worse on CLV.

## Weekly review 2026-10-14 (see `docs/reviews/2026-10-14-weekly.md`)

**State.** 6 picks settled since 10-07: 5-1, +44.3% [−10, +78], signal CLV
+1.44. All time 71 graded, 37-34, +5.9% [−20, +37]. **KPI: 57 / 200 v4 picks,
signal CLV +0.50 ± 0.40 (90%), beat entry 36/57.** `review.py` KPI block fixed
(s5+s6, `clv_entry`). No adopted rule contradicted at ≥ 40 games. Trader
post-mortem (Cobalt Osprey) → EXP-032. Four picks unsettled, two stale.

- **EXP-030:** 289 / 300 settled signals — bar not met; ETA 2026-10-16. Prelim
  toward +25.0% [+7, +43] (65 games) · flat +5.9% [−14, +28] · away −16.7%
  [−30, −1] (54 games), at entry + vig. `research/linemove_live.py`.
- **EXP-028:** 13 live MLB picks (4-9, −45% [−86, −5]); one-more-month bar runs
  to 2026-11-07 (season ends ~11-01). Not acted on.
- **EXP-032:** running, n = 0 days / 60.
- **EXP-029 — Result (pre-registered test, `research/clv_license.py`,
  `results/exp029-clv-license-2026-10-14.md`).** 438,843 settled research rows;
  276,674 licensed (≥ 60 prior signals, rolling last-100 signal CLV); quartile
  cutoffs bottom ≤ −0.38, top ≥ +0.68. Pool = qualifying rows with T-60 price
  in −122..−180: 1,231 rows / 968 games, −2.1% [−7, +2].

  | tier | n / games | flat ROI [90%] | in-window signal CLV |
  |---|---:|---|---:|
  | top, follower within 1 pt of entry | 156 / 148 | +0.7% [−11, +12] | −0.37 |
  | bottom | 120 / 115 | +6.6% [−6, +19] | — |
  | unlicensed | 172 | +0.2% | — |

  No sport reaches 40 top-tier games (NBA 39, MLB 23, esports 30); the top tier
  does not separate from the pool overall, and the bottom tier is not worse.
  Esports bottom tier +25.9% [+8, +43] is the only interval excluding zero — in
  the wrong direction. **Decision: rejected.** A trader's rolling CLV persists
  (10-07) but does not license their in-window positions; CLV tiers stay an
  informational flag at most. `price_max` retirement stands on its own evidence.
- **EXP-034 — X-send gate: two sized sharps, uncontested (SHIPPED LIVE 2026-10-21).**
  Santhosh's rule: post a side when **>=2 sharps each with >3% hold hold more
  than 2x their own career median on it, and no qualifying sharp opposes it.**
  The veto half is EXP-027 unchanged; `check.mark_xgate` adds the consensus +
  size half, and `picks.qualifies` **stacks** it on `MIN_CONV` 70 at his
  direction. Graded at the sharp's own entry price (his stated assumption, and
  the optimistic one — a live follower fills worse, which is what `clv_entry`
  measures).

  | cut | n / games | flat ROI [90%] | signal CLV |
  |---|---:|---|---:|
  | 1 sharp, no veto (baseline) | 6,208 / 3,941 | −2.50% [−4.5, −0.6] | +0.09 |
  | 2 sharps + EXP-027 veto | 563 / 528 | −1.59% [−8.3, +5.6] | +0.22 |
  | + hold >3% only | 449 / 423 | +1.81% [−6.1, +9.8] | +0.18 |
  | + >2x career median only | 231 / 228 | +2.60% [−7.7, +12.5] | +0.24 |
  | **the rule** | 171 / 168 | **+8.78% [−3.2, +21.2]** | +0.11 |
  | **the rule, stacked on conv>=70 (shipped)** | 160 / 157 | **+11.29% [−1.0, +23.7]** | +0.06 |
  | today's gate alone, conv>=70 | 5,389 / 3,070 | −2.82% [−5.2, −0.5] | +0.07 |

  Robust: dropping the best month leaves +5.00%, dropping the single biggest
  winner +6.40%, leave-one-month-out spans +5.00%..+11.65% without flipping
  sign, 9 of 12 months positive (binomial p = 0.146), only 5 of 171 plays at
  entry < 0.25.

  **Against it, recorded before the forward test starts.** No interval excludes
  zero. Signal CLV *falls* when the two bars are applied (+0.22 -> +0.11) and
  splits +0.29 first half / −0.06 second half; ROI rising while CLV falls is the
  signature of variance, and CLV is the quantity that persisted on 10-07
  (+0.60) where ROI did not (−0.00). The 48-cell knob sweep produced **2** cells
  clearing zero against **4.8 expected by chance**, and is non-monotone in the
  middle (mult 3x below 2x at every hold level). An earlier version of this test
  omitted the $250k prior-P/L bar and reported −5.67%; 76% of the "sharps" in it
  were wallets with a median lifetime P/L of $82,410. That run is void.

  **Cost:** 160 of 5,389 conv>=70 plays, a ~97% cut in X volume — roughly 13
  posts a month. **Pre-registered:** judge on signal CLV over 150 posted picks;
  flip `check.XGATE = False` to revert. Scripts `research/xrule.py`,
  `research/knobs.py`.
- **New hypotheses:** none beyond EXP-032 (filed 10-14 pre-review).

## Rule freeze candidates 2026-10-22 (Santhosh)

### EXP-035 — v5 frozen-rule candidates
- **Status:** proposed, script ready, **not yet run** (`research/rules_v5.py`, needs the host corpus).
- **R1** consensus, no opposition: >=2 bettors with hold >2.5% and lifetime P/L >$100k each above 1x their career median on a side; no bettor clearing the same bars on the other side. Close to EXP-034 (live since 10-21) with lower bars and a symmetric veto. The EXP-034 test measured the symmetric veto *worse* than EXP-027's (+1.08% vs +8.78%), and a $100k P/L bar is what voided the first EXP-034 run, so the script also prints R1 with the EXP-027 veto and with the v4 gate required.
- **R2** trail hold >5% and lifetime P/L >$250k, with sized and no-opposition variants.
- **R3** league specialist and **R4** late size-up: proposed to Santhosh, not adopted.
- **Live shadow (2026-10-22, Santhosh: "continue with assumptions"):** `production/shadow.py` logs R1, R2, R2-sized and R4 on every pick-job board, beside a `live` benchmark, into the private `shadow.jsonl`; graded nightly by run-picks-results.sh. Nothing is posted and EXP-034 stays the published rule. Assumptions: ">2 bettors" = 2 or more; "winnings" = all-time Polymarket sports P/L; R2 tested both any-size and >1x median. R3 is research-only (live rows carry no per-league graded history). R1's $100k wallets are only seen if they are in the ~1,080-wallet pool.
- **Pre-registered:** a candidate is frozen only if its later-35% (out-of-sample) slice has signal CLV >= 0 and flat ROI at or above the EXP-034 benchmark on >= 100 plays; ten rows are tested at once, so the best in-sample row is expected to look good by chance.

## Consensus vs opposition cohorts 2026-10-27 (Santhosh)

### EXP-036 — fifteen cohorts x a 32-bar profile grid, split by kickoff day
- **Status:** proposed, script ready, **not yet run** (`research/cohorts.py`, needs the host corpus).
- **Ask:** "trail Kch-Temp and primm ... more conviction if multiple users are behind the same side with no opposing significant positions ... 3 sharps vs 1 insignificant, 2 vs 1 ... different hold, P/L thresholds ... look primarily at Sundays, Saturdays, Mondays, Thursdays, then the rest."
- **Sharp** = prior hold > H, prior P/L > P, stake > M x own career median; grid H {0, 1.5, 3, 5}% x P {0, 50k, 100k, 250k} x M {1, 2}. **Significant** opposition = a sharp on another side of the same market; **insignificant** = any other $2.5K+ wallet there.
- **Part 1:** 1..4+ sharps x {nothing / <=1 insignificant / insignificant only / significant opposed}. **Part 2:** C01–C15 (the asks as stated; the Kch-Temp/primm anchors; money ratio; prior-CLV roster; steam vs value price; independent vs herd arrivals; late money; league specialists; fading a bad-CLV wallet; a net conviction score), each swept over the grid and judged on the sweep median. **Part 3:** kickoff day, US Eastern, Sun/Sat/Mon/Thu/rest.
- **Grading:** flat at the T-60 price + vig (what a follower gets) and, for reference only, at the sharp's entry. CLV = close − T-60 price.
- **Pre-registered:** ~900 cells are measured, so ~5% of the large ones clear zero by luck (Part 4 prints the count against that). A cohort is a candidate only if its later-35% slice has CLV >= 0 and follow ROI at or above the EXP-034 benchmark on >= 100 plays, and its sweep median is above zero. A day-of-week edge must hold in the held-out slice too; it is not a reason to change the rule on its own.
- **Result 2026-10-27 (host corpus, 445,190 rows, 49,743 pregame markets; report `~/nl-research/exp036-cohorts.md`):** **no cohort clears the bar.** Only 3 cells with >= 100 plays have a follow-ROI 90% interval above zero, against ~27 expected by chance, and all 3 are C08.
  - **C08 (2+ sharps, the T-60 price already moved >= 2 pts their way):** +29.0% [+13, +46] on 95 plays. Held out: +47.4% on 32 plays, CLV +0.45. The sweep median is +5.2%, and 24 of 29 cells are positive. It is the only lead worth following, and it fails the bar on held-out n alone.
  - **C04 (Kch-Temp/primm, no sig opposition):** +4.4% [−13, +22] on 113 plays, CLV +0.39, held out +3.3% on 19 plays.
  - **C05 (Kch-Temp/primm plus one other sharp):** +9.1% on 37 plays.
  - **C06 (money ratio):** the sweep median is +5.2%, but it fades to about 0 held out.
  - **The asks as stated:** C01 (2+ vs <=1 insig) is −9.3% on 36 plays, and C02 (3+) has 3 plays. Almost every market has 2 or more other $2.5K wallets against the side.
  - **Significant opposition** costs −8.4% at 2+ sharps and −9.5% at 3+, so the veto half of the idea is supported.
  - **C14 (fading bad-CLV wallets):** −48.6% held out. Rejected.
  - **Day of week:** no day separates from the baseline (−2.2% to −3.7%).
  - **C10–C12 are not testable on this corpus,** because bt_rebuild stamps every row t_ts = kickoff − 60m. They need live-ledger entry times.
- **Next:** shadow C08 and C04/C05 live, beside EXP-034, until the held-out n reaches 100.
