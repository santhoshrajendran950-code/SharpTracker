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
| [`docs/`](docs/) | Methodology, results, the honest verdict, and the exclusion list. |

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

## Running it

Everything assumes the host layout (`/Users/santhosh/.nightly-line`,
`/Users/santhosh/nl-research`, `/usr/bin/python3` = 3.9 with no third-party
packages). Secrets resolve from the environment first, then the macOS Keychain —
nothing is read from a file in the tree. See `production/README.md`.
