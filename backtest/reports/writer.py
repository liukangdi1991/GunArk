"""Structured backtest artifact writer for the web application."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import pandas as pd
import polars as pl

from core.runtime import runtime_root
from core.storage import AppStorage


def _sanitize_run_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name.strip()).strip("_")


def create_run_dir(
    output_root: Path,
    start: date,
    end: date,
    run_name: str | None = None,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nonce = uuid4().hex[:6]
    suffix = _sanitize_run_name(run_name) if run_name else ""
    execution_key = f"bt_{ts}_{suffix}_{nonce}" if suffix else f"bt_{ts}_{nonce}"
    run_dir = output_root / execution_key
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _to_json_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return df.to_dict(orient="records")


def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def _fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _load_stock_meta() -> dict[str, dict[str, str]]:
    stocklist = runtime_root() / "stocklist.csv"
    if not stocklist.exists():
        return {}
    try:
        df = pd.read_csv(stocklist, usecols=["symbol", "name", "industry"])
    except Exception:
        return {}
    return {
        str(row["symbol"]).zfill(6): {
            "name": str(row.get("name", "")),
            "industry": str(row.get("industry", "")),
        }
        for _, row in df.iterrows()
    }


def _with_stock_meta(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "code" not in df.columns:
        return df
    stock_meta = _load_stock_meta()
    out = df.copy()
    codes = out["code"].astype(str).str.zfill(6)
    out["code"] = codes

    if "name" not in out.columns:
        out["name"] = codes.map(lambda code: stock_meta.get(code, {}).get("name", ""))
    else:
        out["name"] = out["name"].fillna("").astype(str)
        missing = out["name"].str.len() == 0
        out.loc[missing, "name"] = codes[missing].map(lambda code: stock_meta.get(code, {}).get("name", ""))

    if "industry" not in out.columns:
        out["industry"] = codes.map(lambda code: stock_meta.get(code, {}).get("industry", ""))
    else:
        out["industry"] = out["industry"].fillna("").astype(str)
        missing = out["industry"].str.len() == 0
        out.loc[missing, "industry"] = codes[missing].map(lambda code: stock_meta.get(code, {}).get("industry", ""))
    return out


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    if df.empty:
        pl.DataFrame().write_parquet(path, compression="zstd")
        return
    pl.DataFrame(_to_json_records(df)).write_parquet(path, compression="zstd")


def _with_execution_key(df: pd.DataFrame, execution_key: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "execution_key" not in out.columns:
        out.insert(0, "execution_key", execution_key)
    if "run_id" in out.columns:
        out = out.drop(columns=["run_id"])
    return out


def _build_log_text(meta: Mapping[str, Any], summaries: list[dict[str, Any]]) -> str:
    lines = [
        "回测运行日志",
        f"execution_key: {meta.get('execution_key', '-')}",
        f"created_at: {meta.get('created_at', '-')}",
        f"range: {meta.get('from', '-')} ~ {meta.get('to', '-')}",
        f"strategies: {', '.join(str(s) for s in meta.get('strategies', []))}",
        f"capital_mode: {meta.get('capital_mode', '-')}",
        f"cash_per_trade: {_fmt_num(meta.get('cash_per_trade', 0), 0)}",
        "",
        "summary:",
    ]
    for item in summaries:
        lines.append(
            f"- {item.get('strategy', '-')}: "
            f"trades={int(float(item.get('trade_count', 0) or 0))}, "
            f"skips={int(float(item.get('skip_count', 0) or 0))}, "
            f"total_return={_fmt_num(item.get('total_return_pct', 0))}%, "
            f"max_drawdown={_fmt_num(item.get('max_drawdown_pct', 0))}%"
        )
    lines.append("")
    return "\n".join(lines)


def _collect_result_frames(
    strategy_results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    equity_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    skip_frames: list[pd.DataFrame] = []

    for strategy_name, result in strategy_results.items():
        summary = dict(result.get("summary", {}) or {})
        summary["strategy"] = strategy_name

        equity_df = result.get("daily_equity", pd.DataFrame())
        trades_df = result.get("trades", pd.DataFrame())
        skips_df = result.get("skips", pd.DataFrame())

        summary["skip_count"] = int(len(skips_df)) if isinstance(skips_df, pd.DataFrame) else 0
        summaries.append(summary)

        if isinstance(equity_df, pd.DataFrame):
            equity_frames.append(equity_df.assign(strategy=strategy_name))
        if isinstance(trades_df, pd.DataFrame):
            trade_frames.append(trades_df)
        if isinstance(skips_df, pd.DataFrame):
            skip_frames.append(skips_df)

    summaries.sort(key=lambda item: float(item.get("total_return_pct", 0.0) or 0.0), reverse=True)
    equity = pd.concat(equity_frames, ignore_index=True) if equity_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    skips = pd.concat(skip_frames, ignore_index=True) if skip_frames else pd.DataFrame()
    return summaries, equity, _with_stock_meta(trades), _with_stock_meta(skips)


def save_run_outputs(
    run_dir: Path,
    meta: dict[str, Any],
    strategy_results: Mapping[str, Mapping[str, Any]],
    storage_root: Path | None = None,
) -> Path:
    summaries, equity_df, trades_df, skips_df = _collect_result_frames(strategy_results)

    execution_key = str(meta.get("execution_key") or run_dir.parents[0].name)
    meta["execution_key"] = execution_key
    meta.pop("run_id", None)

    meta_path = run_dir / "meta.json"
    summary_path = run_dir / "summary.json"
    equity_path = run_dir / "equity.parquet"
    trades_path = run_dir / "trades.parquet"
    skips_path = run_dir / "skips.parquet"
    log_path = run_dir / "log.txt"

    _write_json(meta_path, meta)
    _write_json(summary_path, {"summary": summaries})
    _write_parquet(equity_path, _with_execution_key(equity_df, execution_key))
    _write_parquet(trades_path, _with_execution_key(trades_df, execution_key))
    _write_parquet(skips_path, _with_execution_key(skips_df, execution_key))
    log_path.write_text(_build_log_text(meta, summaries), encoding="utf-8")

    storage = AppStorage(storage_root or run_dir.parents[2])
    storage.record_backtest_result(
        execution_key=execution_key,
        meta=meta,
        summaries=summaries,
        object_dir=run_dir,
    )
    for artifact_type, path, mime_type in [
        ("meta_json", meta_path, "application/json; charset=utf-8"),
        ("summary_json", summary_path, "application/json; charset=utf-8"),
        ("equity_parquet", equity_path, "application/vnd.apache.parquet"),
        ("trades_parquet", trades_path, "application/vnd.apache.parquet"),
        ("skips_parquet", skips_path, "application/vnd.apache.parquet"),
        ("log_txt", log_path, "text/plain; charset=utf-8"),
    ]:
        storage.register_artifact(
            execution_key=execution_key,
            artifact_type=artifact_type,
            path=path,
            mime_type=mime_type,
        )

    return run_dir
