"""产物索引：把大 JSON 产物物化成可分页的列式文件 + 小摘要。

分页/列表端点若每次整份解析 signals.json（最大 13MB）/result.json（最大 16MB），
成本是 O(产物字节)。索引把派生数据落到产物目录下的隐藏子目录（不污染产物清单——
产物目录的列举只收文件，子目录天然被跳过）：

    <产物目录>/.index/picks.parquet   strategy,date,code（按固有全序排序）
    <产物目录>/.index/summary.json    计数与窗口（列表/KPI 用，μs 级读取）

- 新作业落盘后调用 ``ensure_*``（幂等）；启动时 ``warm_indexes`` 后台补齐存量；
- 新鲜度由 ``source_signature``（来源文件 → mtime_ns 字典）判定，任一不匹配即重建；
- 生成失败（只读盘、源损坏等）返回 None，调用方降级直读 JSON——绝不因索引问题 5xx。

并发：写入一律 tmpfile + os.replace；两个请求同时生成时内容相同、后者覆盖前者，
读侧永远只见完整文件。写序为 parquet 先、summary 后（summary 充当提交标记）。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from trendradar.infrastructure.filesystem.atomic import atomic_write_parquet, atomic_write_text

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 2  # v2：source_signature（字段级）取代 source_mtime_ns
INDEX_DIRNAME = ".index"
SUMMARY_NAME = "summary.json"
PICKS_NAME = "picks.parquet"
SIGNALS_NAME = "signals.json"

# 索引写入与产物删除互斥：删除方持锁后再 rmtree，避免「文件在遍历删除过程中被写入」
# 导致目录删不干净（删除返回 0 条、历史里删不掉）。预热/自愈都在本模块内写索引，
# 因此同一把锁即可覆盖本进程内的两条路径。
INDEX_WRITE_LOCK = threading.Lock()

_PICKS_SCHEMA: dict[str, Any] = {"strategy": pl.Utf8, "date": pl.Utf8, "code": pl.Utf8}


@dataclass(frozen=True)
class SelectionIndex:
    """选股产物索引；picks_path 与 summary 均已就绪且新鲜。"""

    picks_path: Path
    summary_path: Path
    summary: dict


def summarize_signals(signals: dict) -> tuple[dict, pl.DataFrame]:
    """signals.json → (摘要字段, picks 帧)。**聚合的单一实现源**。

    索引构建与 presenter 的回落路径都走这里，避免同一套计数/分组写两遍。
    帧按固有全序 `(strategy, date, code)` 排序；调用方只读，不应就地改。
    """
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

    fields = {
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
    return fields, frame


def selection_source_signature(selection_dir: Path) -> dict[str, int] | None:
    """选股索引的来源签名；signals.json 缺失或被并发删除返回 None（该 run 无产物）。

    注意 stat 必须在 try 内：`is_file()` 与 `stat()` 之间文件可能被删除（产物删除与
    索引预热/自愈并发），裸 stat 会抛 FileNotFoundError 逃逸到调用方。
    """
    signals_path = selection_dir / SIGNALS_NAME
    try:
        if not signals_path.is_file():
            return None
        return {SIGNALS_NAME: signals_path.stat().st_mtime_ns}
    except OSError:
        return None


def ensure_selection_index(
    execution_key: str, selection_dir: Path, *, force: bool = False
) -> SelectionIndex | None:
    """确保索引存在且新鲜。

    force=True 忽略现有 summary（读坏索引时的重建路径）。
    返回 None 的情形：源产物缺失、源损坏、索引写入失败——调用方一律降级直读。
    """
    signature = selection_source_signature(selection_dir)
    if signature is None:
        return None

    summary_path = selection_dir / INDEX_DIRNAME / SUMMARY_NAME
    picks_path = selection_dir / INDEX_DIRNAME / PICKS_NAME

    if not force:
        cached = _load_fresh_summary(summary_path, picks_path, signature)
        if cached is not None:
            return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=cached)

    try:
        signals = _read_signals(selection_dir / SIGNALS_NAME)
        fields, frame = summarize_signals(signals)
        summary = {"schema_version": INDEX_SCHEMA_VERSION, "source_signature": signature, **fields}
        with INDEX_WRITE_LOCK:
            atomic_write_parquet(frame, picks_path)
            atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:  # 索引是派生物：任何失败都降级，不影响主功能
        logger.warning("selection index build failed for %s: %s", execution_key, exc)
        return None

    return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=summary)


def warm_indexes(executions_root: Path) -> int:
    """启动预热：为存量产物补齐缺失索引，返回新建数量。

    有界（只扫一层）；失败静默（记 WARNING）——预热失败不得影响服务启动，也不得
    改变请求路径的降级语义。
    """
    if not executions_root.is_dir():
        return 0

    built = 0
    for exec_dir in sorted(executions_root.iterdir()):
        try:
            if not exec_dir.is_dir():
                continue
            selection_dir = exec_dir / "selection"
            summary_path = selection_dir / INDEX_DIRNAME / SUMMARY_NAME
            if summary_path.is_file() or selection_source_signature(selection_dir) is None:
                continue
            if ensure_selection_index(exec_dir.name, selection_dir) is not None:
                built += 1
        except Exception as exc:  # 预热是尽力而为：任何异常都不得逃逸（含并发删除的 TOCTOU）
            logger.warning("warm index failed for %s: %s", exec_dir.name, exc)
    if built:
        logger.info("index warm-up built %d selection index(es)", built)
    return built


def _read_signals(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_fresh_summary(
    summary_path: Path, picks_path: Path, signature: dict[str, int]
) -> dict | None:
    """新鲜索引 = 两文件都在 + schema 版本一致 + 来源签名逐字段一致。"""
    if not (summary_path.is_file() and picks_path.is_file()):
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if summary.get("schema_version") != INDEX_SCHEMA_VERSION:
        return None
    if summary.get("source_signature") != signature:
        return None
    return summary
