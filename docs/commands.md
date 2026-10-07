# The commands

The phrases Santhosh uses in the terminal, what each runs, and how the output
is meant to be read. `tools/nl <name>` runs any of them; the scripts live in
`~/.nightly-line` on the host and are mirrored in [`production/`](../production/).

| Phrase | Runs | Prints |
|---|---|---|
| **"big board"** | `bigboard.py --wide` | Every qualifying open position on today's slate, one row per play |
| **"check again"** | `check.py` | The rest of today's slate, high-conviction plays only |
| **"macro board"** | `macro.py [econ\|politics\|crypto\|finance]` | Non-sports sharp money, grouped by view |
| **"how are we doing"** | `scorecard.py` | Signal record, ROI, CLV, game-clustered CI |
| **"how are the picks doing"** | `picks.py --record` | The graded public-pick ledger by tier / sizing / league |
| **"what moved"** | `drift.py` | Live signals the market has moved against, while still bettable |
| **"who is X"** | `who.py X` | One trader: career vs current form, open book, ledger record |
| weekly review | `research/review.py --write` | The live ledger cut 14 ways → `results/live/<date>.md` |

## "big board" — `bigboard.py --wide`

Render the pipe-delimited rows as a table with exactly these columns, in
this order:

**Conv | Size | Wager | Play | Odds | Game | Start | Bettor | Hold | Month P/L | Lifetime P/L**

- `--wide` also emits `vs Avg` and `Flags`; drop them.
- **Conv** is v4 (bettor profile only, 20–100). **Size** is units inside the
  −122..−180 pick window, `alert` if conv ≥ 65 outside it, `—` otherwise.
- **Bettor** is the real username. Terminal only; every public surface uses
  the codename from `anon.py`.
- **Hold** = lifetime sports P/L ÷ volume from the leaderboard, never
  recomputed from trades.
- Keep the header line (games scanned, `N/690 wallets`, qualifying count).
  Bold the top rows and any `LIVE` or imminent start.
- Flags: `--pre` pre-kickoff only (what the pick publisher sees), `--live`
  include games started in the last 6 h, `--all` ignore the score floor,
  `--min-score N`, `--json <path>` for the publishers, league names to filter.

## "check again" — `check.py`

Short and summarized, high-conviction plays only. No methodology recap, no
per-league walkthrough. Covers every tagged league (NFL, CFB, NBA, CBB, MLB,
WNBA, NHL, tennis, golf, soccer), games more than 10 minutes out; anything
underway is dropped. Prints only positions clearing `passes()` (hold ≥ 1.5%,
P/L ≥ $250K, stake ≥ $2.5K) — see [`alert-rules.md`](alert-rules.md) §2.
Flags: `--live`, `--hours N`, `--strict` (score ≥ 60 only), `--alert N`
(the email path; writes its own snapshot so manual runs never suppress an
alert), `--pool N`, `--min-hold X`.

## "macro board" — `macro.py`

One category per event (ECONOMICS > CRYPTO > FINANCE > POLITICS so a politics
trader's record never vouches for a Fed bet). Freshness is dollars actually
bought in the last 7 days from `/activity`, not entry ≈ current. Hedges
(opposite sides, or YES on 2+ outcomes of a neg-risk event) are excluded
unless one leg is ≥ 75% of the stake. Plays are grouped by view. A full run is
~110 s over ~1,340 wallets; `econ` alone ~22 s. Flags: `--all`, `--days N`.
Not graded; reference only.

## "how are we doing" — `scorecard.py`

Lead with the OVERALL line and the bootstrap verdict, not the prettiest trait
row. The CI is clustered by game; if it contains zero, say there is no
measured edge rather than quoting the point estimate. Reads `signals.jsonl`
only; never the private bet log. Flags: `--days N`, `--model v1|v2`,
`--verbose`.

## "how are the picks doing" — `picks.py --record`

The public record: every graded pick with units, book odds, tier, sizing
version, result, Polymarket close and CLV. Units and ROI only, never dollars.
Quote the v4/s5 rows as the forward record; the 14-day replay (20-7, +29.9%)
was in-sample and is not a record. `--days N` for a window. The other
`picks.py` modes are the publisher's: `--scan`, `--settle`, `--report`,
`--day`, `--open`, `--log`.

## "what moved" — `drift.py`

MOVED AGAINST rows first; those are the warnings. A drifting line is
ambiguous (news, or the market catching up to a sharp who was early), so it
is a flag, never "fade this". Unlike CLV it is measurable while the game is
still bettable. Flags: `--hours N` (default 24), `--all` to include
favourable and flat moves.

## "who is X" — `who.py X [--positions]`

Accepts a name fragment or a `0x` wallet. Splits `prior = ALL − MONTH` so
career record and current form sit side by side. Quote the form verdict and
always carry the thin-slice caveat when it fires: a 24% hold on $2.1M against
a $237M career is noise, not a hot streak. `--positions` lists the open book.

## Weekly review — `research/review.py`

See the README's "improvement loop". `--days N` windows the ledger;
`--write` saves to `results/live/<date>.md`. Read the cuts against
[`alert-rules.md`](alert-rules.md); contradictions and new hypotheses go in
[`experiments/LEDGER.md`](experiments/LEDGER.md).

## Scheduled, not asked for

`alert.sh` (email, 15 min), `run-picks.sh` (X/Discord picks, 10 min),
`run-picks-results.sh` (nightly card), `holders.py --discover` (11:30),
`catalyst.py --alert` (15 min), `weeksheet.py` (Mon 08:00). Schedule in
[`alert-rules.md`](alert-rules.md) §7; plists in `production/launchd/`.
