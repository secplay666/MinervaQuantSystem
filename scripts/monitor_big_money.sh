#!/usr/bin/env bash
# 盘中大资金监测 for today's session, started by the quant-monitor systemd user
# timer (deploy/systemd/, Mon-Fri 09:20).  Polls the broad-index ETFs every
# minute until just after the close and exits; a non-trading day exits at once.
# Reads the market catalog, writes data/monitor/big_money/<date>/ and alerts as
# events in the notification centre.  Takes no lock: it does not ingest.
# Log: logs/monitor/<date>.log.
set -uo pipefail
export TZ=Asia/Shanghai
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$REPO/logs/monitor"
exec "$REPO/.venv/bin/quant-app" --root "$REPO" monitor big-money >> "$REPO/logs/monitor/$(date +%F).log" 2>&1
