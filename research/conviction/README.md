# research/conviction — EXP-039

Conviction score (0–100) and bet / no-bet call. Writeup:
`docs/reviews/2026-11-04-conviction-model.md`.

| file | what it does | needs |
|---|---|---|
| `features.py` | builds the corpus into one row per play (game, market, side), using only what is known at T-60: profile, stack/opposition, line move, labels | pandas, numpy |
| `model.py` | fits gradient-boosted and decision-tree models walk-forward by month, per feature family and combined, and tests them against the market price | + lightgbm, scikit-learn |
| `score.py` | **conviction v6**: points score, reasons, bet gate | stdlib only (host Python 3.9) |
| `evaluate_v6.py` | grades v6 on the research plays: bands, the bet, ablations, by period, sport and month | pandas |
| `live_check.py` | grades v6 on `~/.nightly-line/ledgers` (live signals) | stdlib + `wallethist.py` |

```
cd ~/SharpTracker
python3 research/conviction/features.py --corpus ~/nl-research/windows --out ~/nl-research/conviction/plays.parquet
python3 research/conviction/model.py --plays ~/nl-research/conviction/plays.parquet --out results/exp039-ml-walkforward.md
python3 research/conviction/evaluate_v6.py --plays ~/nl-research/conviction/plays.parquet --out results/exp039-v6-research.md
python3 research/conviction/live_check.py --out results/exp039-v6-live.md
```

The play table holds wallet addresses, so it stays in `~/nl-research` and is
never committed (docs/exclusions.md).
