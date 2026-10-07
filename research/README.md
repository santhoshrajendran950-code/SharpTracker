# research/ — historical backtests

Mirror of `~/nl-research` on the host, minus the raw data (wallet-keyed,
~480 MB — see `../docs/exclusions.md`). `lib/` holds **frozen** copies of the
production scoring/sizing modules taken 2026-09-23, so production can change
without altering these results. Nothing here writes to production.

## Pipeline, per sport-month
| Stage | File | What it does |
|---|---|---|
| 1 | `hist_slate2.py` (`hist_slate.py` = the original football-only version) | Every closed game/match in the window from gamma, main markets only, tagged `market_type`. Sport shape from `lib/sport_config.py`: team / soccer-3-way / h2h. h2h sports get one game per market (independent kickoffs). Uses `gameStartTime`, not `startDate` (a listing time — found the hard way). |
| 2 | `hist_discover.py` | `/trades` on every market, `takerOnly=false`, fills ≥ $2.5K → the wallets that bet size **then**. |
| 3 | `hist_prior.py` | Each wallet's realised P/L, volume, hold and settled-market count over the 90 days **before** the window, from `/activity`. The leaderboard can't be read as of a past date. |
| 3.5 | `hist_adapt.py` | Writes the frozen stages' inputs (`bt_slate.json`, `bt_pool.json`) plus a `market_meta.json` sidecar. |
| 4–5 | `lib/bt_rebuild.py`, `lib/bt_score.py` | Positions at T = kickoff − 60 m from activity; price at T from CLOB history; settle; score with frozen v4; size with frozen s5. |
| 6 | `hist_cuts.py` | Five wallet-gate variants side by side: wallets, alerts (≥65), picks (≥70 in −122..−180), record, flat & sized ROI, CI, CLV, by market type. |
| 7 | `hist_report_all.py` | The cross-window table: sport × gate × market type. |
| — | `lib/bt_strategies.py` | Four strategy families (sizing, selection, wallets, timing), descriptive + chronological 60/40 out-of-sample, per window. |
| — | `hist_probe_months.py` | The cheap 12-month × 10-sport existence check (`probe_results.json`). |

`run_season.sh` ran the seven football months; `run_all_sports.sh` runs the
92 sport-months the probe kept, sequentially, via `run_sport_window.sh`.

## backtest-v1/
The original 30-day (Aug 16–Sep 15 2026) out-of-sample backtest the live rules
were first built and re-tested on: `bt_pool` (prior = ALL − MONTH), `bt_slate`,
`bt_rebuild`, `bt_score`, `bt_report`, plus `bt_sizing.py` (calibration/Kelly
vs band-table sizing) and `bt_strategies.py`.

## Sports excluded, checked not assumed
- **F1, NASCAR** — every market is a full-field outright; no head-to-head contract exists.
- **MMA, boxing, golf** — real matchup markets exist, but no market carries a usable kickoff (`gameStartTime` null, event `startTime` null; only listing timestamps), which would mistime every T-60 snapshot.
- **NCAAB** — 59 events in Polymarket's history, all tournament/conference-winner futures; zero per-game markets.
