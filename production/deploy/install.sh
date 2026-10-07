#!/bin/bash
# Run as root on a fresh Ubuntu/Debian box.
#   curl -fsSL <this file> | bash     — or just: sudo bash deploy/install.sh
set -euo pipefail

APP=/opt/nightly-line
echo "==> packages"
apt-get update -qq
apt-get install -y -qq python3 curl ca-certificates tzdata >/dev/null

echo "==> user + directory"
id -u nightly >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin nightly
mkdir -p "$APP"
chown -R nightly:nightly "$APP"

echo "==> systemd units"
install -m644 "$APP"/deploy/systemd/*.service /etc/systemd/system/
install -m644 "$APP"/deploy/systemd/*.timer   /etc/systemd/system/
systemctl daemon-reload

if [ ! -f "$APP/deploy/nightly-line.env" ]; then
  echo "!! $APP/deploy/nightly-line.env is missing."
  echo "   cp deploy/nightly-line.env.example deploy/nightly-line.env, fill it in, chmod 600."
  exit 1
fi
chown nightly:nightly "$APP/deploy/nightly-line.env"
chmod 600 "$APP/deploy/nightly-line.env"

echo "==> enabling timers"
systemctl enable --now nightly-line-alert.timer nightly-line-scan.timer
systemctl list-timers --no-pager | grep nightly-line || true

echo
echo "Done. Verify with:"
echo "  sudo -u nightly python3 $APP/oddskey.py          # key resolves, never printed"
echo "  sudo systemctl start nightly-line-alert.service  # one manual run"
echo "  journalctl -u nightly-line-alert.service -n 50 --no-pager"
echo "  tail -f $APP/alert.log"
