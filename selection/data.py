from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

# ─────────────────────────── 数据加载 ─────────────────────────── #

def load_data_table(data_dir: str, tickers: Optional[List[str]] = None) -> pl.DataFrame:
    """读取并返回按 code/date 排序的大表，使用 scan_parquet 延迟加载 + 列裁剪"""
    data_path = Path(data_dir)
    
    if tickers:
        files = [str(data_path / f"{t}.parquet") for t in tickers if (data_path / f"{t}.parquet").exists()]
    else:
        files = [str(f) for f in data_path.glob("*.parquet")]
    
    if not files:
        return pl.DataFrame()
    
    cols = ["date", "open", "close", "high", "low", "volume"]
    return (
        pl.scan_parquet(files, include_file_paths="file_path")
        .select([
            pl.col("file_path").str.extract(r"([^/]+)\.parquet$").alias("code"),
            *[pl.col(c) for c in cols],
        ])
        .sort(["code", "date"])
        .collect()
    )


def table_to_data_dict(df_all: pl.DataFrame) -> Dict[str, pl.DataFrame]:
    """将大表转换为旧版 Dict[code, DataFrame] 结构。

    说明：
    - 为兼容历史 selector 的接口，这里保留 code->DataFrame 的字典形态。
    - 每个子表会移除重复的 code 列，只保留原始行情数据列。
    """
    data = {}
    for code, df in df_all.partition_by("code", as_dict=True).items():
        data[code[0]] = df.drop("code")
    return data


def load_data(data_dir: str, tickers: Optional[List[str]] = None) -> Dict[str, pl.DataFrame]:
    """使用 Polars 批量读取 Parquet 文件（旧接口）"""
    return table_to_data_dict(load_data_table(data_dir, tickers))
