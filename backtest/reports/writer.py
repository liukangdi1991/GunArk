from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

import pandas as pd


def save_run_outputs(
    output_root: Path,
    strategy_dir_name: str,
    daily_equity: pd.DataFrame,
    trades: pd.DataFrame,
    skips: pd.DataFrame,
    summary: Dict[str, float],
) -> Path:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"{run_id}_{strategy_dir_name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    daily_equity.to_csv(run_dir / "daily_equity.csv", index=False)
    trades.to_csv(run_dir / "trades.csv", index=False)
    skips.to_csv(run_dir / "skips.csv", index=False)

    with (run_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return run_dir
