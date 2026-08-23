#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
print_banner "重新构建并启动"
require_docker
ensure_deploy_layout
require_tushare_token
compose up -d --build
initialize_storage_schema
tag_built_image
