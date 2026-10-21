# EXP-033 — candidate-rule screen, 2026 football season

**Status:** hypothesis generation. Not a validation, and nothing here is
deployable. The deliverable is a ranked shortlist of rules to carry into the
year-long nl-research window.

**Run:** 2026-10-19. Scripts `research/screen_features.py`,
`research/screen_rules.py`, `research/bookclose.py`,
`research/backfill_2026.sh`. Report `nl-research/screen_report.txt`, machine
copy `nl-research/screen_results.json`.

---

## Why this is a screen and not a test

The project has already answered the question "does the live rule win on
football" — it does not. The full-season 2025-26 football backtest returned five
losing months out of seven ([LEDGER](../docs/experiments/LEDGER.md), nl-research
`season.log`). Separately, per-trader ROI was shown not to persist (2026-10-07,
rank correlation −0.00 over 1,283 traders) and no bet-time feature or fitted
model predicted ROI out of sample. So the prior going in is that no rule of this
shape will work, and the useful output of a screen is a shortlist plus an honest
statement of how much of any apparent edge is selection.

That last part is the main contribution here, and it is quantified rather than
asserted — see **Selection calibration** below.

## Data

| | |
|---|---|
| Window | Sep 1 → Oct 18 2026 (see the scope note below) |
| Sports | NFL and NCAAF, **analysed separately, never pooled** |
| Scorer | frozen v4 in `nl-research/lib/` — the research copy, untouched by live changes |
| Position basis | reconstructed at **T-60** (kickoff minus 60 minutes) by `bt_rebuild.py` |
| Universe | scored positions with stake ≥ **$2,500** and not a hedger's two-sided book |
| Settlement grading | flat 1u at the T-60 price + **1.5 pt vig**, outcome from `won` |
| CLV, Polymarket | last Polymarket trade on the token before kickoff, minus the T-60 price |
| CLV, sportsbook | median de-vigged book close across books, minus the T-60 price |

The $2,500 floor is the scanner's own `min_stake`: below it a position is never a
candidate play, so including it would pad every count with rows the rule could
never have fired on. All 210 frozen-v4 qualifiers sit above the floor, so no
qualifying position is lost to it.

### Scope note — CFB's opening weekend is left out, deliberately

The brief said "from season start". The NFL's regular season starts 2026-09-09,
inside the window. **CFB's starts 2026-08-27**, which is not: the window begins
Sep 1. The `2026-08` research window does exist and was scored in the earlier
season run, but it yields **<!--AUG_POS--> candidate positions across
<!--AUG_GAMES--> games** above the $2,500 floor, from a pool of
<!--AUG_WALLETS--> wallets — because that window's 90-day prior
cuts at 2026-08-01, when most of the current sharps had no qualifying record
yet (4 of the top 6 first traded in June 2026). Its pool is therefore not
comparable to the Sep/Oct pools of 400+ wallets.

Those positions sit below this screen's own 10-game reportability floor and
could not support any rule, while adding an earlier slate would move the 60/40
exploration boundary. The exclusion is on pool construction, decided before any
outcome was looked at — not on results. The one NFL August game (2026-08-16) is
preseason and is excluded on those grounds.

### Window coverage had to be fixed twice, and the second fix is a pipeline bug

**Pass 1 — unsettled games.** The windows as they stood covered **Sep 10–22 and
Oct 4–6 only, with no CFB at all in October**, because each had been built while
later games were still unsettled and `hist_slate.py` takes only closed events.
`backfill_2026.sh` re-ran both windows to Sep 1–30 and Oct 1–18.

**Pass 2 — two defects in `hist_slate.py` itself.** Auditing the census dates
after that backfill showed NFL slates starting **2026-09-17**, with no Week 1
anywhere, and no CFB on Sat **Oct 3**. Both turned out to be discovery bugs, not
absent data:

1. **The window filter asked the wrong question.** `hist_slate` filtered gamma
   on `start_date_min`/`start_date_max`, but on a game event `startDate` is when
   the *market opened*, not kickoff — the Week 1 49ers/Rams game opened
   2026-07-31 and kicked off 2026-09-11. Kickoff lives in `startTime`. Because
   the walk only covered open-dates inside the window ±3 days, every game listed
   earlier was invisible: **all 13 games of NFL Week 1's Sunday** (2026-09-13),
   Thu 9/10, and Thu 10/1 among them. gamma does support `start_time_min`/
   `start_time_max` on full ISO timestamps, which is the correct filter; the
   fixed stage sweeps that and unions in the old open-date sweep.
2. **Pages were large enough to time out.** A game event carries every prop
   market it ever listed (~300–380), so a 100-event page of CFB Saturdays is
   **~46 MB and ~20 s to serve** — right against the 30 s `curl` timeout in
   `lib/check.py`. Those slices failed intermittently and `fetch()` returned
   short *in silence*, indistinguishable from a quiet week. Paging at 25 brings
   the same request to ~13 MB in ~6 s.

The verified consequence for this experiment: the September window held **32 NFL
games where gamma has 48**. A missing NFL Sunday is 18% of the sport's games, so
the screen was re-run from discovery on the corrected windows.

Beyond the fix, `fetch()` now records every unreadable slice under `holes` in
`slate.json` and the stage says so on exit, because the failure mode here was
not that a request failed — it was that a failure looked exactly like data.
**This is a bug in shared `nl-research` discovery, not in anything specific to
EXP-033**, so every window ever built by this stage is affected to the degree its
games were listed early.

Stages above the rebuild are resumable (`hist_discover` skips markets already in
`trades.jsonl`, `hist_prior` skips wallets already in `prior.json`), so only
newly-added games cost anything; positions are rebuilt from scratch because the
slate is wider. `btlib` never takes production's sweep lock — it waits on it, so
the live picks job is unaffected. Earlier windows are preserved in
`nl-research/windows/_prebackfill-*` (pass 1) and `_partialscope-*` (pass 2).

### Disclosure: the holdout was looked at on the partial window first

The protocol says to report the holdout once. It was in fact computed twice, and
pretending otherwise would be the dishonest option:

- **Pass A** ran the complete protocol on the partial windows above, and its
  holdout was computed and read. Report kept at
  `nl-research/partialscope_screen_report.txt`.
- The coverage gap was then found by auditing the **census dates** — the
  protocol's own first step — and diagnosed as the two `hist_slate` bugs. It was
  not found by looking at rule results, and the fix was decided before any
  corrected number existed.
- **Pass B**, reported below, is the full-scope run and is the one that counts.

Both are reported, so the extra look is visible in the multiplicity accounting
rather than hidden by it. Pass A's top-3 holdout ROIs were NFL +55.7% (n=6, below
the floor), −13.0% (n=48), +18.1% (n=33) and NCAAF +7.9% (n=28), −8.5% (n=41),
−6.1% (n=26) — five of six under the 30-position floor or flagged underpowered,
which is itself the finding that a 40% holdout of this window cannot power a
narrow rule.

### Sportsbook closes are partial, and that is not fixable

`linearchive.py` began collecting on **2026-09-20** (first poll 19:38 EDT) for
NFL and **2026-09-24** for CFB. Everything before that has no book line and
never will. Within coverage the join is near-complete for moneylines and thin
for spreads and totals, because Polymarket lists a ladder of alternate lines
(one game carried O/U 45.5 through 54.5) and the books only ever quoted the main
line and the one or two they moved through.

The join was validated against an independent quantity: book `close_fair`
versus the Polymarket close already in the scored rows agrees to a **mean
difference of +0.0000** (median −0.0005, mean absolute 0.0113, max 0.0363). A
flipped side or a wrong home/away orientation would show up as a ~(1−p) error;
none appears. `game` is confirmed AWAY vs. HOME on 119/119 joined keys.

Consequence for the screen: **Polymarket CLV is the ranking metric**, because
every row has it. Book CLV is reported alongside wherever it exists and is never
used to choose a rule, since its coverage is correlated with both market type
and date.

## Pre-registered dimensions

Six, three levels each, fixed before any result was looked at. No dimension was
added mid-run.

| # | Dimension | Levels |
|---|---|---|
| 1 | sharps trailing the same side | ≥2, ≥3, ≥4 |
| 2 | wallet quality, 90-day prior P/L | ≥$50K, ≥$150K, ≥$250K |
| 3 | wallet history depth, resolved positions in prior 90d | ≥25, ≥50, ≥100 |
| 4 | price band at T-60 | −122..−180 (live rule), −110..−250 (wide), −130..−165 (narrow) |
| 5 | stake relative to that wallet's own median | ≥2×, ≥5×, ≥10× |
| 6 | entry timing, first buy before kickoff | ≥6h, ≥24h, <3h (late money) |

### Three definitional choices, reported rather than buried

**1. "Sharps on a side" needs production's bar, not the research pool's.**
Counted over every pooled wallet — the pool clears hold ≥1.5%, 90d P/L ≥$50K,
volume ≥$500K — **79% of candidate positions already have 4+ wallets on the same
lean**, so the registered levels {2,3,4+} cannot discriminate at all. The
dimension therefore counts only wallets clearing the *production* quality bar
(90d prior P/L ≥$250K and hold ≥1.5%), which is what production means when it
says a side has N sharps on it. That yields 41% / 16% / 11% of positions at
≥2 / ≥3 / ≥4. Both counts are written to `screen_rows.jsonl` (`trail`,
`trail_pool`). Leans are grouped with the frozen `check._lean()`, so a team's ML
and its spread count as one opinion, exactly as production counts it.

**2. Slate dates come from the Eastern kickoff, not the slug.** Polymarket slugs
carry the UTC date, so a Sunday 8:20pm ET kickoff is slugged Monday and a Monday
night game is slugged Tuesday. Splitting on slug dates scattered one NFL Sunday
across three "slates" and would have put parts of a single slate on both sides
of the train/holdout boundary — the exact leak the time split exists to prevent.

**3. Entry timing is only measurable on the big positions.** `lead_h` comes
from the market-first trade pull, which keeps fills ≥$2,500. Across all scored
rows its coverage is 39%, and the covered rows have a median stake of $8,697
against $476 for the uncovered ones — so on the full row set the dimension is
hopelessly confounded with stake size. Inside the $2,500 universe coverage is
85% and the confound is gone. This is a second reason the universe is the floor.

## Protocol

- **Split by time, not at random.** Earliest 60% of distinct slate dates per
  sport is exploration; the most recent 40% is holdout. The split is on dates,
  so no slate straddles the boundary.
- **The top 3 rules are chosen on exploration alone**, then the holdout is
  reported **once**. No iteration on the holdout.
- Singles first, then all 2-way combinations across different dimensions.
- Any reported rule needs ≥30 graded positions **and ≥10 distinct games**;
  30–100 positions is flagged underpowered.
- Ranking weights ROI and CLV **equally**.
- Every rule carries n, games, win rate, ROI, a game-clustered bootstrap CI on
  ROI (2,000 reps, 95%), and CLV as both mean and % positive. Each metric is
  z-scored across the surviving candidates on exploration, so that percent and
  points cannot dominate by units alone.
- Two baselines, on both splits: **(a)** every candidate position unfiltered,
  **(b)** the frozen v4 production filter.

**The games floor is a deviation from the brief**, which set a position floor
only. It was added because positions are not independent observations. On the
NFL exploration split, `trail>=3` has **42 positions in a single game** — only
one game in that span ever had 3+ production-grade sharps on one lean — so a
position-only floor passes a "rule" that is one coin flip, and would have let it
into the top-3 with a bootstrap CI computed from one cluster. Games are the unit
the CIs already cluster on. The floor drops 18 of 77 surviving NFL
configurations, `trail>=3` and `trail>=4` among them, which is also the honest
finding that **those two levels are untestable on this window at all**.

CIs are **game-clustered** because positions in one game share an outcome;
resampling positions would understate every interval badly.

Baseline (b) is the *frozen v4* filter, not today's live rule. The live rule has
since added the contested check, the EXP-030 line-move tier and the EXP-032
thin/cold gate, none of which can be reconstructed from research rows (line-move
needs the live `linemove.py` history). So (b) is a v4 snapshot and is labelled
as one.

## Selection calibration — how much of any edge is artifact

The protocol asks this explicitly, so it is measured rather than hand-waved.

Shuffling outcomes would answer the wrong question, because favourites really do
win more often. Instead the null simulates a market that is **exactly
efficient**: every position settles from its own T-60 price, which is also the
price the screen bets at, so any ROI that appears is selection alone.
Within-game correlation is preserved, since that is what makes selection
dangerous here — each (game, lean) draws one uniform, a token on the reference
side wins at `u ≤ p` and a token on the other side at `u > 1 − p`, which is
exact complementarity for a two-way market.

For each of 400 replications the screen is re-run across every surviving
configuration and the **best** ROI is recorded. That distribution is what "the
luckiest of N rules" looks like when nothing works.

---

## Census

<!--CENSUS-->

## Results

<!--RESULTS-->

## Decision

<!--DECISION-->
