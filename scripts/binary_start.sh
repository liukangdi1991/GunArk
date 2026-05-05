#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "${APP_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${APP_ROOT}/.env"
  set +a
fi

if [ -z "${TUSHARE_TOKEN:-}" ]; then
  echo "缺少 TUSHARE_TOKEN。请编辑 ${APP_ROOT}/.env 后再启动。" >&2
  exit 1
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8818}"

exec "${APP_ROOT}/trend-radar" \
  --runtime-dir "${APP_ROOT}" \
  --host "${HOST}" \
  --port "${PORT}"
