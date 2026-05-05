#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${APP_ROOT}/trend-radar.pid"
LOG_FILE="${APP_ROOT}/logs/server.log"

mkdir -p "${APP_ROOT}/logs"

if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [ -n "${PID}" ] && kill -0 "${PID}" >/dev/null 2>&1; then
    echo "服务已在运行: ${PID}"
    exit 0
  fi
fi

nohup "${APP_ROOT}/start.sh" >"${LOG_FILE}" 2>&1 &
echo "$!" > "${PID_FILE}"
echo "服务已后台启动: $(cat "${PID_FILE}")"
echo "日志: ${LOG_FILE}"
