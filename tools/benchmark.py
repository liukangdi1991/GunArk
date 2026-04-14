"""Pandas vs Polars 选股性能对比"""
import json
import sys
import time
from pathlib import Path
from datetime import date

import pandas as pd
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pandas_version"))
import Selector as selector_pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "polars_version"))
import Selector as selector_pl

ROOT = Path(__file__).resolve().parent.parent

TRADE_DATE_STR = "2026-03-25"
TRADE_DATE_PD = pd.Timestamp(TRADE_DATE_STR)
TRADE_DATE_PL = date.fromisoformat(TRADE_DATE_STR)
DATA_DIR = ROOT / "data"
DB_DIR = ROOT / "db"

def load_pd_data():
    frames = {}
    for fp in DATA_DIR.glob("*.csv"):
        code = fp.stem
        df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
        frames[code] = df
    return frames

def load_pl_data():
    files = [str(f) for f in DB_DIR.glob("*.parquet")]
    df_all = pl.read_parquet(files, include_file_paths="file_path")
    df_all = df_all.with_columns(
        pl.col("file_path").str.extract(r"([^/]+)\.parquet$").alias("code")
    ).drop("file_path")
    data = {}
    for code, df in df_all.partition_by("code", as_dict=True).items():
        data[code[0]] = df.drop("code").sort("date")
    return data

def load_config():
    with open(ROOT / "configs.json", encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("selectors", raw) if isinstance(raw, dict) else raw

def main():
    cfgs = load_config()

    # 加载数据
    print("=" * 60)
    print(f"选股日期: {TRADE_DATE_STR}")
    print("=" * 60)

    t0 = time.perf_counter()
    pd_data = load_pd_data()
    pd_load_time = time.perf_counter() - t0
    print(f"Pandas  加载数据: {pd_load_time:.3f}s  ({len(pd_data)} 只)")

    t0 = time.perf_counter()
    pl_data = load_pl_data()
    pl_load_time = time.perf_counter() - t0
    print(f"Polars  加载数据: {pl_load_time:.3f}s  ({len(pl_data)} 只)")

    print()

    # 选股对比
    print(f"{'战法':<20} {'Pandas结果':>10} {'Pandas耗时':>10} {'Polars结果':>10} {'Polars耗时':>10} {'加速比':>8} {'一致':>6}")
    print("-" * 96)

    pd_total_time = 0
    pl_total_time = 0

    for cfg in cfgs:
        if cfg.get("activate") is False:
            continue
        alias = cfg.get("alias", cfg["class"])
        cls_name = cfg["class"]
        params = cfg.get("params", {})

        cls_pd = getattr(selector_pd, cls_name)
        cls_pl = getattr(selector_pl, cls_name)

        t0 = time.perf_counter()
        picks_pd = cls_pd(**params).select(TRADE_DATE_PD, pd_data)
        pd_time = time.perf_counter() - t0
        pd_total_time += pd_time

        t0 = time.perf_counter()
        picks_pl = cls_pl(**params).select(TRADE_DATE_PL, pl_data)
        pl_time = time.perf_counter() - t0
        pl_total_time += pl_time

        speedup = pd_time / pl_time if pl_time > 0 else float("inf")
        match = "✅" if set(picks_pd) == set(picks_pl) else "❌"

        print(f"{alias:<20} {len(picks_pd):>10} {pd_time:>9.3f}s {len(picks_pl):>10} {pl_time:>9.3f}s {speedup:>7.2f}x {match:>6}")

    print("-" * 96)
    speedup = pd_total_time / pl_total_time if pl_total_time > 0 else float("inf")
    print(f"{'总计选股':<20} {'':>10} {pd_total_time:>9.3f}s {'':>10} {pl_total_time:>9.3f}s {speedup:>7.2f}x")
    print()
    pd_all = pd_load_time + pd_total_time
    pl_all = pl_load_time + pl_total_time
    speedup_all = pd_all / pl_all if pl_all > 0 else float("inf")
    print(f"{'数据加载 + 选股':<20} {'':>10} {pd_all:>9.3f}s {'':>10} {pl_all:>9.3f}s {speedup_all:>7.2f}x")


if __name__ == "__main__":
    main()
