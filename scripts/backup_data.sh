#!/usr/bin/env bash
# Back up everything that cannot be regenerated to the second disk, as one
# dated snapshot per day.  rsync --link-dest hard-links files unchanged since
# the previous snapshot, so every snapshot is a complete, browsable copy but
# only costs what changed; deleting or corrupting a file in the checkout never
# touches an existing snapshot.
#
#   scripts/backup_data.sh          # snapshot for today (a rerun replaces it)
#
# Backed up: data/raw, manifests, reports, checkpoints, quarantine, canonical
# (restoring it avoids an 11-minute rebuild), artifacts, the business
# database data/app, and logs.  The SQLite databases (experiment registry,
# business database) are copied through SQLite's online backup API, so the
# copy is consistent even while the API is writing.  Not backed up, because they
# are regenerated: market.duckdb, features, staging, archive.
# Keeps the last KEEP_DAILY snapshots plus the first snapshot of each of the
# last KEEP_MONTHLY months.  Restore: see README (备份与恢复).
#
# Run by scripts/daily_update.sh after every ingest; safe to run by hand
# (refuses while an update is running).
set -uo pipefail
export TZ=Asia/Shanghai

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${QUANT_BACKUP_ROOT:-/mnt/data_hdd/quant/backup}"
SNAPSHOTS="$DEST/snapshots"
LOG_DIR="$REPO/logs/backup"
KEEP_DAILY=14
KEEP_MONTHLY=12
SOURCES=(data/raw data/manifests data/reports data/checkpoints data/quarantine data/canonical data/app artifacts logs)
DATABASES=(artifacts/registry.sqlite data/app/app.sqlite)

mkdir -p "$LOG_DIR" "$REPO/logs/daily"
fail() { echo "backup failed: $*" >&2; exit 1; }

if [[ -z "${QUANT_UPDATE_LOCKED:-}" ]]; then  # daily_update.sh already holds the lock
  exec 9> "$REPO/logs/daily/.lock"
  flock -n 9 || fail "an update is running; try again later"
fi
[[ -d "$DEST" ]] || fail "$DEST does not exist (is the backup disk mounted?)"
[[ "$(stat -c %d "$DEST")" != "$(stat -c %d "$REPO")" ]] || fail "$DEST is on the same disk as $REPO"
mkdir -p "$SNAPSHOTS"

started=$(date +%s)
today=$(date +%F)
target="$SNAPSHOTS/$today"
partial="$SNAPSHOTS/.$today.partial"
list_snapshots() {
  find "$SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
    -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}' -printf '%f\n' | sort
}
before=$(list_snapshots | awk -v today="$today" '$0 < today' | tail -n 1)  # last day before today
base=$before
[[ -d "$target" ]] && base=$today  # a rerun links against this morning's snapshot
link=()
[[ -n "$base" ]] && link=(--link-dest="$SNAPSHOTS/$base")

cd "$REPO" || fail "cannot enter $REPO"
present=()
for source in "${SOURCES[@]}"; do [[ -e "$source" ]] && present+=("$source"); done
excludes=()
for database in "${DATABASES[@]}"; do excludes+=(--exclude="/$database*"); done
rsync -a --relative "${link[@]}" "${excludes[@]}" "${present[@]}" "$partial/" || fail "rsync exited $?"
# Nothing may be left to transfer: the snapshot equals the checkout right now.
pending=$(rsync -a --relative --dry-run --itemize-changes "${excludes[@]}" "${present[@]}" "$partial/")
[[ -z "$pending" ]] || fail "snapshot differs from the checkout: $(head -n 3 <<< "$pending")"
for database in "${DATABASES[@]}"; do  # consistent copies even while being written
  [[ -f "$database" ]] || continue
  mkdir -p "$partial/$(dirname "$database")"
  "$REPO/.venv/bin/python" -c 'import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)' "$database" "$partial/$database" || fail "backup of $database failed"
done

if [[ -d "$target" ]]; then
  mv "$target" "$SNAPSHOTS/.$today.old" && rm -rf "$SNAPSHOTS/.$today.old"
fi
mv "$partial" "$target" || fail "cannot move $partial into place"
ln -sfn "$today" "$SNAPSHOTS/latest"

# Retention: last KEEP_DAILY snapshots, plus the first of each of the last KEEP_MONTHLY months.
mapfile -t all < <(list_snapshots)
declare -A keep=()
start=$(( ${#all[@]} > KEEP_DAILY ? ${#all[@]} - KEEP_DAILY : 0 ))
for name in "${all[@]:start}"; do keep[$name]=1; done
declare -A first_of_month=()
for name in "${all[@]}"; do [[ -n "${first_of_month[${name:0:7}]:-}" ]] || first_of_month[${name:0:7}]=$name; done
for month in $(printf '%s\n' "${!first_of_month[@]}" | sort | tail -n "$KEEP_MONTHLY"); do
  keep[${first_of_month[$month]}]=1
done
removed=0
for name in "${all[@]}"; do
  if [[ -z "${keep[$name]:-}" ]]; then rm -rf "${SNAPSHOTS:?}/$name" && removed=$((removed + 1)); fi
done

# du counts hard links once across its arguments: the second figure is what today added.
if [[ -n "$before" && -d "$SNAPSHOTS/$before" ]]; then
  added=$(du -sm "$SNAPSHOTS/$before" "$target" | awk 'NR == 2 {print $1}')
else
  added=$(du -sm "$target" | awk '{print $1}')
fi
files=$(find "$target" -type f | wc -l)
seconds=$(( $(date +%s) - started ))
HISTORY="$LOG_DIR/history.tsv"
[[ -f "$HISTORY" ]] || printf 'finished\tsnapshot\tfiles\tadded_mb\tseconds\tremoved\n' > "$HISTORY"
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -Is)" "$today" "$files" "$added" "$seconds" "$removed" >> "$HISTORY"
echo "backup ok: $target ($files files, +${added} MB, ${seconds} s, removed $removed old snapshots)"
