#!/usr/bin/env bash
set -euo pipefail

RELEASE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_APP="${RELEASE_ROOT}/app"
TARGET_DIR="${1:-${HOME}/trend-radar}"

fail() {
  echo "$1" >&2
  exit 1
}

if [ ! -f "${SOURCE_APP}/trend-radar" ]; then
  fail "发布包不完整: 缺少 app/trend-radar"
fi

case "${TARGET_DIR}" in
  ""|"/"|"/root"|"/home")
    fail "目标目录不安全: ${TARGET_DIR}"
    ;;
esac

TARGET_DIR="$(mkdir -p "${TARGET_DIR}" && cd "${TARGET_DIR}" && pwd)"

MODE="install"
if [ -f "${TARGET_DIR}/.trend-radar-install" ] \
  || [ -x "${TARGET_DIR}/trend-radar" ] \
  || [ -f "${TARGET_DIR}/storage/app.db" ]; then
  MODE="upgrade"
fi

if [ -f "${TARGET_DIR}/trend-radar.pid" ]; then
  PID="$(cat "${TARGET_DIR}/trend-radar.pid" 2>/dev/null || true)"
  if [ -n "${PID}" ] && kill -0 "${PID}" >/dev/null 2>&1; then
    fail "服务仍在运行，请先执行 ${TARGET_DIR}/stop.sh"
  fi
fi

if [ "${MODE}" = "upgrade" ]; then
  find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
    ! -name "db" \
    ! -name "storage" \
    ! -name "logs" \
    ! -name "configs.json" \
    ! -name "stocklist.csv" \
    ! -name ".env" \
    -exec rm -rf {} +
fi

cp -a "${SOURCE_APP}/." "${TARGET_DIR}/"
chmod +x "${TARGET_DIR}/trend-radar" "${TARGET_DIR}"/*.sh

mkdir -p "${TARGET_DIR}/db" "${TARGET_DIR}/storage/cache" "${TARGET_DIR}/storage/objects" "${TARGET_DIR}/logs"

if [ ! -f "${TARGET_DIR}/configs.json" ]; then
  cp "${TARGET_DIR}/configs.default.json" "${TARGET_DIR}/configs.json"
fi

if [ ! -f "${TARGET_DIR}/stocklist.csv" ]; then
  cp "${TARGET_DIR}/stocklist.default.csv" "${TARGET_DIR}/stocklist.csv"
fi

if [ ! -f "${TARGET_DIR}/.env" ]; then
  cp "${TARGET_DIR}/.env.example" "${TARGET_DIR}/.env"
fi

"${TARGET_DIR}/trend-radar" --runtime-dir "${TARGET_DIR}" --init-only
printf '%s\n' "${MODE}" > "${TARGET_DIR}/.trend-radar-install"

echo "趋势雷达 ${MODE} 完成: ${TARGET_DIR}"
echo "启动: ${TARGET_DIR}/start.sh"
