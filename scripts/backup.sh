#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
ensure_deploy_layout

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/deploy/backups}"
mkdir -p "${BACKUP_DIR}"

ARCHIVE="${BACKUP_DIR}/daily-watch-$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "${ARCHIVE}" -C "${PROJECT_ROOT}/deploy" configs.json stocklist.csv data

echo "备份完成: ${ARCHIVE}"
