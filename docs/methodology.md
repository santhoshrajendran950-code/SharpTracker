# Methodology

## 1. The signal

`production/check.py` builds a pool of wallets from Polymarket's sports
leaderboard (top-690 lifetime P/L, top-500 this month) plus `discovered.json`
(wallets the daily market-first sweep in `holders.py` found holding size that
neither slice ranks). For every open pre-game position it records the wallet's
career hold and P/L, the stake, the entry price, the current price, and
whether the wallet's other legs on the same game agree (`stack`), disagree
(`hedged`) or straddle the market (`two_sided` — a bookmaker's book, excluded).
`mark_consensus` counts distinct qualifying wallets leaning the same way.

A row **qualifies** (`check.passes`) if hold ≥ 1.5%, lifetime P/L ≥ $250K,
stake ≥ $2.5K, not decided, not two-sided, entry in range, and score ≥ gate.

## 2. Conviction — versions

| Version | What it was | Why it changed |
|---|---|---|
| v1 (pre-09-14) | geometric mean of hold / P/L / stake sub-scores × stack 1.35 × dog 1.20 | first cut |
| v2 (09-14) | stack 1.15 × **dog 1.45** × fresh 1.10 | reweighted on 31 CLV observations |
| v3 (09-23) | `50 + 175·(price − 0.5)` + 10 hold≥3% + 10 wager≥$10K | v2 was **anti-monotone** in both windows: its top quintile returned −21% and −15%, because the ×1.45 dog multiplier put every big underdog bet at 100, and underdogs lose |
| v3.1 | price term saturates at −180 | so the pick window spans the full scale |
| **v4 (live)** | **bettor only**: hold pts (5%+ 45 · 3–5 40 · 2–3 25 · <2 10) + wager pts ($100K+ 45 · $25K 40 · $10K 35 · $5K 20 · <5K 10) + fresh 10. Range 20–100. **No price term.** | The operator's call: "price should just be a filter… the score should be based on user profile characteristics." Tested: inside the favourites window the profile score orders ROI in both build windows (≥80: +18.5% / +28.1%); outside it, no profile feature orders anything — every one flips sign between windows. |

`check.conviction_v2` is kept for side-by-side reads. Every logged signal is
stamped with `model` so versions can be compared on the same games.

## 3. Price is a filter (`production/picks.py`)

Graded picks require the sharp's entry **and** the book price both inside
**−122..−180** (entry probability 0.55–0.643). Both build windows: underdogs
−20% / −17%; favourites −0.4% / +10%; conviction ≥ 80 inside the window +18.5%
/ +28.1%, outside it negative in both. Period markets (1H, 1Q, first inning)
never publish — the settler grades full-game results only. Picks are
pre-kickoff only (`run-picks.sh --pre`).

Per-trader exceptions live in `trusted.json` and are applied by
`form.price_window()`: a `price_min` widens the dog-ward bound (Kch-Temp, to
pick'em), a `price_max` widens the favourite-ward bound (justaluckydude,
CandleHammerDrums, to −300). Each was set from that trader's own settled
record split by price band, is capped where the operator wants it, and every
pick that publishes through one is tagged `price_override: true` so its CLV
can be judged apart. These are bets on people, not validated rules.

## 4. Unit sizing — versions (`production/units.py`)

| Version | Rule | Verdict |
|---|---|---|
| s1 | ladder on size-vs-typical (the trader's bet against their own median) | no dose-response in either window; the 5–15× band was the worst bucket (−22%, CI < 0) |
| s2 | + consensus steps, trusted names | consensus == 2 is the worst consensus bucket in three samples |
| s3 | + the operator's sheet: NFL ×2.3, totals ×0.68, long dogs ×0.56 | describes his sizing; does not show it wins |
| s4 | core/rest tiers, +150 gate, 4 rest picks/day | 37% less risked, shallowest drawdown — loses less by betting less |
| **s5 (live)** | **score bands**: v4 65–69 → ½u, 70–79 → 1u, 80+ → 2u; then NFL ×1.5, trusted +1, totals ×0.75, stale −1; snap to {½,1,2,3,4} | beat flat cross-window on the pick set (+10.8% vs +8.2%; +22.0% vs +16.0%) with lower drawdown. A fitted Kelly calibration flipped between "all ½u" and "1–3u" depending on the fit week — not shipped. |

A published size is **locked** (`picks.locked_units`): later boards repeat it
rather than re-deriving it.

## 5. Grading

Every backtest row is graded at a **bettable** price: the T-60 price (or, in
the live ledger, the sharp's entry) **plus 1.5 pts of vig**. Intervals are
bootstrapped by resampling **games**, never legs — 47 legs across 20 games is
20 outcomes. "Nothing survives" is a legitimate output and the scripts say so.

## 6. Out-of-sample discipline (`research/`)

Each historical month **discovers its own sharps** from that month's trades
(`hist_discover.py`), then rebuilds each wallet's record from the 90 days
*before* the window (`hist_prior.py`). Today's leaderboard cannot be read as of
a past date, and reading it today selects wallets on what they did after the
window — most of today's pool did not exist a year ago. The historical gate
(hold ≥ 1.5%, 90-day realised P/L ≥ $50K, vol ≥ $500K) is therefore not the
production gate; `hist_cuts.py` reports five gate variants side by side.

Rows carry `sport` and `market_type` (moneyline / spread / total / draw).
Outright and futures markets are excluded by construction — the classifiers in
`research/lib/sport_config.py` only match head-to-head questions.

## 7. Publishing

- **Email** (`alert.sh`, every ~17 min): everything new scoring ≥ 65.
- **X + Discord picks** (`run-picks.sh`, every 10 min): graded picks only, with a
  rendered card (`xcard.py`), caption = play + best price + game time.
- **X + Discord alerts** (`alertpost.py`): everything the email sends that is
  *not* a pick, marked "not graded, no size."
- **Boards** (`xcard.py`, `postplays.py`): a `Size` column distinguishes graded
  units from `alert`.
- **Nightly results** (`run-picks-results.sh`): W/L per pick, record to date,
  and **CLV** (`pickclv.py`) against the Polymarket close.
