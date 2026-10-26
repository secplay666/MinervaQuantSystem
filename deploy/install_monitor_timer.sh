#!/usr/bin/env bash
# Install or update the systemd user timer of the 盘中大资金监测 (quant-monitor)
# for this checkout.  Separate from install_daily_timer.sh: the monitor is
# optional.  Remove with: systemctl --user disable --now quant-monitor.timer
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$UNIT_DIR"
for unit in quant-monitor.service quant-monitor.timer; do
  sed "s|@REPO@|$REPO|g" "$REPO/deploy/systemd/$unit" > "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now quant-monitor.timer
systemctl --user list-timers quant-monitor.timer --no-pager
