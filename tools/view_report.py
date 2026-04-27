"""回测报告查看器：输出 Web 报告入口。"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.storage import BacktestStorage


def _legacy_markdown_main() -> None:
    raise SystemExit("Markdown 报告已改为 Web 前端渲染，请使用 main() 输出 Web URL。")


def main() -> None:
    parser = argparse.ArgumentParser(description="查看回测 Web 报告入口")
    parser.add_argument("--run-id", help="指定 run_id；不传则自动选择最新 run")
    parser.add_argument("--host", default="127.0.0.1", help="Web 服务地址")
    parser.add_argument("--port", default=8818, type=int, help="Web 服务端口")
    parser.add_argument(
        "--mode",
        default="url",
        choices=["url", "browser"],
        help="查看方式：输出 URL 或打开浏览器",
    )
    parser.add_argument("--list", action="store_true", help="列出最近 20 个 run")
    args = parser.parse_args()

    storage = BacktestStorage(ROOT / "storage")
    runs = storage.list_backtest_runs(limit=20)
    if args.list:
        if not runs:
            print("未找到回测记录")
            return
        for run in runs:
            print(f"{run['run_id']}  {run['start_date']} ~ {run['end_date']}  {', '.join(run.get('strategies', []))}")
        return

    if args.run_id:
        run_id = args.run_id
        if storage.get_backtest_run(run_id) is None:
            raise SystemExit(f"未找到指定 run: {run_id}")
    else:
        if not runs:
            raise SystemExit("未找到回测记录")
        run_id = runs[0]["run_id"]

    url = f"http://{args.host}:{args.port}/#{quote(run_id)}"
    if args.mode == "browser":
        ok = webbrowser.open(url)
        if not ok:
            print(f"浏览器打开失败，请手动打开: {url}")
        return

    print(url)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
