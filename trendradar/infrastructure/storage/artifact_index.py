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
BACKTEST_RESULT_NAME = "result.json"

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


@dataclass(frozen=True)
class BacktestIndex:
    """回测产物索引；summary 已就绪且新鲜（明细由引擎产出的 parquet 承担）。"""

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
        cached = _load_fresh_summary(summary_path, signature, picks_path=picks_path)
        if cached is not None:
            return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=cached)

    try:
        signals = _read_signals(selection_dir / SIGNALS_NAME)
        fields, frame = summarize_signals(signals)
        summary = {"schema_version": INDEX_SCHEMA_VERSION, "source_signature": signature, **fields}
        with INDEX_WRITE_LOCK:
            # 构建期间产物可能被删（删除与自愈并发）：此时写索引会在已删除的目录里
            # 「复活」目录树（atomic_write 会 mkdir parents）。拿锁后复核来源签名，变了就不写。
            if selection_source_signature(selection_dir) != signature:
                logger.info("selection index skipped for %s: artifact changed during build", execution_key)
                return None
            atomic_write_parquet(frame, picks_path)
            atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:  # 索引是派生物：任何失败都降级，不影响主功能
        logger.warning("selection index build failed for %s: %s", execution_key, exc)
        return None

    return SelectionIndex(picks_path=picks_path, summary_path=summary_path, summary=summary)


def backtest_source_signature(bt_dir: Path) -> dict[str, int] | None:
    """回测索引的来源签名；result.json 缺失或被并发删除返回 None。"""
    result_path = bt_dir / BACKTEST_RESULT_NAME
    try:
        if not result_path.is_file():
            return None
        return {BACKTEST_RESULT_NAME: result_path.stat().st_mtime_ns}
    except OSError:
        return None


def summarize_backtest_result(result: dict) -> dict:
    """result.json → 机器事实（窗口 / 计数 / 每策略聚合）。**回测聚合的唯一实现源**。

    presenter 的展示行与索引落盘都从这里出发，保证两条读路径逐字段一致。
    区间口径与旧实现相同：有净值曲线取曲线首尾，否则取 trades(buy/sell)、
    open_positions(buy, mark||target_sell)、skips(buy) 的 min/max。
    只存事实（wins 而非胜率、求和而非比率），比率由展示层现算。
    """
    trades = result.get("trades") or []
    skips = result.get("skips") or []
    open_positions = result.get("open_positions") or []
    equity = result.get("equity_curve") or []

    by_strategy: dict[str, dict] = {}

    def row_for(strategy_id: str) -> dict:
        return by_strategy.setdefault(
            strategy_id,
            {
                "strategy": strategy_id,
                "trade_count": 0,
                "skip_count": 0,
                "open_positions": 0,
                "wins": 0,
                "realized_profit_sum": 0.0,
                "invested_notional_sum": 0.0,
                "unrealized_pnl": 0.0,
            },
        )

    for trade in trades:
        row = row_for(str(trade.get("strategy") or "未知"))
        row["trade_count"] += 1
        row["realized_profit_sum"] += float(trade.get("profit") or 0.0)
        row["invested_notional_sum"] += float(trade.get("buy_price") or 0.0) * float(
            trade.get("shares") or 0
        )
        if float(trade.get("return_pct") or 0.0) > 0:
            row["wins"] += 1
    for skip in skips:
        row_for(str(skip.get("strategy") or "未知"))["skip_count"] += 1
    for position in open_positions:
        row = row_for(str(position.get("strategy") or "未知"))
        row["open_positions"] += 1
        row["unrealized_pnl"] += float(position.get("unrealized_pnl") or 0.0)

    if equity:
        window = (equity[0].get("date"), equity[-1].get("date"))
    else:
        dates: list[str] = []
        for trade in trades:
            dates += [d for d in (trade.get("buy_date"), trade.get("sell_date")) if d]
        for position in open_positions:
            dates += [
                d
                for d in (
                    position.get("buy_date"),
                    position.get("mark_date") or position.get("target_sell_date"),
                )
                if d
            ]
        dates += [s.get("buy_date") for s in skips if s.get("buy_date")]
        window = (min(dates), max(dates)) if dates else (None, None)

    return {
        "window_from": window[0],
        "window_to": window[1],
        "trade_count": len(trades),
        "skip_count": len(skips),
        "open_position_count": len(open_positions),
        "by_strategy": [by_strategy[key] for key in sorted(by_strategy)],
    }


def ensure_backtest_index(
    execution_key: str, bt_dir: Path, *, force: bool = False
) -> BacktestIndex | None:
    """确保回测索引存在且新鲜；语义与选股索引一致（原子写 / 失败降级 / 共用写锁）。

    从 `result.json` 构建（而非 parquet）：与旧读路径同源同算术，保证逐字段一致；
    parquet 只在明细分页时使用。
    """
    signature = backtest_source_signature(bt_dir)
    if signature is None:
        return None

    summary_path = bt_dir / INDEX_DIRNAME / SUMMARY_NAME
    if not force:
        cached = _load_fresh_summary(summary_path, signature)
        if cached is not None:
            return BacktestIndex(summary_path=summary_path, summary=cached)

    try:
        result = _read_signals(bt_dir / BACKTEST_RESULT_NAME)
        summary = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "source_signature": signature,
            **summarize_backtest_result(result),
        }
        with INDEX_WRITE_LOCK:
            # 同选股：拿锁后复核来源签名，产物已被删/已换则不写（否则会把目录复活）
            if backtest_source_signature(bt_dir) != signature:
                logger.info("backtest index skipped for %s: artifact changed during build", execution_key)
                return None
            atomic_write_text(summary_path, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:
        logger.warning("backtest index build failed for %s: %s", execution_key, exc)
        return None

    return BacktestIndex(summary_path=summary_path, summary=summary)


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
            built += _warm_selection(exec_dir)
            built += _warm_backtest(exec_dir)
        except Exception as exc:  # 预热是尽力而为：任何异常都不得逃逸（含并发删除的 TOCTOU）
            logger.warning("warm index failed for %s: %s", exec_dir.name, exc)
    if built:
        logger.info("index warm-up built %d index(es)", built)
    return built


def _warm_selection(exec_dir: Path) -> int:
    selection_dir = exec_dir / "selection"
    if (selection_dir / INDEX_DIRNAME / SUMMARY_NAME).is_file():
        return 0
    if selection_source_signature(selection_dir) is None:
        return 0
    return 1 if ensure_selection_index(exec_dir.name, selection_dir) is not None else 0


def _warm_backtest(exec_dir: Path) -> int:
    bt_dir = exec_dir / "backtest"
    if (bt_dir / INDEX_DIRNAME / SUMMARY_NAME).is_file():
        return 0
    if backtest_source_signature(bt_dir) is None:
        return 0
    return 1 if ensure_backtest_index(exec_dir.name, bt_dir) is not None else 0


def _read_signals(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_fresh_summary(
    summary_path: Path, signature: dict[str, int], *, picks_path: Path | None = None
) -> dict | None:
    """新鲜索引 = 必需文件都在 + schema 版本一致 + 来源签名逐字段一致。

    选股索引还需要 picks.parquet（`picks_path` 非空时校验）；回测索引只有 summary。
    """
    if picks_path is not None and not picks_path.is_file():
        return None
    if not summary_path.is_file():
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
