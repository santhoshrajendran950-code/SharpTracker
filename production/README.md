# production/ — the live system

Mirror of `~/.nightly-line` on the host, minus data and secrets
(see `../docs/exclusions.md`). All scripts assume that path and
`/usr/bin/python3` (3.9, stdlib only). Emails and the Drive folder ID are
redacted to placeholders.

## Scoring, sizing, selection
| File | Purpose |
|---|---|
| `check.py` | The scanner: wallet pool, position sweep, stack/hedge/two-sided detection, `mark_consensus`, **conviction v4** (v2 kept as `conviction_v2`), the `passes` gate, `--alert` mode for the email. |
| `units.py` | Unit sizing **s5** (score bands ½/1/2/3/4u, NFL/trusted/totals/stale adjustments). `TIERS`/`USE_FORM` switches let backtests replay s1–s4. `public_size()` is the one rule every public surface uses. |
| `picks.py` | The public record: `qualifies()` (gate 70, −122..−180 window, no period markets), `scan()`, price lookup, `locked_units`, `caption`, `record` (`--record` prints the running ledger ROI), settlement report. |
| `form.py` | Per-trader judgment: `trusted.json` bumps, ledger hot/cold cuts (20+ games), and `price_window()` — the per-trader price overrides. |
| `anon.py` | One-way trader codenames (salted) and coarse rounding of money/hold for anything public. |
| `bigboard.py` | The terminal board: Conv \| Size \| Wager \| Play \| Odds \| Game \| Start. `--wide`, `--pre`, `--live`, league filters, `--json` for the publishers. `period()` labels 1H/1Q/inning markets. |

## Publishing
| File | Purpose |
|---|---|
| `xpost.py` | X posting (OAuth 1.0a from Keychain), media upload, `post_with_image` with text-only fallback, board caption. |
| `xcard.py` | Headless-Chrome PNG cards: board, single pick, alert, nightly results. |
| `alertpost.py` | Email-parity alerts to X/Discord for plays that are *not* graded picks. |
| `postplays.py` | Discord formatting and routing by sport. |
| `discordsink.py` | Push-only Discord delivery; webhooks from env/Keychain, never logged. |
| `pickclv.py` | CLV on every settled pick vs the Polymarket close — the KPI. Feeds the nightly card. |
| `xboard-mlb.py` | Fires the all-sports X board 15 min before the first MLB pitch on non-NFL days. |
| `weekpost.py`, `weeksheet.py`, `xlsxlite.py` | Weekly public summary; weekly ledger to a Drive sheet. |

## Discovery and diagnostics
| File | Purpose |
|---|---|
| `holders.py` | Market-first sweep: top holders on every main market of today's slate, from **any** wallet; `--discover` writes gate-clearing unknowns to `discovered.json`. Daily at 11:30. |
| `who.py` | One trader's dossier: career vs current form, open book, ledger record. |
| `scorecard.py`, `clv.py`, `myclv.py`, `resolve.py`, `dayledger.py`, `drift.py`, `outliers.py`, `bettors.py`, `today.py`, `scan.py`, `live-watch.py`, `windows.py` | Health reads, CLV, settlement, drift, ad-hoc views. Each docstring says what and why. |
| `books.py`, `oddskey.py`, `linearchive.py` | Sportsbook prices via The Odds API; key from Keychain; hourly line archive. |
| `settle.py` | Resolves bets and refreshes the weekly sheet. |
| `catalyst.py`, `catalyst-drift.py`, `macro.py` | Non-sports: economic-release and macro/politics/crypto sharp money. |

## Scripts and schedule
`run-*.sh` are the launchd entry points; `launchd/` has the 32 plists
(picks every 10 min, alerts ~17 min, boards at kickoff waves, results nightly,
discovery 11:30, weekly sheet Mon 08:00). `deploy/` is a generic server-side
variant with an `.env.example`.
