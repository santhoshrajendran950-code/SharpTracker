#!/bin/bash
# Run as root on a fresh Ubuntu 24.04 box, AFTER the code is in /opt/nightly-line
# and deploy/nightly-line.env is filled in:   sudo bash /opt/nightly-line/deploy/install.sh
set -euo pipefail
APP=/opt/nightly-line

echo "==> packages (python3 + curl + chromium for the PNG cards + fonts)"
apt-get update -qq
apt-get install -y -qq python3 curl ca-certificates tzdata rsync \
  chromium fonts-liberation fonts-noto-color-emoji >/dev/null
# Ubuntu's 'chromium' is a snap shim on some images; fall back to the deb from Debian if it fails to run
if ! chromium --headless=new --disable-gpu --screenshot=/tmp/.chromium-test.png about:blank >/dev/null 2>&1; then
  echo "!! chromium did not run headless. Install chromium-browser (deb) or Google Chrome .deb, then set CHROME_BIN in the env file."
fi

echo "==> user + directory"
id -u nightly >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin nightly
mkdir -p "$APP"
chown -R nightly:nightly "$APP"
chmod +x "$APP"/*.sh "$APP"/deploy/*.sh

if [ ! -f "$APP/deploy/nightly-line.env" ]; then
  echo "!! $APP/deploy/nightly-line.env is missing."
  echo "   cp deploy/nightly-line.env.example deploy/nightly-line.env, fill it in, chmod 600."
  exit 1
fi
chown nightly:nightly "$APP/deploy/nightly-line.env"
chmod 600 "$APP/deploy/nightly-line.env"
# the codename salt + registry must be present or every public codename changes
for f in .anon-salt aliases.json; do
  [ -f "$APP/$f" ] || echo "!! $APP/$f missing — copy it from the Mac before enabling timers (see RUNBOOK step 5)"
done
chmod 600 "$APP/.anon-salt" "$APP/aliases.json" 2>/dev/null || true

echo "==> timezone"
timedatectl set-timezone America/New_York || true

echo "==> systemd units"
install -m644 "$APP"/deploy/systemd/*.service /etc/systemd/system/
install -m644 "$APP"/deploy/systemd/*.timer   /etc/systemd/system/
systemctl daemon-reload

echo "==> enabling timers"
TIMERS=$(cd "$APP/deploy/systemd" && ls *.timer | tr '\n' ' ')
# shellcheck disable=SC2086
systemctl enable --now $TIMERS
systemctl list-timers --no-pager | grep nightly-line || true

echo
echo "Done. Next: RUNBOOK.md step 7 (verify) — start with a DRY_RUN day."
