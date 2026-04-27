from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List

from backtest.models import Signal


def _date_from_filename(path: Path) -> date:
    return datetime.strptime(path.stem, "%Y%m%d").date()


def list_signal_files(signal_dir: Path, start: date, end: date) -> List[Path]:
    if not signal_dir.exists():
        legacy_dir = signal_dir.parent / "polars"
        if legacy_dir.exists():
            signal_dir = legacy_dir
        else:
            return []
    files: List[Path] = []
    for p in signal_dir.glob("*.json"):
        try:
            d = _date_from_filename(p)
        except ValueError:
            continue
        if start <= d <= end:
            files.append(p)
    return sorted(files, key=lambda x: x.stem)


def load_signals(
    signal_files: Iterable[Path],
    strategy_names: List[str],
    calendar: List[date],
    fixed_hold_n_days: int,
) -> Dict[date, List[Signal]]:
    calendar_index = {d: i for i, d in enumerate(calendar)}
    by_day: Dict[date, List[Signal]] = {}

    for file_path in signal_files:
        signal_date = _date_from_filename(file_path)
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
