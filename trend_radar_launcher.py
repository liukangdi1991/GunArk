from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


APP_NAME = "趋势雷达 TrendRadar"


def _resource_root() -> Path:
    configured = os.environ.get("TREND_RADAR_RESOURCE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(str(bundled_root)).resolve()
    return Path(__file__).resolve().parent


def _default_runtime_root() -> Path:
    configured = os.environ.get("TREND_RADAR_RUNTIME_ROOT") or os.environ.get("TREND_RADAR_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_env(runtime_root: Path) -> None:
    env_path = runtime_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _copy_default(runtime_root: Path, resource_root: Path, target_name: str, default_name: str) -> None:
    target = runtime_root / target_name
    if target.exists():
        return
    candidates = [
        runtime_root / default_name,
        resource_root / target_name,
        resource_root / default_name,
    ]
    for source in candidates:
        if source.exists() and source.resolve() != target.resolve():
            shutil.copy2(source, target)
            return


def _init_runtime(runtime_root: Path, resource_root: Path) -> None:
    os.environ["TREND_RADAR_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["TREND_RADAR_RESOURCE_ROOT"] = str(resource_root)

    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "db").mkdir(parents=True, exist_ok=True)
    (runtime_root / "storage" / "cache").mkdir(parents=True, exist_ok=True)
    (runtime_root / "storage" / "objects").mkdir(parents=True, exist_ok=True)
    (runtime_root / "logs").mkdir(parents=True, exist_ok=True)

    _copy_default(runtime_root, resource_root, "configs.json", "configs.default.json")
    _copy_default(runtime_root, resource_root, "stocklist.csv", "stocklist.default.csv")
    _load_env(runtime_root)

    from core.storage import AppStorage

    AppStorage(runtime_root / "storage").ensure_ready()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Web 服务")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--runtime-dir", default=None, help="运行数据目录，默认使用可执行文件所在目录")
    parser.add_argument("--init-only", action="store_true", help="仅初始化目录和 SQLite 表，不启动服务")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    resource_root = _resource_root()
    runtime_root = Path(args.runtime_dir).expanduser().resolve() if args.runtime_dir else _default_runtime_root()
    _init_runtime(runtime_root, resource_root)

    if args.init_only:
        print(f"{APP_NAME} 初始化完成: {runtime_root}")
        return

    if not os.environ.get("TUSHARE_TOKEN", "").strip():
        raise SystemExit(f"缺少 TUSHARE_TOKEN。请编辑 {runtime_root / '.env'} 后再启动。")

    import uvicorn
    from web.app import app

    host = args.host or os.environ.get("APP_HOST", "0.0.0.0")
    port = args.port or int(os.environ.get("APP_PORT", "8818"))

    print(f"{APP_NAME} 启动中: http://{host}:{port}")
    print(f"runtime: {runtime_root}")
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
