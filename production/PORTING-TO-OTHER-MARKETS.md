# Porting the sharp-money method to other markets

*Written 2026-09-15. Companion to `nightly-line-sharp-money-system.md`, which
documents the sports build itself. This one is about what transfers, what
doesn't, and how to stand the same thing up somewhere else.*

---

## 1. What the method actually is

Strip away the sports and it's four steps:

1. **Identify a population of traders with a measurable track record.**
   Polymarket is on-chain, so wallets are public and the leaderboard exposes
   lifetime P/L and volume per address. Hold rate (P/L ÷ volume) is the quality
   metric; absolute P/L is a scale metric and a poor proxy for skill.
2. **Read their current open positions.** `data-api.polymarket.com/positions`.
3. **Score the positions** on trader quality, size relative to that trader's own
   norm, price taken, and structure (stacked across markets, underdog, fresh).
4. **Measure whether the scoring predicts anything** — via outcomes and, far more
   usefully, closing-line value.

Steps 1–3 are cheap. **Step 4 is the whole thing.** Without it you have a
plausible-looking dashboard that may be worth nothing, and no way to tell.

---

## 2. The precondition that makes or breaks a port

**Turnover.** Sports works because a signal logged at 2pm is settled by 11pm.
That produces dozens of scored outcomes a day, so the model can be evaluated in
weeks rather than years.

Measured on Polymarket, 2026-09-15 — markets resolving within 24h, with 24h
volume:

| Category | <24h markets | 24h volume | Verdict |
|---|---|---|---|
| **Crypto (BTC/ETH dailies)** | **1,443** | **$2.17M** | **Best analogue to sports** |
| Weather | 104 | $579K | Viable, thinner |
| Pop-culture (Elon tweets) | 12 | $590K | Weekly, few markets |
| Finance / indices | 61 | $239K | Thin |
| Politics | 1 | $19.2M | One-off events, not a stream |
| Economics | **0** | $0 | **No fast markets at all** |

Economics having zero is why `catalyst.py` reaches out 45 days and measures price
drift instead of outcomes — it cannot wait for CPI to print. That is the correct
adaptation, and the template for any slow market.

**Rule of thumb:** if a market class produces fewer than ~20 settled signals a
week, do not build an outcome-based model for it. Build a drift-based one.

---

## 3. What transfers unchanged

- **Pool construction from two leaderboard slices** — all-time (proven) plus
  last-30-days (current form, catches sharps with no lifetime ranking yet).
  Ranking by *volume* selects market makers by construction; never do it.
- **Hold rate over a volume floor** as the quality bar. A 19% hold on $400K of
  volume is a coin-flip winner; 4% on $100M is not.
- **Size relative to the trader's own median position.** This repeatedly surfaced
  signal that raw dollars missed — a wallet at 40x its normal size on $22K
  matters more than a routine $51K.
- **Two-sided exclusion.** A wallet holding both sides of one market is running a
  book, not expressing a view. This was the single highest-value bug fix.
- **Counting failures.** Every report prints `N/M wallets`. Silent partial data
  looks exactly like "no signal" and will fool you — it did, three times.
- **CLV as the primary health metric.** Converges roughly an order of magnitude
  faster than win rate.

## 4. What must be re-derived per market class

| Thing | Why it can't carry over |
|---|---|
| **Which leaderboard** | Category-specific. A sports sharp's Fed bet is not signal. `macro.py` already does this. |
| **"Fresh"** | Sports end at kickoff, so entry≈current implied recent. A 6-month market can sit at its entry price by coincidence — you must pull trade history and measure dollars bought recently. |
| **Liquidity floors** | Fed decisions run $12.9M; other econ releases $5–30K. A fixed floor either admits noise or excludes everything. |
| **Stack / correlation** | "Same side across markets" is obvious in sports (spread + ML). In neg-risk multi-outcome events, YES on two outcomes is a *hedge*, not a stack. |
| **Underdog** | Entry < 0.45 is meaningful when two outcomes split the book. In a 12-outcome event, every outcome is an underdog. |
| **Settlement lookup** | Sports settle to one winner at ~1.0. Scalar and multi-strike markets need different resolution logic. |

---

## 5. Worked example — porting to crypto dailies

The closest thing to a drop-in port, because the turnover matches.

**Slate.** `tag_slug=bitcoin`, `tag_slug=ethereum`, `tag_slug=crypto`, filtered to
`endDate` within 24–36h. Expect 400–1,400 markets; most are strike ladders on the
same underlying.

**The trap.** "Bitcoin above 112K / 113K / 114K on Sept 15" are *not* independent
markets — they're one view expressed at different strikes. A wallet holding five
of them is one position, not five signals. This is the same correlation problem as
the sports stack, and it must be collapsed before scoring or every count inflates
by ~5x.

**Pool.** `category=CRYPTO`, all-time + month. `macro.py` already sets
`POOL["CRYPTO"]=500`, `MIN_PNL["CRYPTO"]=250_000`.

**Scoring changes.** Drop the underdog multiplier — a far-OTM strike is "a dog"
mechanically, not by conviction. Replace it with **distance from spot**: a large
position on a strike near spot is a real directional call; one 8% away is a
lottery ticket. Keep hold, size-vs-typical, and freshness.

**Measurement.** Outcomes settle daily, so use the sports approach directly —
`resolve.py` and `clv.py` work with a different settlement parser. Expect a
usable sample in ~2 weeks rather than ~2 months.

**Honest caveat:** crypto dailies may be dominated by market makers and
delta-hedgers rather than opinionated traders. Check the hold distribution of
whoever actually shows up *before* building the alerting layer. If the top
holders are all sub-1% hold on enormous volume, there is no signal to find.

---

## 6. What already exists here

| File | Covers | Horizon |
|---|---|---|
| `check.py` / `bigboard.py` | Sports | hours |
| `macro.py` | ECONOMICS, CRYPTO, FINANCE, POLITICS | up to 120d |
| `catalyst.py` | US economic releases, 15-min diffs | 45d |
| `catalyst-drift.py` | price drift after alerts (CLV stand-in) | — |
| `dayledger.py` | daily settled ledger + AI post-mortem | sports |

**The gap is the fast lane.** `macro.py` treats crypto as long-dated; nothing
targets the daily BTC/ETH markets where turnover actually matches sports. That is
the highest-value next build, and it should reuse `check.py`'s shape rather than
`macro.py`'s.

---

## 7. Findings that should inform any port

Measured on the sports ledger, all provisional at current sample size:

- **CLV said underdog entries were worth +3.5 pts over 8 signals.** One day of
  real outcomes then had them 2-for-13, −55.8%. Both numbers are too small to
  act on. This is the central lesson: *do not tune on a day.*
- **Stacked positions measured +0.6 pts CLV** — much weaker than expected, which
  is why the stack multiplier was cut from 1.35 to 1.15.
- **Most losses were "bad read", not variance** — on 2026-09-14, 17 of 24 losses
  had the market moving away before settlement. A filter on pre-game drift may
  be worth more than any trait weighting.
- **Correlated signals wildly overstate sample size.** 47 signals across 20
  games; seven losses came from one final score.

---

## 8. Build order for a new market class

1. **Measure turnover and liquidity first.** If nothing resolves fast, plan for a
   drift-based model and say so up front.
2. **Check the hold distribution of who actually holds positions there.** No
   high-hold wallets means no signal, and no amount of scoring fixes that.
3. **Collapse correlated instruments** (strike ladders, multi-outcome events)
   before anything else.
4. **Build the ledger and the measurement layer before the alerting layer.**
   Alerts on an unvalidated score are just noise with a notification sound.
5. **Only then** tune weights, and only on a sample that can support it.

The sports build did steps 4 and 5 backwards — alerts existed for three days
before anything was logged, so those three days are unmeasurable. Don't repeat it.
