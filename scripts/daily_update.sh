#!/usr/bin/env bash
# Daily incremental data update on the server, started by the quant-daily
# systemd user timer (deploy/systemd/).  Safe to run by hand:
#
#   scripts/daily_update.sh            # skipped when the post-close data is already in
#   scripts/daily_update.sh --force    # run anyway
#
# The timer fires twice on weekdays; the later firing is a retry and exits at
# once when an earlier run completed after the most recent session close
# (session_final_time in configs/data_platform.json).  One run at a time.
#
# Output: logs/daily/<date>.log (full log) and logs/daily/history.tsv (one line
# per run).  After the ingest, scripts/backup_data.sh snapshots the data to the
# backup disk.  Exit code: 0 complete or skipped, 2 partial/failed (see the
# quality report named in the log), 3 complete but the backup failed.
set -uo pipefail
export TZ=Asia/Shanghai
export TQDM_DISABLE=1   # AKShare's per-request progress bars would flood the log

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
CONFIG="$REPO/configs/data_platform.json"
LOG_DIR="$REPO/logs/daily"
STATE="$LOG_DIR/last_complete_epoch"   # start time of the last complete run
KEEP_DAYS=180

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%F).log"
note() { echo "$(date -Is) $*" >> "$LOG"; }

exec 9> "$LOG_DIR/.lock"
if ! flock -n 9; then
  note "another update is running; skipped"
  exit 0
fi

final_time="$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["session_final_time"])' "$CONFIG")"
now="$(date +%s)"
cutoff="$(date -d "today $final_time" +%s)"
(( now < cutoff )) && cutoff="$(date -d "yesterday $final_time" +%s)"
last="$(cat "$STATE" 2> /dev/null || echo 0)"
if [[ "${1:-}" != "--force" ]] && (( last >= cutoff )); then
  note "a complete run started after the last close ($(date -d "@$last" -Is)); skipped"
  exit 0
fi

note "== start (commit $(git -C "$REPO" rev-parse --short HEAD))"
"$REPO/.venv/bin/quant-data" --root "$REPO" ingest --config "$CONFIG" >> "$LOG" 2>&1
rc=$?
note "== end, exit $rc"
[[ $rc -eq 0 ]] && echo "$now" > "$STATE"

# One summary line per run, from the newest manifest.
HISTORY="$LOG_DIR/history.tsv"
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

find "$LOG_DIR" -name '*.log' -mtime +"$KEEP_DAYS" -delete

# Snapshot to the backup disk after every ingest that ran, complete or not: raw
# responses of a partial run are worth keeping too.  A missed backup is caught
# up by the next one (every snapshot is complete).
note "== backup"
QUANT_UPDATE_LOCKED=1 "$REPO/scripts/backup_data.sh" >> "$LOG" 2>&1
backup_rc=$?
note "== backup exit $backup_rc"
(( rc == 0 && backup_rc != 0 )) && rc=3
exit "$rc"
