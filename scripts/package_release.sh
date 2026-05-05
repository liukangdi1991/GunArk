#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_compose.sh"

VERSION="${1:-$(date +%Y%m%d_%H%M%S)}"
RELEASE_NAME="${APP_SLUG}-${VERSION}"
BIN_DIR="${PROJECT_ROOT}/bin"
BUILD_ROOT="${PROJECT_ROOT}/.release"
STAGE_DIR="${BUILD_ROOT}/${RELEASE_NAME}"
APP_DIR="${STAGE_DIR}/app"
PYINSTALLER_DIST="${BUILD_ROOT}/pyinstaller-dist"
PYINSTALLER_BUILD="${BUILD_ROOT}/pyinstaller-build"
PYINSTALLER_SPEC="${BUILD_ROOT}/pyinstaller-spec"
ARCHIVE="${BIN_DIR}/${RELEASE_NAME}.zip"

require_command() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "未找到 ${name}，无法继续。" >&2
    exit 1
  fi
}

python_bin() {
  if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    echo "${PROJECT_ROOT}/.venv/bin/python"
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi

  echo "未找到 python，无法创建二进制发布包。" >&2
  exit 1
}

ensure_pyinstaller() {
  local py="$1"
  if ! "${py}" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "缺少 PyInstaller。请先执行: ${py} -m pip install -r requirements-build.txt" >&2
    exit 1
  fi
}

build_frontend() {
  require_command npm
  pushd "${PROJECT_ROOT}/frontend" >/dev/null
  if [ ! -d node_modules ]; then
    npm ci
  fi
  npm run build
  popd >/dev/null
}

build_binary() {
  local py="$1"
  rm -rf "${PYINSTALLER_DIST}" "${PYINSTALLER_BUILD}" "${PYINSTALLER_SPEC}"
  "${py}" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --name trend-radar \
    --distpath "${PYINSTALLER_DIST}" \
    --workpath "${PYINSTALLER_BUILD}" \
    --specpath "${PYINSTALLER_SPEC}" \
    --add-data "${PROJECT_ROOT}/frontend/dist:frontend/dist" \
    --add-data "${PROJECT_ROOT}/configs.json:." \
    --add-data "${PROJECT_ROOT}/stocklist.csv:." \
    --collect-all polars \
    --hidden-import uvicorn.loops.auto \
    --hidden-import uvicorn.protocols.http.auto \
    --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.lifespan.on \
    "${PROJECT_ROOT}/trend_radar_launcher.py"
}

copy_file() {
  local source="$1"
  local target="$2"
  mkdir -p "$(dirname "${STAGE_DIR}/${target}")"
  cp "${PROJECT_ROOT}/${source}" "${STAGE_DIR}/${target}"
}

prepare_stage() {
  rm -rf "${STAGE_DIR}"
  mkdir -p "${APP_DIR}"

  cp -a "${PYINSTALLER_DIST}/trend-radar/." "${APP_DIR}/"
  copy_file configs.json app/configs.default.json
  copy_file stocklist.csv app/stocklist.default.csv
  copy_file deploy/.env.example app/.env.example
  copy_file scripts/binary_start.sh app/start.sh
  copy_file scripts/binary_start_background.sh app/start_background.sh
  copy_file scripts/binary_stop.sh app/stop.sh
  copy_file scripts/binary_init.sh app/init.sh
  copy_file scripts/binary_install.sh install.sh
  chmod +x "${STAGE_DIR}/install.sh" "${APP_DIR}"/*.sh "${APP_DIR}/trend-radar"

  cat > "${STAGE_DIR}/README.md" <<EOF
# ${APP_NAME} ${APP_EN_NAME} 二进制发布包

安装到默认目录:

\`\`\`bash
./install.sh
\`\`\`

安装到指定目录:

\`\`\`bash
./install.sh /opt/trend-radar
\`\`\`

安装脚本会自动判断首次安装或升级。升级时会保留 \`db/\`、\`storage/\`、\`logs/\`、\`.env\`、\`configs.json\`、\`stocklist.csv\`，只替换程序文件。

启动:

\`\`\`bash
cd /opt/trend-radar
./start.sh
\`\`\`

后台启动:

\`\`\`bash
./start_background.sh
./stop.sh
\`\`\`
EOF
}

create_archive() {
  local py="$1"
  mkdir -p "${BIN_DIR}"
  "${py}" - "${STAGE_DIR}" "${ARCHIVE}" <<'PY'
from pathlib import Path
import sys
import zipfile

stage = Path(sys.argv[1]).resolve()
archive = Path(sys.argv[2]).resolve()
archive.unlink(missing_ok=True)

with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(stage.rglob("*")):
        if path.is_dir():
            continue
        arcname = (Path(stage.name) / path.relative_to(stage)).as_posix()
        info = zipfile.ZipInfo.from_file(path, arcname)
        info.external_attr = (path.stat().st_mode & 0o777) << 16
        zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)

print(archive)
PY
}

cd "${PROJECT_ROOT}"
print_banner "构建二进制 zip 发布包"

PYTHON_BIN="$(python_bin)"
ensure_pyinstaller "${PYTHON_BIN}"
build_frontend
build_binary "${PYTHON_BIN}"
prepare_stage
create_archive "${PYTHON_BIN}"
rm -rf "${STAGE_DIR}" "${PYINSTALLER_DIST}" "${PYINSTALLER_BUILD}" "${PYINSTALLER_SPEC}"
rmdir "${BUILD_ROOT}" 2>/dev/null || true

echo "发布包已生成: ${ARCHIVE}"
echo "解压后进入 ${RELEASE_NAME}，执行 ./install.sh /目标目录。"
