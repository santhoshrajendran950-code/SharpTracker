# charts/

Rendered on the host from local data with matplotlib; only the PNGs are
committed. Regenerate with the snippet in the git history of this directory
(it reads `ledgers/`, `signals.jsonl`, `probe_results.json` and the parsed
`cuts.txt` files, none of which are in the repo).

| File | What it shows | Source |
|---|---|---|
| `football_monthly_cutA.png` | Flat vs s5-sized ROI per football month under the live rule, with avg CLV on the right axis and pick counts under each month. | `results/football/*/cuts.txt`, cut A |
| `sport_month_games_heatmap.png` | Games found per sport-month, Sep 2025–Sep 2026, all 130 cells checked. Log colour scale; the number in each cell is the real count. | `results/sports/probe_results.json` |
| `replay_14d_cumulative.png` | Cumulative sized P/L over the last 14 days of logged signals, replayed through `picks.qualifies()`: default window vs with trader overrides. In-sample — see `docs/results.md` §3. | `signals.jsonl` + `ledgers/` (host only) |
| `v4_profile_score_thresholds.png` | Cumulative flat ROI of every pick at or above each conviction-v4 threshold, inside the −122..−180 window, for both build windows. The 70 and 80 gates are marked. | `profile.json` extract from the two build windows (host only) |
| `kchtemp_price_bands.png` | Kch-Temp's flat ROI by entry-price band across his 32 settled signals; the shaded region is his override window. | `ledgers/` (host only) |
