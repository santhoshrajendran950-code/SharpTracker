# deploy/

**Start with `RUNBOOK.md`** — the exact, numbered steps (box → secrets → copy → install →
DRY_RUN day → cutover → operate). Everything else here is what the runbook uses:

| File | Purpose |
|---|---|
| `RUNBOOK.md` | the procedure |
| `install.sh` | one-shot server install: packages (incl. chromium), `nightly` user, 10 systemd timers |
| `systemd/` | one `.service` + `.timer` per scheduled job (mirrors the Mac's launchd schedule) |
| `xboard-waves.sh` | replaces the 18 per-wave launchd jobs with one timer + a clock-based dispatch |
| `mailer.py` | email via Resend or SMTP; used automatically when `MAIL_BACKEND` is set |
| `nightly-line.env.example` | every credential/switch the code reads: `DRY_RUN`, `MAIL_*`, `X_*`, `ODDS_API_KEY`, `CHROME_BIN`, `ALERT_THRESHOLD` |
| `legacy/` | the Sep-14 two-timer version; superseded |

Design rules: the Mac's behaviour is unchanged unless an env switch is set; `bets.jsonl`
and the private jobs (settle, dayledger, weeksheet, weekpost) never move; one publisher at
a time (DRY_RUN until cutover); `.anon-salt` + `aliases.json` must travel or every public
codename changes.
