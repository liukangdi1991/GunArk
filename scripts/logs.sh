#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
print_banner "查看服务日志"
require_docker
compose logs -f --tail=200
