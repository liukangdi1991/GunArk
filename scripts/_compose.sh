#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_NAME="趋势雷达"
APP_EN_NAME="TrendRadar"
APP_SLUG="trend-radar"

print_banner() {
  local action="${1:-A股日线级高性能量化选股系统}"
  cat <<EOF
==================================================
  ${APP_NAME} ${APP_EN_NAME}
  ${action}
==================================================
EOF
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi

  echo "未找到 docker compose。请安装 Docker Compose v2，或提供 docker-compose 命令。" >&2
  exit 1
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "未找到 docker。请先安装 Docker。" >&2
    exit 1
  fi
}

require_tushare_token() {
  local env_file="${PROJECT_ROOT}/deploy/.env"

  if [ ! -f "${env_file}" ]; then
    echo "缺少 deploy/.env，请先复制 deploy/.env.example 并填写 TUSHARE_TOKEN。" >&2
    exit 1
  fi

  local token
  token="$(grep -E '^TUSHARE_TOKEN=' "${env_file}" | tail -1 | cut -d= -f2- | tr -d '[:space:]' || true)"
  if [ -z "${token}" ]; then
    echo "缺少 TUSHARE_TOKEN。请编辑 deploy/.env 后再启动。" >&2
    exit 1
  fi
}

ensure_deploy_layout() {
  mkdir -p "${PROJECT_ROOT}/deploy/data/db" "${PROJECT_ROOT}/deploy/data/storage"

  if [ ! -f "${PROJECT_ROOT}/deploy/configs.json" ]; then
    cp "${PROJECT_ROOT}/configs.json" "${PROJECT_ROOT}/deploy/configs.json"
  fi

  if [ ! -f "${PROJECT_ROOT}/deploy/stocklist.csv" ]; then
    cp "${PROJECT_ROOT}/stocklist.csv" "${PROJECT_ROOT}/deploy/stocklist.csv"
  fi

  if [ ! -f "${PROJECT_ROOT}/deploy/.env" ]; then
    cp "${PROJECT_ROOT}/deploy/.env.example" "${PROJECT_ROOT}/deploy/.env"
  fi

  copy_dir_if_empty "${PROJECT_ROOT}/db" "${PROJECT_ROOT}/deploy/data/db"
  copy_dir_if_empty "${PROJECT_ROOT}/storage" "${PROJECT_ROOT}/deploy/data/storage"
}

copy_dir_if_empty() {
  local source_dir="$1"
  local target_dir="$2"

  if [ ! -d "${source_dir}" ]; then
    return
  fi

  if find "${target_dir}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    return
  fi

  cp -a "${source_dir}/." "${target_dir}/"
}
