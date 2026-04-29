from __future__ import annotations

import json
import re
from functools import lru_cache
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Mapping

import pandas as pd
import polars as pl
from rich.console import Console
from rich.panel import Panel

from backtest.reports.console import print_strategy_report, print_summary
from backtest.storage import BacktestStorage


def _sanitize_run_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name.strip()).strip("_")


def create_run_dir(
    output_root: Path,
    start: date,
    end: date,
    run_name: str | None = None,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if run_name:
        suffix = _sanitize_run_name(run_name)
        run_id = f"{ts}_{suffix}" if suffix else ts
    else:
        run_id = f"{ts}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    run_dir = output_root / run_id
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


def _safe_md_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    return text.replace("|", "\\|").replace("\n", " ")


def _humanize_indicator_terms(value: Any) -> Any:
    """仅用于报告展示：把内部英文指标名转成中文术语。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[_humanize_indicator_terms(k)] = _humanize_indicator_terms(v)
        return out
    if isinstance(value, list):
        return [_humanize_indicator_terms(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_humanize_indicator_terms(v) for v in value)
    if isinstance(value, str):
        return (
            value.replace("SHORT_TERM_TREND_LINE", "短期趋势线")
            .replace("short_term_trend_line", "短期趋势线")
            .replace("LONG_TERM_BULL_BEAR_LINE", "长期多空线")
            .replace("long_term_bull_bear_line", "长期多空线")
        )
    return value


def _load_stock_names() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    stocklist = root / "stocklist.csv"
    if not stocklist.exists():
        return {}
    try:
        df = pd.read_csv(stocklist, usecols=["symbol", "name"])
    except Exception:
        return {}
    return {str(row["symbol"]).zfill(6): str(row["name"]) for _, row in df.iterrows()}


def _load_stock_meta() -> dict[str, dict[str, str]]:
    root = Path(__file__).resolve().parents[2]
    stocklist = root / "stocklist.csv"
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


@lru_cache(maxsize=1)
def _load_strategy_meta() -> dict[str, dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    cfg_path = root / "configs.json"
    if not cfg_path.exists():
        return {}
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    selectors = payload.get("selectors", []) if isinstance(payload, dict) else []
    meta: dict[str, dict[str, Any]] = {}
    for item in selectors:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias", "")).strip()
        if not alias:
            continue
        meta[alias] = {
            "class": str(item.get("class", "")).strip(),
            "comment": str(item.get("_comment", "")).strip(),
            "params": item.get("params", {}),
        }
    return meta


def _build_strategy_details_markdown(
    summaries: list[dict[str, Any]],
    strategy_results: Mapping[str, Dict[str, Any]],
) -> str:
    stock_names = _load_stock_names()
    strategy_meta = _load_strategy_meta()
    lines: list[str] = []
    lines.append("## 各策略详细报告")
    lines.append("")

    for s in summaries:
        strategy = str(s.get("strategy", "-"))
        result = strategy_results.get(strategy, {})
        trades_df = result.get("trades", pd.DataFrame())
        skips_df = result.get("skips", pd.DataFrame())
        if not isinstance(trades_df, pd.DataFrame):
            trades_df = pd.DataFrame()
        if not isinstance(skips_df, pd.DataFrame):
            skips_df = pd.DataFrame()

        lines.append(f"### 🎯 {strategy}")
        lines.append("")
        meta = strategy_meta.get(strategy, {})
        strategy_class = str(meta.get("class", "")).strip() or "-"
        strategy_logic = str(meta.get("comment", "")).strip() or "未在 configs.json 中配置策略说明。"
        strategy_logic = _humanize_indicator_terms(strategy_logic)
        strategy_params = meta.get("params", {})

        lines.append("#### 策略逻辑")
        lines.append("")
        lines.append("| 策略 | 类名 | 逻辑说明 |")
        lines.append("|---|---|---|")
        lines.append(f"| {_safe_md_cell(strategy)} | {_safe_md_cell(strategy_class)} | {_safe_md_cell(strategy_logic)} |")
        lines.append("")
        lines.append("#### 选股条件参数")
        lines.append("")
        lines.append("```json")
        try:
            display_params = _humanize_indicator_terms(strategy_params)
            lines.append(json.dumps(display_params, ensure_ascii=False, indent=2, sort_keys=True))
        except Exception:
            lines.append("{}")
        lines.append("```")
        lines.append("")

        lines.append("#### 回测指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---:|")
        lines.append(f"| 交易笔数 | {int(float(s.get('trade_count', 0) or 0))} |")
        lines.append(f"| 胜率 | {_fmt_num(s.get('win_rate_pct', 0))}% |")
        lines.append(f"| 总收益 | {_fmt_num(s.get('total_return_pct', 0))}% |")
        lines.append(f"| 年化收益 | {_fmt_num(s.get('annual_return_pct', 0))}% |")
        lines.append(f"| 最大回撤 | {_fmt_num(s.get('max_drawdown_pct', 0))}% |")
        lines.append(f"| Sharpe | {_fmt_num(s.get('sharpe', 0), 3)} |")
        lines.append(f"| 最终现金 | {_fmt_num(s.get('final_cash', 0), 2)} |")
        lines.append(f"| 未平仓数 | {int(float(s.get('open_positions', 0) or 0))} |")
        lines.append(
            f"| 盈利/亏损笔数 | "
            f"{int(round((float(s.get('trade_count', 0) or 0) * float(s.get('win_rate_pct', 0) or 0)) / 100.0))}"
            f" / "
            f"{int(float(s.get('trade_count', 0) or 0)) - int(round((float(s.get('trade_count', 0) or 0) * float(s.get('win_rate_pct', 0) or 0)) / 100.0))} |"
        )
        lines.append("")

        lines.append("#### 交易明细")
        lines.append("")
        if trades_df.empty:
            lines.append("无成交记录。")
            lines.append("")
        else:
            cols = [
                "code",
                "signal_date",
                "buy_date",
                "buy_price",
                "sell_date",
                "sell_price",
                "shares",
                "profit",
                "return_pct",
                "sell_postpone_days",
            ]
            avail_cols = [c for c in cols if c in trades_df.columns]
            show = trades_df[avail_cols].copy()
            if "code" in show.columns:
                show["code"] = show["code"].astype(str).str.zfill(6)
                show.insert(1, "name", show["code"].map(lambda x: stock_names.get(x, "")))
            sort_cols = [c for c in ["sell_date", "buy_date", "code"] if c in show.columns]
            if sort_cols:
                show = show.sort_values(sort_cols)

            lines.append("| 代码 | 名称 | 信号日 | 买入日 | 买入价 | 卖出日 | 卖出价 | 股数 | 盈亏 | 收益率% | 备注 |")
            lines.append("|---|---|---|---|---:|---|---:|---:|---:|---:|---:|")
            for _, row in show.iterrows():
                profit = float(row.get("profit", 0) or 0)
                profit_view = f"{profit:+.2f}"
                postpone_days = int(float(row.get("sell_postpone_days", 0) or 0))
                remark = f"延迟卖出{postpone_days}天" if postpone_days > 0 else ""
                lines.append(
                    f"| {_safe_md_cell(row.get('code', ''))} | "
                    f"{_safe_md_cell(row.get('name', ''))} | "
                    f"{_safe_md_cell(row.get('signal_date', ''))} | "
                    f"{_safe_md_cell(row.get('buy_date', ''))} | "
                    f"{_fmt_num(row.get('buy_price', 0), 2)} | "
                    f"{_safe_md_cell(row.get('sell_date', ''))} | "
                    f"{_fmt_num(row.get('sell_price', 0), 2)} | "
                    f"{int(float(row.get('shares', 0) or 0))} | "
                    f"{profit_view} | "
                    f"{_fmt_num(row.get('return_pct', 0), 2)} | "
                    f"{_safe_md_cell(remark)} |"
                )
            lines.append("")

        if not skips_df.empty:
            lines.append("#### 跳过记录")
            lines.append("")
            show_cols = [c for c in ["code", "signal_date", "buy_date", "reason"] if c in skips_df.columns]
            show = skips_df[show_cols].copy()
            if "code" in show.columns:
                show["code"] = show["code"].astype(str).str.zfill(6)
                show.insert(1, "name", show["code"].map(lambda x: stock_names.get(x, "")))
            sort_cols = [c for c in ["signal_date", "code"] if c in show.columns]
            if sort_cols:
                show = show.sort_values(sort_cols)

            lines.append("| 代码 | 名称 | 选出时间 | 买入时间 | 跳过原因 |")
            lines.append("|---|---|---|---|---|")
            for _, row in show.iterrows():
                lines.append(
                    f"| {_safe_md_cell(row.get('code', ''))} | "
                    f"{_safe_md_cell(row.get('name', ''))} | "
                    f"{_safe_md_cell(row.get('signal_date', ''))} | "
                    f"{_safe_md_cell(row.get('buy_date', ''))} | "
                    f"{_safe_md_cell(row.get('reason', ''))} |"
                )
            lines.append("")

    return "\n".join(lines)


def _build_summary_markdown(
    meta: Mapping[str, Any],
    summaries: list[dict[str, Any]],
    strategy_results: Mapping[str, Dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# 回测结果概览")
    lines.append("")
    lines.append("## 参数")
    lines.append("")
    lines.append("| 参数 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 区间 | {meta.get('from', '-')} ~ {meta.get('to', '-')} |")
    lines.append(f"| 策略数 | {len(meta.get('strategies', []))} |")
    lines.append(f"| 资金模式 | {meta.get('capital_mode', '-')} |")
    lines.append(f"| 每票金额 | {_fmt_num(meta.get('cash_per_trade', 0), 0)} |")
    lines.append("")
    lines.append("## 策略汇总")
    lines.append("")
    lines.append("| 策略 | 交易数 | 胜率% | 总收益% | 年化% | 最大回撤% | Sharpe |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for s in summaries:
        lines.append(
            f"| {s.get('strategy', '-')} | "
            f"{int(float(s.get('trade_count', 0) or 0))} | "
            f"{_fmt_num(s.get('win_rate_pct', 0))} | "
            f"{_fmt_num(s.get('total_return_pct', 0))} | "
            f"{_fmt_num(s.get('annual_return_pct', 0))} | "
            f"{_fmt_num(s.get('max_drawdown_pct', 0))} | "
            f"{_fmt_num(s.get('sharpe', 0), 3)} |"
        )
    lines.append("")

    if summaries:
        best = summaries[0]
        worst = min(summaries, key=lambda x: float(x.get("total_return_pct", 0.0) or 0.0))
        lines.append("## 关键结论")
        lines.append("")
        lines.append(
            f"- 最优策略: **{best.get('strategy', '-')}** "
            f"(总收益 **{_fmt_num(best.get('total_return_pct', 0))}%**, "
            f"最大回撤 {_fmt_num(best.get('max_drawdown_pct', 0))}%)"
        )
        lines.append(
            f"- 最弱策略: **{worst.get('strategy', '-')}** "
            f"(总收益 **{_fmt_num(worst.get('total_return_pct', 0))}%**, "
            f"最大回撤 {_fmt_num(worst.get('max_drawdown_pct', 0))}%)"
        )
        lines.append("")

        top_n = summaries[: min(3, len(summaries))]
        bottom_n = sorted(
            summaries, key=lambda x: float(x.get("total_return_pct", 0.0) or 0.0)
        )[: min(3, len(summaries))]

        lines.append("## Top 策略")
        lines.append("")
        lines.append("| 排名 | 策略 | 总收益% | 年化% | 最大回撤% | Sharpe |")
        lines.append("|---:|---|---:|---:|---:|---:|")
        for idx, s in enumerate(top_n, 1):
            lines.append(
                f"| {idx} | {s.get('strategy', '-')} | "
                f"{_fmt_num(s.get('total_return_pct', 0))} | "
                f"{_fmt_num(s.get('annual_return_pct', 0))} | "
                f"{_fmt_num(s.get('max_drawdown_pct', 0))} | "
                f"{_fmt_num(s.get('sharpe', 0), 3)} |"
            )
        lines.append("")

        lines.append("## Bottom 策略")
        lines.append("")
        lines.append("| 排名 | 策略 | 总收益% | 年化% | 最大回撤% | Sharpe |")
        lines.append("|---:|---|---:|---:|---:|---:|")
        for idx, s in enumerate(bottom_n, 1):
            lines.append(
                f"| {idx} | {s.get('strategy', '-')} | "
                f"{_fmt_num(s.get('total_return_pct', 0))} | "
                f"{_fmt_num(s.get('annual_return_pct', 0))} | "
                f"{_fmt_num(s.get('max_drawdown_pct', 0))} | "
                f"{_fmt_num(s.get('sharpe', 0), 3)} |"
            )
        lines.append("")

    lines.append(_build_strategy_details_markdown(summaries, strategy_results))
    return "\n".join(lines)


def _build_monthly_trend_markdown(equity_df: pd.DataFrame) -> str:
    lines: list[str] = ["# 月度趋势", ""]
    if equity_df.empty or "date" not in equity_df.columns or "strategy" not in equity_df.columns:
        lines.append("暂无可用净值数据。")
        lines.append("")
        return "\n".join(lines)

    df = equity_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["strategy", "date"])
    if df.empty:
        lines.append("暂无可用净值数据。")
        lines.append("")
        return "\n".join(lines)

    df["month"] = df["date"].dt.strftime("%Y-%m")
    monthly_rows = []

    for strategy, g in df.groupby("strategy"):
        g = g.sort_values("date")
        prev_end = None
        for month, mg in g.groupby("month"):
            start_equity = float(mg.iloc[0]["equity"])
            end_equity = float(mg.iloc[-1]["equity"])
            base = prev_end if prev_end and prev_end > 0 else start_equity
            monthly_return_pct = ((end_equity - base) / base * 100.0) if base > 0 else 0.0
            monthly_rows.append(
                {
                    "strategy": strategy,
                    "month": month,
                    "monthly_return_pct": monthly_return_pct,
                }
            )
            prev_end = end_equity

    if not monthly_rows:
        lines.append("暂无可用净值数据。")
        lines.append("")
        return "\n".join(lines)

    monthly_df = pd.DataFrame(monthly_rows)
    pivot = monthly_df.pivot(index="strategy", columns="month", values="monthly_return_pct").sort_index()
    months = list(pivot.columns)

    lines.append("| 策略 | " + " | ".join(months) + " |")
    lines.append("|---|" + "|".join("---:" for _ in months) + "|")
    for strategy, row in pivot.iterrows():
        vals = [f"{float(row[m]):.2f}%" if pd.notna(row[m]) else "-" for m in months]
        lines.append(f"| {strategy} | " + " | ".join(vals) + " |")

    lines.append("")
    lines.append("注：月收益按“月末净值 vs 上月末净值（首月用当月首日净值）”计算。")
    lines.append("")
    return "\n".join(lines)


def _build_rich_text_report(
    meta: Mapping[str, Any],
    summaries: list[dict[str, Any]],
    strategy_results: Mapping[str, Dict[str, Any]],
) -> str:
    console = Console(record=True, width=180, force_terminal=False, color_system=None)
    console.print(
        Panel(
            f"[bold]回测区间:[/bold] {meta.get('from', '-')} ~ {meta.get('to', '-')}\n"
            f"[bold]策略数:[/bold] {len(meta.get('strategies', []))}\n"
            f"[bold]资金模式:[/bold] {meta.get('capital_mode', '-')}\n"
            f"[bold]每票金额:[/bold] {_fmt_num(meta.get('cash_per_trade', 0), 0)}",
            title="📊 回测结果（Rich 文本版）",
            border_style="green",
        )
    )

    if not summaries:
        console.print("[yellow]暂无策略结果。[/yellow]")
        return console.export_text(styles=False)

    for s in summaries:
        strategy = str(s.get("strategy", "-"))
        result = strategy_results.get(strategy, {})
        trades_df = result.get("trades", pd.DataFrame())
        skips_df = result.get("skips", pd.DataFrame())
        if not isinstance(trades_df, pd.DataFrame):
            trades_df = pd.DataFrame()
        if not isinstance(skips_df, pd.DataFrame):
            skips_df = pd.DataFrame()
        console.print()
        print_strategy_report(
            console=console,
            strategy_name=strategy,
            trades=trades_df,
            skips=skips_df,
            summary=s,
        )
        print_summary(console, s)

    return console.export_text(styles=False)


def _build_windows_friendly_text_report(
    meta: Mapping[str, Any],
    summaries: list[dict[str, Any]],
    strategy_results: Mapping[str, Dict[str, Any]],
) -> str:
    """Windows 友好文本：不依赖等宽字体和框线对齐。"""
    lines: list[str] = []
    lines.append("回测结果（Windows 友好文本）")
    lines.append(f"区间: {meta.get('from', '-')} ~ {meta.get('to', '-')}")
    lines.append(f"策略数: {len(meta.get('strategies', []))}")
    lines.append(f"资金模式: {meta.get('capital_mode', '-')}")
    lines.append(f"每票金额: {_fmt_num(meta.get('cash_per_trade', 0), 0)}")
    lines.append("")

    if not summaries:
        lines.append("暂无策略结果。")
        lines.append("")
        return "\n".join(lines)

    stock_names = _load_stock_names()
    for s in summaries:
        strategy = str(s.get("strategy", "-"))
        result = strategy_results.get(strategy, {})
        trades_df = result.get("trades", pd.DataFrame())
        skips_df = result.get("skips", pd.DataFrame())
        if not isinstance(trades_df, pd.DataFrame):
            trades_df = pd.DataFrame()
        if not isinstance(skips_df, pd.DataFrame):
            skips_df = pd.DataFrame()

        lines.append(f"=== 策略: {strategy} ===")
        lines.append(
            "摘要: "
            f"交易笔数={int(float(s.get('trade_count', 0) or 0))}, "
            f"胜率={_fmt_num(s.get('win_rate_pct', 0))}%, "
            f"总收益={_fmt_num(s.get('total_return_pct', 0))}%, "
            f"年化={_fmt_num(s.get('annual_return_pct', 0))}%, "
            f"最大回撤={_fmt_num(s.get('max_drawdown_pct', 0))}%, "
            f"Sharpe={_fmt_num(s.get('sharpe', 0), 3)}, "
            f"最终现金={_fmt_num(s.get('final_cash', 0), 2)}"
        )
        lines.append("")

        lines.append("交易明细(CSV):")
        lines.append("code,name,signal_date,buy_date,buy_price,sell_date,sell_price,shares,profit,return_pct,sell_postpone_days")
        if trades_df.empty:
            lines.append("N/A")
        else:
            sort_cols = [c for c in ["sell_date", "buy_date", "code"] if c in trades_df.columns]
            show = trades_df.sort_values(sort_cols) if sort_cols else trades_df
            for _, row in show.iterrows():
                code = str(row.get("code", "")).zfill(6)
                name = stock_names.get(code, "")
                lines.append(
                    ",".join(
                        [
                            _safe_md_cell(code),
                            _safe_md_cell(name),
                            _safe_md_cell(row.get("signal_date", "")),
                            _safe_md_cell(row.get("buy_date", "")),
                            _fmt_num(row.get("buy_price", 0), 2),
                            _safe_md_cell(row.get("sell_date", "")),
                            _fmt_num(row.get("sell_price", 0), 2),
                            str(int(float(row.get("shares", 0) or 0))),
                            _fmt_num(row.get("profit", 0), 2),
                            _fmt_num(row.get("return_pct", 0), 2),
                            str(int(float(row.get("sell_postpone_days", 0) or 0))),
                        ]
                    )
                )
        lines.append("")

        lines.append("跳过记录(CSV):")
        lines.append("code,name,signal_date,buy_date,reason")
        if skips_df.empty:
            lines.append("N/A")
        else:
            sort_cols = [c for c in ["signal_date", "code"] if c in skips_df.columns]
            show = skips_df.sort_values(sort_cols) if sort_cols else skips_df
            for _, row in show.iterrows():
                code = str(row.get("code", "")).zfill(6)
                name = stock_names.get(code, "")
                lines.append(
                    ",".join(
                        [
                            _safe_md_cell(code),
                            _safe_md_cell(name),
                            _safe_md_cell(row.get("signal_date", "")),
                            _safe_md_cell(row.get("buy_date", "")),
                            _safe_md_cell(row.get("reason", "")),
                        ]
                    )
                )
        lines.append("")

    return "\n".join(lines)


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
        f"execution_key: {meta.get('execution_key') or meta.get('run_id', '-')}",
        f"created_at: {meta.get('created_at', '-')}",
        f"range: {meta.get('from', '-')} ~ {meta.get('to', '-')}",
        f"strategies: {', '.join(str(s) for s in meta.get('strategies', []))}",
        f"capital_mode: {meta.get('capital_mode', '-')}",
        f"cash_per_trade: {_fmt_num(meta.get('cash_per_trade', 0), 0)}",
        "",
        "summary:",
    ]
    for s in summaries:
        lines.append(
            f"- {s.get('strategy', '-')}: "
            f"trades={int(float(s.get('trade_count', 0) or 0))}, "
            f"skips={int(float(s.get('skip_count', 0) or 0))}, "
            f"total_return={_fmt_num(s.get('total_return_pct', 0))}%, "
            f"max_drawdown={_fmt_num(s.get('max_drawdown_pct', 0))}%"
        )
    lines.append("")
    return "\n".join(lines)


def save_run_outputs(
    run_dir: Path,
    meta: Dict[str, Any],
    strategy_results: Mapping[str, Dict[str, Any]],
    storage_root: Path | None = None,
) -> Path:
    summaries: list[dict[str, Any]] = []
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_skips: list[pd.DataFrame] = []

    for strategy_name, result in strategy_results.items():
        summary = dict(result.get("summary", {}) or {})
        summary["strategy"] = strategy_name

        equity_df = result.get("daily_equity", pd.DataFrame())
        trades_df = result.get("trades", pd.DataFrame())
        skips_df = result.get("skips", pd.DataFrame())
        if isinstance(skips_df, pd.DataFrame):
            summary["skip_count"] = int(len(skips_df))
        else:
            summary["skip_count"] = 0
        summaries.append(summary)

        if isinstance(equity_df, pd.DataFrame):
            all_equity.append(equity_df.assign(strategy=strategy_name))
        if isinstance(trades_df, pd.DataFrame):
            all_trades.append(trades_df)
        if isinstance(skips_df, pd.DataFrame):
            all_skips.append(skips_df)

    summaries.sort(key=lambda x: float(x.get("total_return_pct", 0.0) or 0.0), reverse=True)

    equity_df = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    trades_df = _with_stock_meta(pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame())
    skips_df = _with_stock_meta(pd.concat(all_skips, ignore_index=True) if all_skips else pd.DataFrame())
    run_id = str(meta.get("run_id") or run_dir.name)
    meta["run_id"] = run_id
    meta["execution_key"] = run_id

    equity_path = run_dir / "equity.parquet"
    trades_path = run_dir / "trades.parquet"
    skips_path = run_dir / "skips.parquet"
    log_path = run_dir / "log.txt"

    _write_parquet(equity_path, _with_execution_key(equity_df, run_id))
    _write_parquet(trades_path, _with_execution_key(trades_df, run_id))
    _write_parquet(skips_path, _with_execution_key(skips_df, run_id))
    log_path.write_text(_build_log_text(meta, summaries), encoding="utf-8")

    storage = BacktestStorage(storage_root or run_dir.parents[2])
    storage.record_backtest_result(
        run_id=run_id,
        meta=meta,
        summaries=summaries,
        object_dir=run_dir,
    )
    storage.register_artifact(
        run_id=run_id,
        artifact_type="equity_parquet",
        path=equity_path,
        mime_type="application/vnd.apache.parquet",
    )
    storage.register_artifact(
        run_id=run_id,
        artifact_type="trades_parquet",
        path=trades_path,
        mime_type="application/vnd.apache.parquet",
    )
    storage.register_artifact(
        run_id=run_id,
        artifact_type="skips_parquet",
        path=skips_path,
        mime_type="application/vnd.apache.parquet",
    )
    storage.register_artifact(
        run_id=run_id,
        artifact_type="log_txt",
        path=log_path,
        mime_type="text/plain; charset=utf-8",
    )

    return run_dir
