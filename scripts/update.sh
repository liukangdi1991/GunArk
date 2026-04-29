#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
print_banner "更新代码并上线"
require_docker
ensure_deploy_layout
require_tushare_token

git pull --ff-only
compose build
compose up -d
