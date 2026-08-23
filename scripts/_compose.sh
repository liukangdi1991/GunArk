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
  local env_args=()
  if [ -f "${PROJECT_ROOT}/deploy/.env" ]; then
    env_args=(--env-file "${PROJECT_ROOT}/deploy/.env")
  fi

  if docker compose version >/dev/null 2>&1; then
    docker compose "${env_args[@]}" "$@"
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "${env_args[@]}" "$@"
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

tag_built_image() {
  # 给刚构建的镜像打版本标签，支持回滚：docker tag trend-radar:2.0.0-<oldsha> trend-radar:latest && docker compose up -d
  local sha
  sha="$(git -C "${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
  docker tag "${APP_SLUG}:latest" "${APP_SLUG}:2.0.0-${sha}"
  echo "镜像版本标签: ${APP_SLUG}:2.0.0-${sha}"
}
wait_for_app_container() {
  local attempt
  for attempt in {1..60}; do
    if compose exec -T trend-radar python -c "print('ready')" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done

  echo "容器启动超时，请使用 ./scripts/logs.sh 查看日志。" >&2
  compose ps >&2 || true
  exit 1
}

initialize_storage_schema() {
  wait_for_app_container
  compose exec -T trend-radar python -c "
from trendradar.infrastructure.runtime import runtime_root
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
storage = runtime_root() / 'storage'
storage.mkdir(parents=True, exist_ok=True)
init_schema(StorageConnection(storage).connect())
print('SQLite 初始化完成')
"
}

ensure_deploy_layout() {
  mkdir -p \
    "${PROJECT_ROOT}/deploy/data/storage/market/bars" \
    "${PROJECT_ROOT}/deploy/data/storage/objects/jobs" \
    "${PROJECT_ROOT}/deploy/data/storage/cache"

  if [ ! -f "${PROJECT_ROOT}/deploy/.env" ]; then
    cp "${PROJECT_ROOT}/deploy/.env.example" "${PROJECT_ROOT}/deploy/.env"
  fi
}
