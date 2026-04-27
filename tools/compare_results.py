"""比较两个选股结果 JSON（按策略逐项对比）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "results" / "signals"


def _load_result(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"结果文件格式错误: {path}")
    return data


def _resolve_file(arg: str) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    if len(arg) == 8 and arg.isdigit():
        candidate = RESULT_DIR / f"{arg}.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"找不到结果文件: {arg}")


def main() -> None:
    parser = argparse.ArgumentParser(description="比较两个选股结果 JSON")
    parser.add_argument("left", help="左侧文件路径，或日期 YYYYMMDD")
    parser.add_argument("right", help="右侧文件路径，或日期 YYYYMMDD")
    args = parser.parse_args()

    left_path = _resolve_file(args.left)
    right_path = _resolve_file(args.right)

    left = _load_result(left_path)
    right = _load_result(right_path)

    print(f"LEFT : {left_path}")
    print(f"RIGHT: {right_path}")
    print("-" * 72)

    strategies = sorted(set(left.keys()) | set(right.keys()))
    if not strategies:
        print("两个文件都没有策略数据。")
        return

    for strategy in strategies:
        left_stocks = set((left.get(strategy) or {}).get("stocks", []))
        right_stocks = set((right.get(strategy) or {}).get("stocks", []))
        only_left = sorted(left_stocks - right_stocks)
        only_right = sorted(right_stocks - left_stocks)
        same = sorted(left_stocks & right_stocks)
        status = "✅ 一致" if not only_left and not only_right else "❌ 不一致"
        print(f"\n[{strategy}] {status}")
        print(f"  LEFT 数量 : {len(left_stocks)}")
        print(f"  RIGHT数量 : {len(right_stocks)}")
        print(f"  交集数量  : {len(same)}")
        if only_left:
            print(f"  仅 LEFT  : {only_left}")
        if only_right:
            print(f"  仅 RIGHT : {only_right}")


if __name__ == "__main__":
    main()
