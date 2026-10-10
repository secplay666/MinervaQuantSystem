#!/usr/bin/env bash
# Install or update the systemd user timers of this checkout: quant-daily (the
# evening update) and quant-catchup (the morning catch-up of ETF shares and
# indices published after the evening run).
# Needs lingering for the user (sudo loginctl enable-linger <user>) so the
# timer also runs while nobody is logged in.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$UNIT_DIR"
for unit in quant-daily.service quant-daily.timer quant-catchup.service quant-catchup.timer; do
  sed "s|@REPO@|$REPO|g" "$REPO/deploy/systemd/$unit" > "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now quant-daily.timer quant-catchup.timer
loginctl show-user "$USER" -p Linger
systemctl --user list-timers quant-daily.timer quant-catchup.timer --no-pager
