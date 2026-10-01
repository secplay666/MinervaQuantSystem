#!/usr/bin/env bash
# The development environment on the server: a second checkout with its own
# copy of the production data, its own business database and settings, and a
# user service on 127.0.0.1 (view it through an SSH tunnel).  Nothing here
# writes to the production checkout.
#
#   deploy/dev_env.sh install [--port 18444]   # settings file, systemd unit, start
#   deploy/dev_env.sh sync-data                # refresh data/, logs/, artifacts/ from production
#   deploy/dev_env.sh deploy BUNDLE BRANCH [WEB_TGZ] [MOBILE_TGZ]
#
# The business database (data/app) is copied once by `install` when missing
# and kept afterwards, so test users and items survive a data refresh.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD="${MINERVA_PROD_ROOT:-$HOME/L1/MinervaQuantSystem}"
ENV_FILE="${MINERVA_DEV_ENV_FILE:-$HOME/.config/minerva/dev.env}"
UNIT=minerva-dev-api.service
[[ "$(cd "$PROD" && pwd)" != "$REPO" ]] || { echo "this is the production checkout; refusing" >&2; exit 2; }

ingest_running() { pgrep -f "bin/quant-data .*ingest" > /dev/null || pgrep -f "scripts/daily_update.sh" > /dev/null; }

copy_database() {
  mkdir -p "$REPO/data/app"
  "$PROD/.venv/bin/python" - "$PROD/data/app/app.sqlite" "$REPO/data/app/app.sqlite" <<'PY'
import sqlite3, sys
source, target = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
source.backup(target)  # consistent while production is serving
target.close()
PY
}

sync_data() {
  if ingest_running; then
    echo "an ingest is running; try again when it has finished" >&2
    exit 1
  fi
  rsync -a --delete --exclude /app/ "$PROD/data/" "$REPO/data/"
  rsync -a --delete "$PROD/logs/" "$REPO/logs/"
  rsync -a --delete "$PROD/artifacts/" "$REPO/artifacts/"
  [[ -f "$REPO/data/app/app.sqlite" ]] || copy_database
  echo "data copied from $PROD ($(du -sh "$REPO/data" | cut -f1))"
}

case "${1:-}" in
  install)
    port=18444  # 8000/8443 production services, 18443 the Minerva production API
    [[ "${2:-}" == "--port" ]] && port="$3"
    if [[ ! -f "$ENV_FILE" ]]; then
      "$REPO/.venv/bin/quant-app" --env-file "$ENV_FILE" init-secret
      sed -i 's/^MINERVA_ENV=.*/MINERVA_ENV=development/' "$ENV_FILE"
    fi
    [[ -d "$REPO/data/canonical" ]] || sync_data
    [[ -f "$REPO/data/app/app.sqlite" ]] || copy_database
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$unit_dir"
    sed -e "s|@REPO@|$REPO|g" -e "s|@ENV_FILE@|$ENV_FILE|g" -e "s|@PORT@|$port|g" \
      "$REPO/deploy/systemd/$UNIT" > "$unit_dir/$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT"
    systemctl --user restart "$UNIT"
    ;;
  sync-data)
    sync_data
    systemctl --user restart "$UNIT"
    ;;
  deploy)
    bundle="$2" branch="$3"
    git -C "$REPO" fetch -q "$bundle" "+$branch:refs/remotes/bundle/$branch"
    git -C "$REPO" checkout -q -B "$branch" "refs/remotes/bundle/$branch"
    VIRTUAL_ENV="$REPO/.venv" "$HOME/.local/bin/uv" pip install -q --no-deps -e "$REPO"
    for pair in "web-antd:${4:-}" "mobile:${5:-}"; do
      app="${pair%%:*}" tarball="${pair#*:}"
      [[ -n "$tarball" ]] || continue
      rm -rf "$REPO/web/apps/$app/dist"
      tar -xzf "$tarball" -C "$REPO/web/apps/$app"
    done
    git -C "$REPO" log --oneline -1
    systemctl --user restart "$UNIT"
    ;;
  *)
    sed -n '2,13p' "$0" >&2
    exit 2
    ;;
esac
sleep 3
systemctl --user --no-pager status "$UNIT" | head -n 4
