#!/usr/bin/env bash
# Install or update the quant-api systemd user service for this checkout.
#
#   deploy/install_api_service.sh [--data-root DIR] [--port 18443]
#
# The service runs `quant-app serve` on 127.0.0.1 (HTTPS when a certificate
# exists in ~/.config/minerva/tls) and serves the built frontend from
# web/apps/web-antd/dist.  --data-root points at the checkout whose data/
# the API reads (default: this checkout).  Prerequisites, once:
#   .venv/bin/quant-app init-secret
#   .venv/bin/quant-app tls init --ip <public ip> --ip 127.0.0.1
#   .venv/bin/quant-app --root <data root> user create --username admin --role admin
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="$REPO"
PORT=18443  # 8000 and 8443 are taken by production services on the server
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-root) DATA_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[[ -x "$REPO/.venv/bin/quant-app" ]] || { echo "no $REPO/.venv/bin/quant-app; install the app extra first" >&2; exit 1; }
[[ -f "$REPO/web/apps/web-antd/dist/index.html" ]] || echo "warning: frontend not built (web/apps/web-antd/dist)" >&2

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
sed -e "s|@REPO@|$REPO|g" -e "s|@DATA_ROOT@|$DATA_ROOT|g" -e "s|@PORT@|$PORT|g" \
  "$REPO/deploy/systemd/quant-api.service" > "$UNIT_DIR/quant-api.service"
systemctl --user daemon-reload
systemctl --user enable quant-api.service
systemctl --user restart quant-api.service
sleep 3
systemctl --user --no-pager status quant-api.service | head -n 5
