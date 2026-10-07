# nl-research — historical NFL / NCAAF backtest

Separate from ~/.nightly-line. `lib/` holds FROZEN copies of check.py (v4),
units.py (s5), settle.py and btlib.py taken 2026-09-23; production can change
without altering these results, and nothing here writes to production.

Pipeline, per window (e.g. `windows/2025-10/`):

  1. hist_slate.py     gamma: every closed NFL/CFB event in the window, main
                       markets only (ML / spread / total), tokens + settlement
  2. hist_discover.py  data-api /trades on every market, takerOnly=false,
                       fills >= $2,500 -> the wallets that bet size THEN
  3. hist_prior.py     each wallet's realised P/L and volume in the 90 days
                       BEFORE the window, from /activity -> prior hold / P/L
                       (the leaderboard cannot be read as of a past date)
  4. hist_rebuild.py   positions at T = kickoff - 60m from /activity (same
                       method as backtest/bt_rebuild.py)
  5. hist_score.py     price at T from CLOB history, settle, score with the
                       frozen v4, size with the frozen s5
  6. hist_report.py    the same tables as bt_strategies.py

Every stage is resumable (checkpoints in the window dir) and rate-limited to
concurrency 2 — the data-api throttles hard and a failed call is counted,
never swallowed.
