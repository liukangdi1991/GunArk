#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
print_banner "一键安装与启动"
require_docker
ensure_deploy_layout
require_tushare_token

compose build
compose up -d

APP_PORT="$(grep -E '^APP_PORT=' deploy/.env | tail -1 | cut -d= -f2 || true)"
APP_PORT="${APP_PORT:-8818}"
echo "${APP_NAME} 已启动: http://localhost:${APP_PORT}"
