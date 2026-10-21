# Exhaustive wager-vs-history feature screen

Generated 2026-10-20. Features computed as of bet time from the wallet's own earlier wagers only (>= 20 prior). Flat 1u at entry + 1.5% vig, CIs bootstrap 2000 clustered by game. 10 features x 2 populations, so the significance bar is Bonferroni-adjusted to alpha=0.0050; SURVIVES means same sign AND significant in BOTH halves of a chronological split.

Rows 106293 · experienced subset (n_hist >= 100) 68242

## 1. All wallets with history

### All wallets, n_hist >= 20 — n=106293, 12998 games, 2228 wallets

Baseline flat ROI -2.29% · train 2025-09..2026-05 (73884) · test 2026-05..2026-10 (32409)

| Feature | Spearman | Top-bin TRAIN | Top-bin TEST | Bot-bin TEST | Verdict |
|---|---:|---|---|---|---|
| rel_med | -0.0104 | -2.2% [-3.6,-0.9] n=15944 | -2.9% [-5.2,-0.4] n=5315 | -4.8% [-7.0,-2.7] n=6780 | sign holds, not sig |
| rel_mean | -0.0105 | -1.5% [-2.9,-0.0] n=15221 | -3.1% [-5.2,-0.7] n=6038 | -4.4% [-6.5,-2.2] n=6169 | sign holds, not sig |
| rel_p90 | -0.0100 | -1.2% [-2.6,+0.4] n=15187 | -2.8% [-5.1,-0.5] n=6072 | -3.4% [-5.5,-1.0] n=6118 | sign holds, not sig |
| pctile | -0.0087 | -2.1% [-3.6,-0.6] n=14594 | -2.8% [-4.8,-0.6] n=6665 | -4.3% [-6.6,-2.1] n=6638 | sign holds, not sig |
| z_log | -0.0103 | -2.3% [-3.7,-0.9] n=15287 | -3.4% [-5.5,-1.3] n=5972 | -4.7% [-6.9,-2.6] n=6644 | **SURVIVES** |
| is_max | -0.0081 | -2.8% [-5.5,+0.1] n=3056 | +0.9% [-3.4,+5.4] n=1377 | -3.3% [-4.3,-2.3] n=31032 | no |
| rel_recent | -0.0085 | -1.6% [-3.0,-0.3] n=15963 | -1.4% [-3.7,+1.0] n=5296 | -5.1% [-7.3,-2.9] n=6877 | sign holds, not sig |
| ramp | -0.0045 | -2.2% [-3.6,-0.9] n=14477 | -5.5% [-7.4,-3.4] n=6782 | -1.2% [-3.2,+0.7] n=6597 | one half only |
| skew | -0.0062 | -1.6% [-3.0,-0.3] n=16351 | -3.2% [-5.5,-0.8] n=4908 | -3.8% [-5.8,-1.8] n=7877 | sign holds, not sig |
| n_hist | +0.0130 | -1.1% [-2.4,+0.2] n=14144 | -3.6% [-5.4,-1.7] n=7144 | -3.8% [-6.1,-1.6] n=5951 | one half only |

## 2. Experienced wallets only

### n_hist >= 100 — n=68242, 11513 games, 742 wallets

Baseline flat ROI -2.08% · train 2025-09..2026-05 (46512) · test 2026-05..2026-10 (21730)

| Feature | Spearman | Top-bin TRAIN | Top-bin TEST | Bot-bin TEST | Verdict |
|---|---:|---|---|---|---|
| rel_med | -0.0123 | -2.2% [-3.8,-0.7] n=10456 | -3.4% [-6.2,-0.6] n=3193 | -4.6% [-7.2,-2.0] n=4733 | sign holds, not sig |
| rel_mean | -0.0135 | -1.8% [-3.5,-0.1] n=9779 | -2.6% [-5.2,+0.1] n=3870 | -3.6% [-6.3,-1.1] n=4192 | sign holds, not sig |
| rel_p90 | -0.0138 | -1.7% [-3.4,+0.0] n=9735 | -3.3% [-6.0,-0.8] n=3914 | -3.9% [-6.4,-1.1] n=4184 | sign holds, not sig |
| pctile | -0.0119 | -2.3% [-4.1,-0.6] n=9227 | -2.4% [-4.7,-0.0] n=4424 | -4.5% [-7.0,-1.8] n=4674 | sign holds, not sig |
| z_log | -0.0137 | -2.1% [-3.9,-0.6] n=9903 | -4.1% [-6.7,-1.6] n=3746 | -5.4% [-7.9,-2.7] n=4572 | one half only |
| is_max | -0.0097 | -4.4% [-9.8,+0.9] n=783 | -1.1% [-7.8,+5.7] n=414 | -2.8% [-3.9,-1.6] n=21316 | sign holds, not sig |
| rel_recent | -0.0096 | -2.6% [-4.2,-0.9] n=10460 | -0.1% [-3.0,+2.8] n=3189 | -4.8% [-7.4,-2.3] n=4706 | one half only |
| ramp | -0.0057 | -0.9% [-2.5,+0.7] n=9377 | -5.4% [-7.9,-3.0] n=4272 | -0.9% [-3.2,+1.6] n=4446 | one half only |
| skew | -0.0063 | -1.7% [-3.2,-0.1] n=11289 | -4.0% [-7.4,-0.9] n=2360 | -3.5% [-5.6,-1.4] n=5710 | sign holds, not sig |
| n_hist | +0.0078 | -0.8% [-2.5,+0.9] n=8712 | -3.4% [-5.4,-1.1] n=4946 | -1.2% [-3.7,+1.4] n=3978 | one half only |

## 3. Conjunctions (all wallets)

### Conjunction — big vs BOTH centres

| Rule | n | Games | Flat ROI | 90% CI | P(ROI<=0) |
|---|---:|---:|---:|---|---:|
| rel_med >= 2 only | 23972 | 7962 | -1.46% | [-2.6, -0.4] | 98.9% |
| rel_mean >= 2 only | 141 | 139 | -14.87% | [-27.5, -1.1] | 95.9% |
| BOTH >= 2 | 40494 | 8886 | -2.25% | [-3.1, -1.4] | 100.0% |
| BOTH >= 3 | 23980 | 6775 | -2.11% | [-3.3, -0.9] | 100.0% |
| BOTH >= 5 | 11125 | 4249 | -1.87% | [-3.5, -0.3] | 97.7% |
| neither | 41686 | 10192 | -2.75% | [-3.7, -1.8] | 100.0% |
| pctile >= 0.95 | 22070 | 6829 | -2.26% | [-3.4, -1.1] | 99.9% |
| pctile >= 0.99 | 7067 | 3395 | -1.89% | [-3.9, +0.1] | 93.8% |
| career-largest bet | 4433 | 2523 | -1.68% | [-4.0, +0.6] | 87.3% |

## 4. Conjunctions (experienced only)

### Conjunction — big vs BOTH centres

| Rule | n | Games | Flat ROI | 90% CI | P(ROI<=0) |
|---|---:|---:|---:|---|---:|
| rel_med >= 2 only | 17169 | 6832 | -0.94% | [-2.2, +0.4] | 87.5% |
| rel_mean >= 2 only | 51 | 51 | -5.34% | [-26.2, +14.0] | 66.3% |
| BOTH >= 2 | 27273 | 7631 | -2.19% | [-3.2, -1.1] | 100.0% |
| BOTH >= 3 | 16563 | 5728 | -2.39% | [-3.7, -1.1] | 100.0% |
| BOTH >= 5 | 7911 | 3524 | -2.17% | [-4.1, -0.3] | 96.8% |
| neither | 23749 | 8235 | -2.77% | [-4.0, -1.6] | 100.0% |
| pctile >= 0.95 | 14565 | 5688 | -2.35% | [-3.7, -1.1] | 99.9% |
| pctile >= 0.99 | 3831 | 2288 | -2.57% | [-5.1, -0.2] | 96.2% |
| career-largest bet | 1197 | 909 | -3.28% | [-7.7, +0.8] | 90.1% |

## 5. Verdict

Surviving features, all wallets: z_log

Surviving features, experienced: **none**

