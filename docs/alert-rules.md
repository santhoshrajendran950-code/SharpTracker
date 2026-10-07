# What gets alerted, and why — the rule record

Every threshold that decides whether a Polymarket position becomes an email
alert, a board row, or a graded public pick, with the evidence it was set on
and the date. This is the file to change when a rule changes; the experiment
that justifies the change goes in [`experiments/LEDGER.md`](experiments/LEDGER.md).

Numbers are as of **2026-10-07**. Constants live in `production/check.py`,
`production/units.py`, `production/picks.py`, `production/form.py`.

## 0. The pipeline in one line

wallet pool → open pre-game positions → `passes()` gate → conviction **v4**
score → **alert ≥ 65** (email, 15 min) → **pick ≥ 70 inside −122..−180**
(X/Discord, 10 min, pre-kickoff) → **core ≥ 80** → size **s5** → settle
nightly → CLV vs the Polymarket close.

## 1. Who is watched — the wallet pool (`check.build_pool`)

| Source | Rule | Why |
|---|---|---|
| Sports leaderboard, lifetime P/L | top **690** | widest slice the data-api serves without 429s at concurrency 24 (re-benchmarked 2026-09-13) |
| Sports leaderboard, this month | top **500** | catches in-form wallets the lifetime list misses |
| `discovered.json` | gate-clearing wallets found by `holders.py --discover` (daily 11:30, since 2026-09-23) | market-first sweep of today's slate finds size from wallets neither slice ranks. **Unmeasured** — no cut yet of discovered-wallet picks vs leaderboard picks (EXP-024). |

## 2. Which positions count — `check.passes()`

| Gate | Value | Evidence |
|---|---|---|
| hold (P/L ÷ volume) | ≥ **1.5%** | market makers sit under 1% (the 2026-09-10 Rybakina "win" came from a 0.38%-hold wallet on a coin-flip market — not a signal). Tighter gates (3%, 5%) raise CLV but not ROI across 99 research windows (EXP-017). |
| lifetime P/L | ≥ **$250K** | scale, not skill; a floor so the sample is not dust |
| stake | ≥ **$2.5K** | below this the position is noise for a $5M-volume wallet |
| not decided | — | pre-game only; in-game picks were published twice under v2 and removed |
| not two-sided | — | a wallet holding both sides is a bookmaker's book, not a view |
| hedge material | smaller side ≥ 0.25 × larger | a token opposite leg is not a hedge |

## 3. The score — conviction v4 (`check.py`, `MODEL_VERSION = "v4"`, since 2026-09-23)

Bettor only. **No price term.** Range 20–100.

| Component | Points |
|---|---|
| hold 5%+ / 3–5 / 2–3 / <2 | 45 / 40 / 25 / 10 |
| wager $100K+ / $25K / $10K / $5K / <$5K | 45 / 40 / 35 / 20 / 10 |
| fresh (entry within 10% of current price) | +10 |

**Why bettor-only:** inside the favourites window (§4) the profile score
orders ROI in both build windows (≥ 80: +18.5% n=45, +28.1% n=17). Outside it,
no profile feature orders anything; each flips sign between windows.
Santhosh, 2026-09-23: "price should just be a filter… the score should be
based on user profile characteristics."

**Rejected on the way here:** v1 (geometric mean × stack × dog), v2 (×1.45
dog multiplier — anti-monotone, top quintile −21%/−15%), v3/v3.1 (price term
50 + 175·(p−0.5) — superseded same day), fitted logistic and boosted-stump
scores (inverted out of sample). See EXP-002, EXP-008, EXP-009.

**Gates** (rescored 2026-09-23 after "those are arbitrary values"):

| Gate | Score | Evidence (v3 bands, W1 / W2 ROI) |
|---|---|---|
| email alert, board minimum, size floor | **65** | 60–69 was −1.6 / −3.9: informational only |
| graded pick | **70** | 70–79: +3.2 / +3.7, the band where ROI crosses zero in both |
| core | **80** | 80–89: +6.4 / +23.7 |

**Live warning (2026-10-07):** the 90+ band is the worst live band (19-26,
−21% [−46, +3], 39 games) while 70–79 is 8-2. Same shape as v2's ≥99 band in
both build windows. Open as EXP-022; not acted on (CI includes 0).

## 4. Price is a filter — `picks.qualifies()`

A graded pick needs **both** the sharp's entry and the book price inside
**−122..−180** (probability 0.55–0.643). Also: no period markets (1H, 1Q,
innings), pre-kickoff only, at most 4 non-core picks a day, and (s5.1,
2026-09-28) a bet under 10% of the trader's own median is an alert, not a pick.

Evidence, both build windows: underdogs −20% / −17%; favourites −0.4% / +10%;
score ≥ 80 inside the window +18.5% / +28.1%, negative outside in both.
Live, 2026-10-07: default-window picks 20-14, +28% [−15, +84] on 29 games;
−122..−150 band 10-3 with 13/13 beating the close.

Rejected: −150 cap (2026-09-23, one day), +150..−180 "favourites and
pick'em" window (no feature orders it).

## 5. Exceptions for people — `form.price_window()` and `trusted.json`

| Trader | Override | Set from | Status |
|---|---|---|---|
| Kch-Temp | `price_min` 0.50 — pick'em allowed | his own 32 settled signals: pick'em..−122 was 17-9 (+29%) | **contradicted live**: pick'em band now 10-19, −33% [−60, −6] on 29 games; all override picks 12-19, −26% [−54, +1]. EXP-020. |
| justaluckydude | `price_max` 0.90 — favourites to ~−900 | 15-0 across every favourite band | too few live picks to judge |
| CandleHammerDrums | `price_max` 0.90 | 12-2 on favourites, 0-2 on dogs | too few live picks to judge |
| Soarin22, GrizzliesSuck | declined | 6 and 10 signals — too thin | — |

Every override pick is stamped `price_override: true` so it is judged apart.
These are bets on people, not validated rules; the sample they were set from
is the same sample that seeded `trusted.json`.

Ledger form (`form.py`): a trader with ≥ **20** graded games at ROI ≤ **−15%**
is cut; ≥ +15% gets a size bump. Nobody has crossed the cold line yet.

## 6. Size — s5 (`units.py`, `SIZING_VERSION = "s5"`, 2026-09-23; s5.1 2026-09-28)

| Step | Rule |
|---|---|
| base from score band | 65–69 ½u · 70–79 1u · 80+ 2u |
| NFL | × 1.5 |
| trusted name | + 1u |
| totals | × 0.75 |
| stale (entry drifted) | − 1u |
| consensus ≥ 3 sharps | + 1 step |
| long dog (+200 or longer) | × 0.6 |
| tiny for this trader (< 25% of own median) | one rung down |
| snap | {½, 1, 2, 3, 4}, cap 4u; published sizes are locked |

Evidence: beat flat on the pick set cross-window (+10.8 vs +8.2; +22.0 vs
+16.0) with lower drawdown. **Contradicted** in the seven out-of-sample
football months, where s5 trailed flat in every month (e.g. Nov −11.1% vs
−4.2%). Sizing cannot create edge (EXP-003, 007); s5's claim is only a
shallower drawdown. Live: sized −12% vs flat −1.5% on v4/s5 picks.

## 7. Cadence (launchd, `production/launchd/`)

| Job | When | What |
|---|---|---|
| alert | every 15 min | new plays ≥ 65 → email (Santhosh + 2 BCC) |
| picks | every 10 min | graded picks → X + Discord, pre-kickoff only |
| picksresults | nightly | W/L, record, CLV card |
| xboard00–17, xboardmlb | kickoff waves | boards |
| discover | 11:30 | wallet discovery |
| settle, dayledger, linearchive, windows | nightly / hourly | grading, ledgers, line archive |
| weeksheet | Mon 08:00 | private weekly ledger to Drive |
| catalyst | every 15 min | non-sports: economic releases (not graded) |
| watchdog | every 15 min | restarts stalled jobs |

## 8. The KPI, and where it stands

**CLV ≥ 0 over 200 graded v4/s5 picks** before anything is charged for.

| 2026-10-07 | |
|---|---|
| v4/s5 picks graded | 51 / 200 |
| mean CLV | +2.07 pts (± 0.88 at 90%), 38/51 beat the close |
| flat ROI, v4/s5 | −1.5% [−27, +22] on 45 games |
| sized ROI, v4/s5 | −12% |
| all-time ledger | 32-33, −26.9u at published sizes |

`research/review.py` regenerates every cut in this file from the live ledger.

## 9. Standing priors — refuted ideas that should stay refuted

- Underdogs lose (both windows, 7 football months, 99 sport-months).
- Consensus == 2 is the worst consensus bucket (three samples). Live: 7-8, 3+ is 0-3.
- Size-vs-typical has no dose-response; the 5–15× band was the worst.
- No fitted score (logistic, stumps, Kelly calibration) survived out of sample.
- The 14-day replay (20-7, +29.9%) is in-sample and is not a record.
- Crypto dailies: no persistent-skill crowd; port failed its gate 2026-09-15.
- Kalshi: no public trader identity; nothing to port.
