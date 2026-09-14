"""产物索引：把大 JSON 产物物化成可分页的列式文件 + 小摘要。

分页/列表端点若每次整份解析 signals.json（最大 13MB）/result.json（最大 16MB），
成本是 O(产物字节)。索引把派生数据落到同目录：

    selection/picks.parquet   strategy,date,code（按固有全序排序）
    selection/summary.json    计数与窗口（列表/KPI 用，μs 级读取）

- 新作业落盘后调用 ``ensure_*``（幂等）；
- 存量产物首访自愈一次（summary 内 ``source_mtime_ns`` 校验源产物是否变过）；
- 生成失败（只读盘、源损坏等）返回 None，调用方降级直读 JSON——绝不因索引问题 5xx。

并发：写入一律 tmpfile + os.replace；两个请求同时生成时内容相同、后者覆盖前者，
读侧永远只见完整文件。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from trendradar.infrastructure.filesystem.atomic import atomic_write_parquet, atomic_write_text

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1
SUMMARY_NAME = "summary.json"
PICKS_NAME = "picks.parquet"

_PICKS_SCHEMA: dict[str, Any] = {"strategy": pl.Utf8, "date": pl.Utf8, "code": pl.Utf8}


@dataclass(frozen=True)
class SelectionIndex:
    """选股产物索引；picks_path 与 summary 均已就绪且新鲜。"""

    picks_path: Path
    summary_path: Path
    summary: dict


def ensure_selection_index(execution_key: str, selection_dir: Path) -> SelectionIndex | None:
    """确保索引存在且新鲜。

    返回 None 的情形：源产物缺失、源损坏、索引写入失败——调用方一律降级直读。
    """
    signals_path = selection_dir / "signals.json"
    if not signals_path.is_file():
        return None

    summary_path = selection_dir / SUMMARY_NAME
    picks_path = selection_dir / PICKS_NAME
    source_mtime_ns = signals_path.stat().st_mtime_ns

    cached = _load_fresh_summary(summary_path, picks_path, source_mtime_ns)
    if cached is not None:
        return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=cached)

    try:
        signals = _read_signals(signals_path)
    except Exception as exc:
        logger.warning("selection index: signals.json unreadable for %s: %s", execution_key, exc)
        return None

    try:
        summary, frame = _summary_from_signals(signals, source_mtime_ns)
        atomic_write_parquet(frame, picks_path)
        atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:  # 索引是派生物：任何写入失败都降级，不影响主功能
        logger.warning("selection index build failed for %s: %s", execution_key, exc)
        return None

    return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=summary)


def _read_signals(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_from_signals(signals: dict, source_mtime_ns: int) -> tuple[dict, pl.DataFrame]:
    """由 signals.json 计算摘要与 picks 帧（帧按固有全序 strategy→date→code 排序）。"""
    by_key: dict[tuple[str, str], dict] = {}
    rows: list[tuple[str, str, str]] = []
    for sig in signals.get("signals", []):
        name = sig.get("strategy_name") or sig.get("strategy_id") or "未知"
        day = sig.get("signal_date") or ""
        codes = [str(c) for c in (sig.get("codes") or [])]
        rows.extend((name, day, code) for code in codes)
        row = by_key.setdefault(
            (name, day), {"strategy": name, "date": day, "count": 0, "elapsed_seconds": 0.0}
        )
        row["count"] += len(codes)
        row["elapsed_seconds"] += sig.get("elapsed_seconds") or 0.0

    if rows:
        frame = pl.DataFrame(rows, schema=list(_PICKS_SCHEMA), orient="row").sort(
            ["strategy", "date", "code"]
        )
        total_picks = frame.height
        unique_codes = int(frame["code"].n_unique())
        trade_days = int(frame["date"].n_unique())
    else:
        frame = pl.DataFrame(schema=_PICKS_SCHEMA)
        total_picks = unique_codes = trade_days = 0

    summary = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "source_mtime_ns": source_mtime_ns,
        "total_picks": total_picks,
        "unique_codes": unique_codes,
        "trade_days": trade_days,
        "signal_from": signals.get("signal_from"),
        "signal_to": signals.get("signal_to"),
        "by_strategy_date": [
            {**row, "elapsed_seconds": round(row["elapsed_seconds"], 3)}
            for row in (by_key[key] for key in sorted(by_key))
        ],
    }
    return summary, frame


def _load_fresh_summary(
    summary_path: Path, picks_path: Path, source_mtime_ns: int
) -> dict | None:
    """新鲜索引 = 两文件都在 + schema 版本一致 + 源 mtime 未变。"""
    if not (summary_path.is_file() and picks_path.is_file()):
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if summary.get("schema_version") != INDEX_SCHEMA_VERSION:
        return None
    if summary.get("source_mtime_ns") != source_mtime_ns:
        return None
    return summary
