"""比较 pandas 和 polars 选股结果（使用采样股票）"""
import json
import random
import sys
from pathlib import Path
from datetime import date
from typing import Dict

import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pandas_version"))
import Selector as selector_pd

sys.path.insert(0, str(ROOT / "polars_version"))
import Selector as selector_pl

TEST_DATES = ["2025-04-07", "2025-07-07", "2025-09-30"]
CONFIG_PATH = ROOT / "configs.json"
DATA_DIR = ROOT / "data"
DB_DIR = ROOT / "db"
SAMPLE_SIZE = 500

random.seed(42)


def load_pd_data(codes):
    frames = {}
    for code in codes:
        fp = DATA_DIR / f"{code}.csv"
        if fp.exists():
            df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
            frames[code] = df
    return frames


def load_pl_data(codes):
    frames = {}
    for code in codes:
        fp = DB_DIR / f"{code}.parquet"
        if fp.exists():
            frames[code] = pl.read_parquet(fp).sort("date")
    return frames


def load_config():
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("selectors", raw) if isinstance(raw, dict) else raw


def main():
    all_csv = [f.stem for f in DATA_DIR.glob("*.csv")]
    all_parquet = [f.stem for f in DB_DIR.glob("*.parquet")]
    common = sorted(set(all_csv) & set(all_parquet))
    print(f"共同股票数: {len(common)}")

    codes = random.sample(common, min(SAMPLE_SIZE, len(common)))
    print(f"采样 {len(codes)} 只股票进行比较\n")

    print("加载数据...")
    pd_data = load_pd_data(codes)
    pl_data = load_pl_data(codes)
    common_loaded = set(pd_data.keys()) & set(pl_data.keys())
    print(f"成功加载: pandas={len(pd_data)}, polars={len(pl_data)}, 共同={len(common_loaded)}")

    cfgs = load_config()

    for d_str in TEST_DATES:
        ts_pd = pd.Timestamp(d_str)
        ts_pl = date.fromisoformat(d_str)
        print(f"\n{'='*60}")
        print(f"选股日期: {d_str}")
        print(f"{'='*60}")

        for cfg in cfgs:
            if cfg.get("activate") is False:
                continue
            alias = cfg.get("alias", cfg["class"])
            cls_name = cfg["class"]
            params = cfg.get("params", {})

            cls_pd = getattr(selector_pd, cls_name)
            cls_pl = getattr(selector_pl, cls_name)

            picks_pd = cls_pd(**params).select(ts_pd, pd_data)
            picks_pl = cls_pl(**params).select(ts_pl, pl_data)

            set_pd = set(picks_pd)
            set_pl = set(picks_pl)
            match = set_pd == set_pl

            status = "✅ 一致" if match else "❌ 不一致"
            print(f"\n  [{alias}] {status}")
            print(f"    pandas ({len(picks_pd)}): {sorted(picks_pd)}")
            print(f"    polars ({len(picks_pl)}): {sorted(picks_pl)}")

            if not match:
                only_pd = sorted(set_pd - set_pl)
                only_pl = sorted(set_pl - set_pd)
                if only_pd:
                    print(f"    仅 pandas: {only_pd}")
                if only_pl:
                    print(f"    仅 polars: {only_pl}")


if __name__ == "__main__":
    main()
