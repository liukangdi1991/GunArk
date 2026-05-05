#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${APP_ROOT}/trend-radar.pid"

if [ ! -f "${PID_FILE}" ]; then
  echo "未找到 PID 文件。"
  exit 0
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [ -z "${PID}" ] || ! kill -0 "${PID}" >/dev/null 2>&1; then
  rm -f "${PID_FILE}"
  echo "服务未运行。"
  exit 0
fi

kill "${PID}"
rm -f "${PID_FILE}"
echo "服务已停止: ${PID}"
