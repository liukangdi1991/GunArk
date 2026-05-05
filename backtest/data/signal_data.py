from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List

from backtest.models import Signal


def _date_from_filename(path: Path) -> date:
    return datetime.strptime(path.stem, "%Y%m%d").date()


def signal_file_date(path: Path) -> date:
    try:
        return _date_from_filename(path)
    except ValueError:
        return _date_from_payload(path)


def _date_from_payload(path: Path) -> date:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"信号文件格式无效: {path}")
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        raw_date = item.get("date")
        if raw_date:
            return _parse_signal_date(str(raw_date))
    raise ValueError(f"信号文件缺少 date 字段: {path}")


def _parse_signal_date(value: str) -> date:
    text = value.strip()
    if "-" in text:
        return datetime.strptime(text, "%Y-%m-%d").date()
    return datetime.strptime(text, "%Y%m%d").date()


def list_signal_files(signal_dir: Path, start: date, end: date) -> List[Path]:
    if not signal_dir.exists():
        return []
    files: List[Path] = []
    for p in signal_dir.glob("*.json"):
        try:
            d = signal_file_date(p)
        except ValueError:
            continue
        if start <= d <= end:
            files.append(p)
    return sorted(files, key=signal_file_date)


def load_signals(
    signal_files: Iterable[Path],
    strategy_names: List[str],
    calendar: List[date],
    fixed_hold_n_days: int,
) -> Dict[date, List[Signal]]:
    calendar_index = {d: i for i, d in enumerate(calendar)}
    by_day: Dict[date, List[Signal]] = {}

    for file_path in signal_files:
        signal_date = signal_file_date(file_path)
        if signal_date not in calendar_index:
            continue
        signal_idx = calendar_index[signal_date]
        # Trade rule:
        # signal at T, buy at T+1 open, sell at T+N+1 close.
        buy_idx = signal_idx + 1
        sell_idx = signal_idx + fixed_hold_n_days + 1
        if buy_idx >= len(calendar) or sell_idx >= len(calendar):
            continue
        buy_date = calendar[buy_idx]
        target_sell_date = calendar[sell_idx]

        with file_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        for strategy in strategy_names:
            data = payload.get(strategy, {})
            stocks = data.get("stocks", []) if isinstance(data, dict) else []
            for code in stocks:
                by_day.setdefault(buy_date, []).append(
                    Signal(
                        strategy=strategy,
                        code=str(code).zfill(6),
                        signal_date=signal_date,
                        buy_date=buy_date,
                        target_sell_date=target_sell_date,
                    )
                )

    return by_day
