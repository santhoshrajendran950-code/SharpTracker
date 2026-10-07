# SharpTracker

A sharp-money tracker built on Polymarket's public on-chain data. It follows
~1,000 wallets with proven sports-betting records, flags when one of them bets
unusually large, scores the bettor, prices the play at a book you can actually
use, and publishes the result — to a terminal board, to Discord, and to X as
timestamped picks that are settled publicly every night, win or lose.

This repo is the code, the research pipeline, the aggregate results and the
analysis. It deliberately does **not** contain the data — see
[`docs/exclusions.md`](docs/exclusions.md) for exactly what was left out and why.

## Layout

| Directory | What it is |
|---|---|
| [`production/`](production/) | The live system (`~/.nightly-line` on the host): scanning, scoring, sizing, publishing, settlement, CLV. One file per job; each has a docstring. Scheduled by the launchd plists in `production/launchd/`. |
| [`research/`](research/) | The historical backtest project (`~/nl-research` on the host). Frozen copies of the scoring/sizing modules in `research/lib/`, so production can change without altering results. `research/backtest-v1/` is the original 30-day backtest the live rules were built on. |
| [`results/`](results/) | Aggregate reports only. `football/<month>/` has the 7 NFL/NCAAF months; `results/sports/<sport>/` fills in as the 10-sport run completes. `probe_results.json` is the sport × month existence table. |
| [`charts/`](charts/) | Generated figures, described in [`docs/results.md`](docs/results.md). |
| [`docs/`](docs/) | Methodology, results, the honest verdict, and the exclusion list. **[`docs/alert-rules.md`](docs/alert-rules.md)** is the rule record — every threshold that decides what gets alerted, with its evidence. **[`docs/experiments/LEDGER.md`](docs/experiments/LEDGER.md)** is every hypothesis tested, adopted or rejected, plus the weekly proposals. |
| [`results/live/`](results/live/) | Weekly cuts of the live pick ledger from `research/review.py`: score band, price band, market type, league, hold, wager, consensus, override, sizing, week — each with a game-clustered CI and CLV. |
| [`tools/`](tools/) | `nl <command>` runs the terminal commands ("big board", "check again", "who is X"…) documented in [`docs/commands.md`](docs/commands.md). `sync-from-live.sh` pulls the live code and finished research results into this repo, redacted, and refuses to finish if a secret survives. `tidy-live.sh` parks the host's `*.bak` files. |

## The honest headline

After two build windows and seven genuinely out-of-sample football months,
**the signal followed mechanically shows no repeatable edge at a bettable price.**
Five of the seven football months lost; the one strong month (January, playoffs)
was 27 picks. Closing-line value is roughly zero across the board — a follower at
T-60 gets the closing price and then pays the vig.

What the analysis *does* support, consistently:

- **Price is a filter, not a score.** Underdogs lose in every window. Bettor-profile
  features only order outcomes among favourites, so graded picks are restricted to
  −122..−180 and the bettor is scored separately (conviction v4).
- **Sizing cannot manufacture edge.** Five sizing versions were tested; none beat
  flat out of sample. The live version (s5, score bands snapped to ½/1/2/3/4u)
  earns its place only by risking less with a shallower drawdown.
- **The KPI is CLV on published picks**, not ROI. ROI needs thousands of games to
  separate a 3% edge from noise; CLV needs ~200 picks. Target set: CLV ≥ 0 over
  200 graded picks before charging anyone for anything.

The operator's own week following the tracker was +28% (65 positions, 51 games) —
that came from selection and price shopping (+1.7 pts vs the sharps' entries),
not from the signal. The per-trader overrides in `production/form.py` are that
judgment automated, tagged so it can be measured on its own.

Full numbers and charts: [`docs/results.md`](docs/results.md).
How everything is computed: [`docs/methodology.md`](docs/methodology.md).

## Where things live

- **Runtime:** `~/.nightly-line` on the Mac — data, logs, secrets, and the 32
  launchd jobs. Never run from this repo.
- **Code of record:** this repo. After editing a live script, run
  `tools/sync-from-live.sh` and commit; the script redacts and checks.
- **Research:** `~/nl-research` (raw windows, ~480 MB) mirrored here minus data.

## The improvement loop

Weekly, Monday morning:

1. `tools/sync-from-live.sh` — code and finished research results into the repo.
2. `python3 research/review.py --write` — every cut of the live ledger into
   `results/live/<date>.md`, with the KPI line (CLV ≥ 0 over 200 v4/s5 picks).
3. Read the cuts against `docs/alert-rules.md`. Any adopted rule whose live
   cut now contradicts the evidence it was set on gets a line in
   `docs/experiments/LEDGER.md`, and up to three new hypotheses are filed from
   `docs/experiments/TEMPLATE.md` — each with the script that tests it and a
   pre-registered pass/fail. Cheap tests run the same day on the 99 research
   windows already on disk.
4. Commit and push.

Rules of evidence: a cut under ~40 games or with a CI through zero is noise;
CLV is the KPI; one window proves nothing; "nothing survives" is an answer.
Nothing in the loop touches production — a rule changes only when Santhosh
says so, and the change is logged in both files.

## Running it

Everything assumes the host layout (`/Users/santhosh/.nightly-line`,
`/Users/santhosh/nl-research`, `/usr/bin/python3` = 3.9 with no third-party
packages). Secrets resolve from the environment first, then the macOS Keychain —
nothing is read from a file in the tree. See `production/README.md`.
