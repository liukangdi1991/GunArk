#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
print_banner "备份运行数据"
ensure_deploy_layout

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/deploy/backups}"
mkdir -p "${BACKUP_DIR}"

ARCHIVE="${BACKUP_DIR}/${APP_SLUG}-$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "${ARCHIVE}" -C "${PROJECT_ROOT}/deploy" configs.json stocklist.csv data

echo "备份完成: ${ARCHIVE}"
