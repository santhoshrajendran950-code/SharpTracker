# RUNBOOK — move the public pipeline off the Mac

Everything that posts to X/Discord or emails alerts runs on the server. The Mac keeps
only the private bet-log jobs (settle, dayledger, weeksheet, weekpost) and manual
`big board`. Written 2026-10-01 after the Mac hibernated at 7:51 PM on 1% battery and
every scan was dead for 13 hours.

What moves: picks · alert · catalyst · windows · morningscan · picksresults · discover
· linearchive · xboard (18 waves → 1 timer) · xboardmlb · watchdog.   (11 timers)
Discord is OFF (2026-10-01): public channel is X; private channel is email.
What stays on the Mac: settle (Sun 23:59 weekly) · dayledger · weeksheet · weekpost (bets.jsonl + Drive)
· tail + place (Novig keys, private ledgers) · wallethist (builds from ~/nl-research; its output is
pushed to the server nightly, step 9) · slateboard (Discord only, which is off).

----------------------------------------------------------------------------------
## 1. Box  (10 min)
Hetzner CX22 (~€4/mo) or DigitalOcean $6 droplet · Ubuntu 24.04 · 1 vCPU / 2 GB is plenty.
Add your SSH key at creation. Then:

    ssh root@SERVER
    apt-get update && apt-get -y upgrade && reboot

## 2. Email key + dead-man's switch  (15 min)
resend.com → API key, verify a sending domain (add the SPF/DKIM records it shows), or use
onboarding@resend.dev to test. SMTP (any provider) works instead: MAIL_BACKEND=smtp.
Every job mails through deploy/mailer.py (via sendmail.sh), on the Mac too: there the settings
come from Keychain items under service `nightly-line-mail`, e.g.
    security add-generic-password -s nightly-line-mail -a RESEND_API_KEY -w
    security add-generic-password -s nightly-line-mail -a MAIL_FROM -w 'Nightly Line <alerts@yourdomain.com>'
Until those exist the Mac falls back to the old claude path and logs LEGACY.

healthchecks.io → new check, cron schedule `*/15 9-23 * * *`, grace 20 min, your email as the
alert. Its ping URL goes in HEALTHCHECK_URL (server env file) or, on the Mac,
    security add-generic-password -s nightly-line-healthcheck -a url -w '<ping url>'
The watchdog pings it every run; if the box dies, the pings stop and the service emails you.

## 3. Export the Mac's secrets — ON THE MAC, in Terminal, never through chat
Each prints one value; paste each into the env file in step 4 over SSH.

    security find-generic-password -s the-odds-api         -a odds          -w   # ODDS_API_KEY  (or: cat ~/.nightly-line/.odds-key)
    security find-generic-password -s nightly-line-x       -a api_key       -w   # X_API_KEY
    security find-generic-password -s nightly-line-x       -a api_secret    -w   # X_API_SECRET
    security find-generic-password -s nightly-line-x       -a access_token  -w   # X_ACCESS_TOKEN
    security find-generic-password -s nightly-line-x       -a access_secret -w   # X_ACCESS_SECRET

## 4. Copy code + state up  (ON THE MAC)
    SERVER=root@SERVER
    ssh $SERVER 'mkdir -p /opt/nightly-line'
    rsync -av --exclude '*.log' --exclude '__pycache__' --exclude '*.bak-*' \
      --exclude 'bets.jsonl' --exclude 'lines/' --exclude 'ledgers/*.md' \
      ~/.nightly-line/ $SERVER:/opt/nightly-line/
    # state that MUST come across (public record + codename continuity):
    scp ~/.nightly-line/.anon-salt ~/.nightly-line/aliases.json ~/.nightly-line/picks.jsonl \
        ~/.nightly-line/signals.jsonl ~/.nightly-line/alerts-posted.jsonl ~/.nightly-line/trusted.json \
        ~/.nightly-line/discovered.json ~/.nightly-line/check-snapshot.json \
        ~/.nightly-line/check-alert-snapshot.json ~/.nightly-line/wallethist.json \
        $SERVER:/opt/nightly-line/
    # wallethist.json is REQUIRED: without it every row has no career median, the
    # EXP-034 gate fails closed, and no pick ever qualifies.
    # bets.jsonl is excluded above on purpose. It never goes to the server.

## 5. Configure  (ON THE SERVER)
    ssh root@SERVER
    cd /opt/nightly-line
    cp deploy/nightly-line.env.example deploy/nightly-line.env
    nano deploy/nightly-line.env        # paste the step-3 values; keep DRY_RUN=1 for now
    chmod 600 deploy/nightly-line.env .anon-salt aliases.json

## 6. Install  (ON THE SERVER)
    sudo bash deploy/install.sh
Installs python3/curl/chromium/fonts, creates the `nightly` user, installs 11 service+timer
pairs from deploy/systemd/ (10), sets TZ, enables all timers. If it prints "chromium did not run
headless", install Google Chrome's .deb and set CHROME_BIN in the env file.

## 7. Verify — DRY_RUN day  (ON THE SERVER)
    sudo -u nightly bash -c 'set -a; . deploy/nightly-line.env; python3 oddskey.py'     # "key found", never printed
    sudo -u nightly bash -c 'set -a; . deploy/nightly-line.env; python3 xpost.py --check-media'
    systemctl list-timers --no-pager | grep nightly-line        # 11 timers, all with a NEXT time
    sudo -u nightly bash -c 'set -a; . deploy/nightly-line.env; echo test | python3 deploy/mailer.py "Nightly Line test"'   # "resend 200"
    sudo systemctl start nightly-line-watchdog.service          # healthchecks.io shows a ping
    sudo systemctl start nightly-line-picks.service && tail -5 picks.log
        # expect: "... · board NNs" and, if a pick qualified, "!! X post FAILED: DRY_RUN (not sent)"
    sudo systemctl start nightly-line-alert.service && tail -3 alert.log
    journalctl -u nightly-line-picks.service -n 30 --no-pager   # python tracebacks land here
Leave it 24 h. Compare the server's picks.log to the Mac's: same picks, same sizes, same
times (±1 min). DRY_RUN picks are written to the SERVER's picks.jsonl but never posted —
before cutover, re-copy picks.jsonl from the Mac (step 8b) so the server's ledger is the
real one.

## 8. Cutover  (do all of it inside ~10 minutes, in this order)
 a. ON THE MAC — stop every public job (the private ones stay):
    for j in picks picksresults alert catalyst windows morningscan discover linearchive \
             xboardmlb xboard00 xboard01 xboard02 xboard03 xboard04 xboard05 \
             xboard06 xboard07 xboard08 xboard09 xboard10 xboard11 xboard12 xboard13 \
             xboard14 xboard15 xboard16 xboard17 watchdog; do
      launchctl unload ~/Library/LaunchAgents/com.nightlyline.$j.plist; done
    launchctl list | grep nightlyline          # only the private jobs: settle, dayledger, weeksheet,
                                               # weekpost, tail, place, placesettle, wallethist, slateboard
 b. ON THE MAC — push the final state:
    scp ~/.nightly-line/picks.jsonl ~/.nightly-line/signals.jsonl ~/.nightly-line/alerts-posted.jsonl \
        ~/.nightly-line/check-snapshot.json ~/.nightly-line/check-alert-snapshot.json root@SERVER:/opt/nightly-line/
 c. ON THE SERVER — go live:
    sed -i 's/^DRY_RUN=.*/DRY_RUN=0/' /opt/nightly-line/deploy/nightly-line.env
    chown -R nightly:nightly /opt/nightly-line
    sudo systemctl start nightly-line-picks.service && tail -3 /opt/nightly-line/picks.log
One publisher at a time. If both run, every pick posts twice.

## 9. Nightly sync with the Mac
    # Mac -> server, after the 04:30 wallethist build (launchd job, 04:45). Until the
    # research corpus also lives on the server, this is the one Mac dependency left;
    # if it lapses the server keeps working on the last copy, just a staler one.
    rsync -a ~/.nightly-line/wallethist.json root@SERVER:/opt/nightly-line/
    # server -> Mac (optional, for `picks.py --record` and review.py here), 00:30:
    rsync -a root@SERVER:/opt/nightly-line/picks.jsonl root@SERVER:/opt/nightly-line/signals.jsonl \
          root@SERVER:/opt/nightly-line/shadow.jsonl ~/.nightly-line/
The server is authoritative from cutover on. Never rsync the other direction again.

## 10. Operate
 * Health: `systemctl list-timers | grep nightly` · `tail -f /opt/nightly-line/picks.log`
 * Failures: the `N/1080 wallets · !! N FAILED` header. Occasional 5–15 is API weather;
   sustained → widen timers (picks to */15) before touching CONCURRENCY in check.py.
 * Odds API: ~1,800 credits/day at this cadence — needs a paid tier.
 * Watchdog: nightly-line-watchdog.timer runs watchdog.sh every 15 min, 09–24: picks.log
   silent > 30 min, the picks timer inactive, or a failed send in mail-failed.jsonl → one
   email per 3 h, and the healthchecks ping (step 2) catches the box itself dying.
 * Shadow rules (EXP-035): shadow.jsonl fills on every pick run; `python3 shadow.py` prints
   them beside the live gate. Never posted.
 * Updating code: edit on the Mac, test, then `rsync` only *.py/*.sh (never the state files)
   and `sudo systemctl daemon-reload` if a unit changed.
 * Rollback: `systemctl disable --now 'nightly-line-*'` on the server, `launchctl load` the
   Mac plists from step 8a, copy picks.jsonl/signals.jsonl back.
