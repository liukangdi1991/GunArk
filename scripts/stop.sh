#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

cd "${PROJECT_ROOT}"
print_banner "停止服务"
require_docker
compose down
