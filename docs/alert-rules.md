# What gets alerted, and why — the rule record

Every threshold that decides whether a Polymarket position becomes an email
alert, a board row, or a graded public pick, with the evidence it was set on
and the date. This is the file to change when a rule changes; the experiment
that justifies the change goes in [`experiments/LEDGER.md`](experiments/LEDGER.md).

Numbers are as of **2026-10-07** (rules changed the same day — see the ledger's 2026-10-07 adoption entry). Constants live in `production/check.py`,
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

## 3. The score — conviction v4, shown as **Profile** (`check.py`, `MODEL_VERSION = "v4"`, since 2026-09-23)

Bettor only. **No price term.** Range 20–100. **It is a gate, not a ranking**
(renamed on the board 2026-10-07): across 99 research sport-months no v4
input orders ROI out of sample, and the 80–89 band is negative with an
interval excluding zero in both the research test set and the live window
(`docs/reviews/2026-10-07-conviction-v5.md`). It still decides what is shown
(≥ 65) and what may be a pick (≥ 70); it no longer sizes anything.

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

## 3a. Line-move tier — `linemove.py` (EXP-030, installed 2026-10-07)

Every row gets a predicted close-minus-entry in pts from `linemove_v5.json`
(ridge on signal CLV over 99 research sport-months; the only material input
is how far the line has already moved toward the bet since the sharp
entered, +2.8 pts per sd). Tiers by research quintile: **toward** (top 40%),
**flat** (Q3), **away** (bottom 40%).

| Tier | Board | Email | Pick |
|---|---|---|---|
| toward | `Move` column, e.g. `toward +2.1` | listed first, tagged | eligible |
| flat / away | `Size` shows `alert` | listed after, tagged "alert only" | **never** (`picks.LINEMOVE_GATE`) |

Evidence (out of sample, test windows ≥ 2026-04): quintiles ran −3.15 →
+2.55 pts CLV and −7.3% → −1.3% ROI at T-60; the bottom 60% lost 6–7% in
every quintile; the top 40% sit at the closing price minus vig. Inside the
window the CLV separation holds but ROI does not — so this is an exclusion
rule, not a selection edge. **Forward test pre-registered:** over the next
300 live signals, "away" CLV < 0 and "toward" CLV > +1, and the pick-rule ROI
of kept rows ≥ before. Fail → `LINEMOVE_GATE = False`, column stays.
`move`/`move_tier` are logged on every signal and pick.

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

## 4a. Contested markets — no play (2026-10-07, EXP-027)

If another qualifying sharp is on the **other side** of the same lean in the
same game (`opposed ≥ 1` from `check.mark_consensus`), the row is not a pick,
is not emailed, and the board shows `contested` in the Size column with a
`CONTESTED:n` flag. Evidence: in the Sep–Oct window 18% of suggested markets
had both sides shown and 39% of suggestions sat on one. Descriptive rule, same
logic `passes()` already applies to one wallet's two-sided book; its effect is
measured by the `contested` cut in `research/eval_window.py`.

## 5. Exceptions for people — `form.price_window()` and `trusted.json`

| Trader | Override | Set from | Status |
|---|---|---|---|
| Kch-Temp, BrotherObama | `price_min` 0.50 — pick'em allowed | own settled signals in the pick'em band | **RETIRED 2026-10-07** (EXP-020): pick'em band 58-66, −12.1% [−26, +2] on 86 games; override picks 12-19. Kept in `trusted.json` as `price_min_retired`. |
| justaluckydude | `price_max` 0.75 — favourites to −300 | 15-0 across every favourite band | kept at **½u** (s6 `OVERRIDE_UNITS`) until 40 games; in-window 15-4 live but −8 pts signal CLV |
| CandleHammerDrums | `price_max` 0.75 | 12-2 on favourites, 0-2 on dogs | same |
| Soarin22, GrizzliesSuck | declined | 6 and 10 signals — too thin | — |

Every override pick is stamped `price_override: true` so it is judged apart.
These are bets on people, not validated rules; the sample they were set from
is the same sample that seeded `trusted.json`.

Ledger form (`form.py`): a trader with ≥ **20** graded games at ROI ≤ **−15%**
is cut; ≥ +15% gets a size bump. Nobody has crossed the cold line yet.

## 6. Size — s6 FLAT (`units.py`, `SIZING_VERSION = "s6"`, `FLAT = True`, 2026-10-07)

**Every graded pick is 1u.** A pick that publishes through a `price_max`
override is ½u. Core/rest tiering (80+) survives only for the 4-rest-picks-a-day
cap. Evidence (EXP-022): sized trailed flat on the live ledger (−17% vs +2.4%,
19u), in all seven out-of-sample football months, and in the Sep 2026 rebuild
(−27.3% vs −22.0%); the 90+ band took the largest units and went 19-26 while
70–79 went 8-2 at the smallest. Five sizing versions never beat flat out of
sample. `FLAT = False` replays s5 for backtests.

### s5, for the record (2026-09-23 → 2026-10-07)

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
| mean CLV vs **book** | +2.07 pts (± 0.88 at 90%), 38/51 beat the close |
| mean CLV vs **sharp's entry** (signal) | +0.03 pts over all 60 priced picks, 52% beat — the signal sits at the close; the +1.6 is the book spread |
| flat ROI, v4/s5 | −1.5% [−27, +22] on 45 games |
| sized ROI, v4/s5 | −12% |
| all-time ledger | 32-33, −26.9u at published sizes |

Both CLVs are stored on every pick (`clv`, `clv_entry`) and printed by
`picks.py --record`, `pickclv.py` and the nightly card. **The KPI is now the
signal CLV** (`clv_entry` ≥ 0 over 200 picks); the book CLV is reported beside
it as the line-shopping edge. `research/review.py` regenerates every cut in
this file from the live ledger.

## 9. Standing priors — refuted ideas that should stay refuted

- Underdogs lose (both windows, 7 football months, 99 sport-months).
- Consensus == 2 is the worst consensus bucket (three samples). Live: 7-8, 3+ is 0-3.
- Size-vs-typical has no dose-response; the 5–15× band was the worst.
- No fitted score (logistic, stumps, Kelly calibration) survived out of sample.
- The 14-day replay (20-7, +29.9%) is in-sample and is not a record.
- Crypto dailies: no persistent-skill crowd; port failed its gate 2026-09-15.
- Kalshi: no public trader identity; nothing to port.

## Paper execution (2026-10-12)

Every published pick is also paper-traded on Novig's play-money exchange
(`production/place.py`, `com.nightlyline.place`): 1u = $10, cap $50/order and
$200/day, the pick's odds are the worst price accepted (+0.005), take if the
book is inside that else rest as a maker until kickoff. Graded nightly from
Novig's position history. This measures fill rate and realised price against
the graded price; it changes no alert rule. See `docs/exchange-apis.md`.

## Ops: data-api rate limit, measured (2026-10-13)

Boards came back `!! 22 FAILED` / `!! 31 FAILED` on 2026-10-13. Probing
`data-api.polymarket.com/positions` under the sweep lock:

| test | result |
|---|---|
| 150 cold requests, 24 threads | 150 × 200 |
| next 150, 24 threads | 49 × 200, 101 × 429 |
| next 150, **8** threads | 150 × 429 |
| 20 s idle, then 300 | 200 × 200, 100 × 429 |
| fixed 10 / 15 / 20 / 30 req/s for 20 s | 10.1 / 15.3 / 16.9 / 16.8 ok/s sustained |

So it is a per-client token bucket of ~200 requests refilling at ~17/s, not a
concurrency limit. Our sweeps fired ~90 req/s and lived on five exponential
retries (~12 s); that worked only while nothing else had drained the bucket.
`catalyst.py` (300 wallets, no lock, every 15 min) finishing a minute before a
board was the trigger for the 22/31 failures; a board taking the lock the
instant `windows.py` released it cost 14 more.

Fix (`production/pace.py`): process-wide bucket of 150 tokens at 15/s for
data-api, 20-token opening burst, zeroed on any 429; `check.get` and
`macro.get` call it before every attempt. `catalyst.py` and `macro.py` now hold
`check.sweep_lock` like the other sweepers. Header shows the breakdown:
`N FAILED (x throttled, y unreachable; z 429s)`. Verified: 1,090/1,090 wallets,
73 s alone; 1,090/1,090 starting the second a picks board finished. If a new
competition or pool makes the sweep too slow, raise `RATE` only after re-running
the fixed-rate probe — 17/s was the ceiling on 2026-10-13.

## Track-record depth + form gate (2026-10-14, Santhosh: "let's make that a rule moving forward")

**Trigger.** raybanman (rank #131, 7.3% lifetime hold, $991k) put ~$2.0M on
LASK Linz vs Liverpool — $845k on "Liverpool win: No" at 0.33, $557k LASK +1.5,
$42k LASK to win — ten times his own median. Liverpool won. The wallet's first
trade was 2026-09-09: 35 days old, 18 settled losses totalling −$3.0M, $5.6M
redeemed on wins. Its "prior" P/L (lifetime minus last 30 days) was $1.6M on
$990k of volume — a 159% hold that no trader sustains — and it was −4.6% on
$12.6M in the 30 days before the match. Both lifetime bars (hold ≥ 1.5%, P/L ≥
$250k) were cleared on one hot fortnight. The match was contested (BreakTheBank
on Liverpool), so no pick was at risk, but the same shape would qualify
unopposed tomorrow.

**Rule (installed in `check.passes()`, shared by check / bigboard / windows /
picks; `who.py` prints the bars).**
- *thin*: prior P/L < $250k **or** prior volume < $2M → no play. The lifetime
  bars must have been earned before the current 30-day window.
- *cold*: month hold < −3% on ≥ $1M month volume → no play.
- `check.annotate_record(hits)` computes both. The MONTH leaderboard slice only
  ranks the top 500 by P/L, so a losing month is invisible in the pool; for every
  wallet on the slate that clears the lifetime bars and has no month row, it does
  one paced per-user MONTH lookup. Board flags: `thin`, `cold` (wide/--all) and
  `THIN RECORD — … / COLD — …` in the block layout.

**What it removes today (pool of 323 gate-passers, 86 with any month volume):**
42 thin, 11 cold, 1 both. Active names removed: mooseborzoii ($62M volume, all
in the last month), joseph00677 ($2.9M P/L, all this month), Kkunn, alwaysfade,
monkeymashingkeyboard, VD721, Diabolical-Prize, totoro3miyazaki (thin);
curie, Talvez10, ndb1 (−26.5%), Jsram, jtwyslljy, PowerSupplyUnit (−34%),
bomber (cold). Unaffected: Kch-Temp, BreakTheBank, omnibus-076daa, UpTheBlues.

**Evidence, honestly.** Research windows (101 windows, 7,234 qualifying settled
positions, 199 wallets) show *no* forward-ROI difference by prior-hold band,
prior volume, or stake/prior-volume concentration (prior hold > 30%: −1.4%
[−14.8, +12.3] vs ≤ 30%: −2.6% [−5.0, +0.4]). The form half is untestable
there: window pools are built from the MONTH top-500, so no qualifying row has a
negative month. The depth half follows from the 2026-10-07 persistence study
(a 30-day ROI does not predict the next 30), i.e. a wallet whose whole record is
one month has not shown anything yet. This is a data-integrity gate, not a
measured edge: EXP-032 tracks it in the picks ledger via the `thin`/`cold`
flags on signals.
