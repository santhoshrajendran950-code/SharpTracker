# Experiments — status (2026-10-14)

KPI: signal CLV ≥ 0 over 200 graded v4 picks — **57 / 200, +0.50 ± 0.40**. Week: 6 settled, 5-1. Full notes: [weekly review](../reviews/2026-10-14-weekly.md) · [LEDGER](LEDGER.md).

| EXP | Hypothesis | Rule it changes | n now / needed | Current CLV / ROI | Decision ETA | Pass condition |
|---|---|---|---|---|---|---|
| [032](LEDGER.md#exp-032--thin--cold-wallet-gate) running | Wallets whose record is < 30 days old, or losing > 3% this month, are not reads | `check.passes` MIN_PRIOR_PNL/VOL, FORM_* | 0 / 60 days | — | 2026-12-13 | removed set worse on CLV than qualifiers; else revert |
| [030](LEDGER.md#exp-030--line-move-tier-exclude-moving-away-order-alerts-by-expected-move) running | Lines moving away from the sharp = alert only | `linemove.py`, `picks.LINEMOVE_GATE` | 289 / 300 settled signals | toward +25% [+7,+43] · away −17% [−30,−1] | ~2026-10-16 | away tier below toward, CI excluding zero |
| [028](LEDGER.md#exp-028--mlb-alert-only) proposed | MLB should be alert-only | `picks.qualifies` league block | 13 picks / one more live month | −45% [−86, −5] | 2026-11-07 | live interval still excludes zero |
| [026](LEDGER.md#exp-026--fresh-bonus-and-stale-penalty-have-the-wrong-sign-1025k-wager-tier-over-scored) proposed | fresh/stale points have the wrong sign; $10–25K tier over-scored | `check.py` fresh +10, `units.py` stale | research windows, not yet run | — | when run | sign flips OOS in ≥ 3 sports |
| [019](LEDGER.md#exp-019--totals-are-not-a-down-weight-they-may-be-the-best-market-type) proposed | Totals should not be sized ×0.75 | `units.TOTAL_MULT` | 18 live totals / 40 games | −27% [−59, +5] | n ≥ 40 | totals ≥ sides on CLV and ROI |
| [021](LEDGER.md#exp-021--nba-moneylines-should-be-alert-only) proposed | NBA moneylines alert-only | `picks.qualifies` | 0 / 40 (season starts late Oct) | research −14.6% [−27, −3] | Dec | live NBA ML interval excludes zero |
| [023](LEDGER.md#exp-023--esports-is-the-first-sport-worth-adding-as-alert-only) proposed | Esports alert-only is worth adding | `check.TAGS` | needs a tag + Santhosh | research +5.9% [−2, +14] | his call | — |
| [024](LEDGER.md#exp-024--measure-wallet-discovery) proposed | Instrument wallet discovery | none yet | — | — | — | — |

**Decided this week:** EXP-029 CLV license — **rejected** (top tier +0.7% [−11, +12] vs pool −2.1%, no sport at 40 games, top tier's own in-window CLV −0.37).

**Adopted, awaiting merge:** none.

**Parked:** EXP-025 (earlier than T-60). **Not proposed, on purpose:** EXP-031 (relative-stake ladder), lower gates, wider price window, sizing formulas, dog multiplier, consensus.

**Next week:** EXP-030 decision at 300 settled signals; grade the two stale unsettled picks (2026-09-22, 2026-10-06); KPI pace check.
