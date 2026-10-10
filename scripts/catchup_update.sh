#!/usr/bin/env bash
# Morning catch-up of the data published after the evening run, started by the
# quant-catchup systemd user timer (deploy/systemd/).  Safe to run by hand.
#
# The evening run (scripts/daily_update.sh, 18:17) cannot see some sources of
# its own session: SSE ETF shares are published around 23:00, SZSE ETF shares
# the next morning, and the CSI total-return indices sometimes after 18:30.
# This run fetches only the indices and ETF steps for the latest final session,
# so the ETF page shows the previous session's shares in the morning instead of
# a day (or a weekend) later.  It builds no decisions and sends nothing.
#
# Output: logs/daily/<date>-catchup.log and logs/daily/catchup.tsv (one line per
# run).  It shares the daily lock and leaves last_complete_epoch alone, so the
# evening run and its retry behave as before.  Exit code: 0 complete or skipped
# (another update running), 2 ingest partial/failed.
set -uo pipefail
export TZ=Asia/Shanghai
export TQDM_DISABLE=1

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
CONFIG="$REPO/configs/data_platform.json"
LOG_DIR="$REPO/logs/daily"
STEPS="indices,etf"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%F)-catchup.log"
note() { echo "$(date -Is) $*" >> "$LOG"; }

exec 9> "$LOG_DIR/.lock"
if ! flock -n 9; then
  note "another update is running; skipped"
  exit 0
fi

now="$(date +%s)"
note "== catch-up $STEPS (commit $(git -C "$REPO" rev-parse --short HEAD))"
"$REPO/.venv/bin/quant-data" --root "$REPO" ingest --config "$CONFIG" --steps "$STEPS" >> "$LOG" 2>&1
rc=$?
note "== end, exit $rc"

HISTORY="$LOG_DIR/catchup.tsv"
[[ -f "$HISTORY" ]] || printf 'started\texit\trun_id\tstatus\tlatest_session\tblocking\twarning\n' > "$HISTORY"
"$PYTHON" - "$REPO/data/manifests" "$(date -d "@$now" -Is)" "$rc" >> "$HISTORY" 2>> "$LOG" << 'EOF'
import json, sys
from pathlib import Path
manifests = sorted(Path(sys.argv[1]).glob("*.json"))
m = json.loads(manifests[-1].read_text(encoding="utf-8")) if manifests else {}
q = m.get("quality_summary") or {}
print("\t".join(str(v) for v in (sys.argv[2], sys.argv[3], m.get("run_id"), m.get("status"),
                                 m.get("expected_latest_date"), q.get("blocking"), q.get("warning"))))
EOF

# A failed catch-up is caught up by the evening run; it only becomes a warning
# in the notification centre (sent with the evening digest).
APP="$REPO/.venv/bin/quant-app"
if (( rc != 0 )) && [[ -x "$APP" ]]; then
  "$APP" --root "$REPO" event add --level warning --category data --id "catchup-$(date +%F)-$now" \
    --title "早上补抓 ETF 份额和指数未完成（退出码 $rc）" --body "日志：logs/daily/$(date +%F)-catchup.log" \
    --hint "傍晚的每日流程会再抓一次" >> "$LOG" 2>&1
fi
exit "$rc"
