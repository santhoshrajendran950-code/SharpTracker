# Deploying The Nightly Line to a server

Moves the scheduled parts off the laptop. The board still runs locally whenever
you want it — the server only owns the daily scan and the alert watcher.

## What changes vs. the Mac

| Mac | Server |
|---|---|
| launchd agents | systemd timers |
| macOS Keychain | `deploy/nightly-line.env` (chmod 600) |
| `osascript` banners | dropped — email is the only channel |
| `claude -p` + Gmail MCP | `deploy/mailer.py` → Resend or SMTP |
| every 15 min | **every 20 min** (see "Cadence" below) |

No Python packages to install. Everything is stdlib + `curl`.

## 1. Create the box

Hetzner CX22 (~€4/mo) or DigitalOcean's $6 droplet. Ubuntu 24.04. Any 1-vCPU
instance is plenty — this is I/O-bound, not CPU-bound.

## 2. Get an email key

[resend.com](https://resend.com) — free tier covers 3,000 emails/month, far more
than this sends. You'll need to verify a sending domain, or use their
`onboarding@resend.dev` sender for testing.

SMTP works too if you'd rather (any provider; set `MAIL_BACKEND=smtp`).

## 3. Copy the code up

```bash
rsync -av --exclude '*.log' --exclude '*.json' --exclude '__pycache__' \
  ~/.nightly-line/ root@YOUR_SERVER:/opt/nightly-line/
```

Bring `signals.jsonl` too if you want the ledger history to continue:

```bash
scp ~/.nightly-line/signals.jsonl root@YOUR_SERVER:/opt/nightly-line/
```

## 4. Configure

```bash
ssh root@YOUR_SERVER
cd /opt/nightly-line
cp deploy/nightly-line.env.example deploy/nightly-line.env
nano deploy/nightly-line.env      # fill in ODDS_API_KEY + RESEND_API_KEY
chmod 600 deploy/nightly-line.env
```

**Do not paste keys into a chat or commit this file anywhere.**

## 5. Install

```bash
sudo bash deploy/install.sh
```

Creates a `nightly` system user, installs the four systemd units, enables both
timers.

## 6. Verify

```bash
sudo -u nightly python3 /opt/nightly-line/oddskey.py     # key found, never printed
sudo systemctl start nightly-line-alert.service          # one manual run
journalctl -u nightly-line-alert.service -n 50 --no-pager
tail -f /opt/nightly-line/alert.log
systemctl list-timers | grep nightly-line
```

The first alert run seeds `check-alert-snapshot.json` and sends nothing — that's
correct. It only fires on positions that are *new* relative to the previous run.

## Cadence — read this

The Mac ran the alert every 15 minutes. The server unit is set to **20**
(`OnCalendar=*:0/20`) on purpose.

A sweep is ~690 wallet lookups. From a laptop running intermittently that was
fine; a server hammering it 45+ times a day, every day, is far more likely to
draw sustained rate limiting. Polymarket's limits have already moved twice on us
— once tightening to where 70% of a sweep came back 429, once relaxing.

**The guard is the failure counter.** Every report prints `N/690 wallets`. If you
start seeing `!! N FAILED` regularly, widen the interval rather than lowering
concurrency first. Change it in `deploy/systemd/nightly-line-alert.timer`, then:

```bash
sudo systemctl daemon-reload && sudo systemctl restart nightly-line-alert.timer
```

`Type=oneshot` plus `TimeoutStartSec=600` means systemd will not start a second
run while one is still going — concurrent sweeps are what produced a 261/690
failure on the Mac.

## Timezone

`install.sh` pulls in `tzdata`; set `TZ=America/New_York` in the env file so
`ACTIVE_FROM`/`ACTIVE_TO` and the 09:00 scan fire on your clock, not UTC. Confirm
with `sudo -u nightly date`.

## What stays on the laptop

`bigboard.py`, `check.py`, `books.py`, `resolve.py`, `clv.py` all still run
locally — "big board" and "check again" are unaffected. The two share a ledger
only if you sync `signals.jsonl`; otherwise they accumulate separately, which is
fine but means `resolve.py` sees different histories in each place.

If you want one ledger, run `resolve.py` and `clv.py` **on the server** and treat
that as authoritative.
