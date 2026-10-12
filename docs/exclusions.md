# What is not in this repo, and why

The repo is private, but these were left out on purpose. Each one is either a
secret, personal, or the proprietary edge itself.

| Excluded | Why |
|---|---|
| `bets.jsonl` | The operator's personal bet log — stakes in dollars, books, results. Terminal-only by standing rule; it never leaves the machine. |
| `.anon-salt` | The salt behind every public trader codename (`production/anon.py`). With it, every "Cobalt Harrier" on X maps back to a wallet. |
| `.odds-key`, X/Discord credentials | Never stored in files; resolved from env or Keychain. Nothing to commit. |
| `orders.jsonl` | Novig orders actually sent (paper or real): personal money, same footing as `bets.jsonl`. |
| `.novig-read.pem`, `.prophetx-session`, Keychain `prophetx/*`, `novig/key-id` | Exchange API keys (docs/exchange-apis.md). The PEM and token cache live under `~/.nightly-line`, never in the repo. |
| `signals.jsonl`, `ledgers/`, `picks.jsonl`, `alerts-posted.jsonl` | Raw signal and pick ledgers carry real Polymarket usernames and wallet addresses. Per `anon.py`: "the wallet identities are the whole edge." |
| `trusted.json`, `discovered.json` | Real usernames + the notes justifying each override; wallet addresses found by the daily sweep. |
| `check-snapshot.json`, `*-snapshot.json` | Live position snapshots — wallets, sizes, entries. |
| `lines/` | The hourly sportsbook line archive (Odds API output; large, and licensed data). |
| Research raw data (`windows/**/*.jsonl`, `*.json` except `probe_results.json`) | ~480 MB of rebuilt positions, trades and per-wallet priors — all keyed on wallet addresses, and over GitHub's file limits. |
| `backtest-v1` data (`bt_*.json`, `*.jsonl`) | Same reasons; 237 MB. |
| Logs, `*.bak-*` (now parked in `~/.nightly-line/_backups/`), `__pycache__` | Noise. |
| `aliases.json`, `nhl_sharps.{json,md}`, `keep_list` raw data | Real usernames / wallet addresses. |
| `~/.nightly-line/.redact` | The redaction map itself (the strings below, `from\|to` per line). `tools/sync-from-live.sh` reads it and refuses to finish if any `from` survives in the tree. |

## Redactions inside committed files

Four strings are replaced by `tools/sync-from-live.sh` on every sync (the originals on the host are unchanged). The script greps the whole tree afterwards and exits non-zero if any survive, and also refuses any 40-hex wallet address under `results/` or `docs/`.

- The operator's email → `YOUR_EMAIL@example.com` in `alert.sh`, `alert-catalyst.sh`, `run-scan.sh`, `run-live.sh`, `run-windows.sh`, `run-weeksheet.sh` (a comment), `weekpost.py`, and `deploy/nightly-line.env.example`
- Two third-party BCC recipients → `BCC_RECIPIENT_1/2@example.com` in `alert.sh` and `deploy/nightly-line.env.example`
- The Google Drive ledger-folder ID → `YOUR_DRIVE_FOLDER_ID` in `settle.py`, `run-weeksheet.sh`, and the frozen `research/lib/settle.py`

The public X handle (`@tradesbotter`) is intentionally left in `xcard.py`.

## What *is* included from the data side

- `results/**/cuts.txt` and `strategies.txt` — aggregate tables only: counts, records, ROI, CIs, CLV. No wallet ever appears in them (the one label "trusted.json names" is a row name, not a name).
- `results/sports/probe_results.json` — games per sport-month. Counts only.
- `charts/*.png` — rendered from local data on the host; only the image leaves.
