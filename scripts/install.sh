#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
require_docker
ensure_deploy_layout

if ! grep -Eq '^TUSHARE_TOKEN=.+$' deploy/.env; then
  echo "提示：deploy/.env 中 TUSHARE_TOKEN 为空。服务可以启动，但行情拉取会失败。" >&2
fi

compose build
compose up -d

APP_PORT="$(grep -E '^APP_PORT=' deploy/.env | tail -1 | cut -d= -f2 || true)"
APP_PORT="${APP_PORT:-8818}"
echo "日线观势已启动: http://localhost:${APP_PORT}"
