#!/usr/bin/env bash
# Install or update the quant-daily systemd user timer for this checkout.
# Needs lingering for the user (sudo loginctl enable-linger <user>) so the
# timer also runs while nobody is logged in.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$UNIT_DIR"
for unit in quant-daily.service quant-daily.timer; do
  sed "s|@REPO@|$REPO|g" "$REPO/deploy/systemd/$unit" > "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now quant-daily.timer
loginctl show-user "$USER" -p Linger
systemctl --user list-timers quant-daily.timer --no-pager
