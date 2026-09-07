"""文件层写入：原子写 / flush_by_code（按日 upsert 幂等）/ 单向换名 / 读回。见 spec §3.6。"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from trendradar.infrastructure.tushare.fetch import _align_columns

_AT_FDCWD = -100
_SYS_RENAMEAT2_X86_64 = 316
_RENAME_EXCHANGE = 2
# 316 是 x86_64 专用号；别的架构上它是另一个（或未定义的）调用，
# 拿 renameat2 的参数去打它属未定义行为，故只在 x86_64 Linux 上尝试
_CAN_EXCHANGE = sys.platform.startswith("linux") and platform.machine() == "x86_64"


def atomic_write_parquet(df: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        os.close(fd)
        df.write_parquet(tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def upsert_code_file(target: Path, new: pl.DataFrame) -> None:
    """单个 code 文件按日 upsert（INV-4 幂等）：本地旧行让位于新行。"""
    target = Path(target)
    if target.exists():
        local = _align_columns(pl.read_parquet(target), new)
        merged = pl.concat(
            [local.filter(~pl.col("date").is_in(new["date"].to_list())), new]
        ).sort("date")
    else:
        merged = new.sort("date")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(merged, target)


def flush_by_code(all_days: pl.DataFrame, bars_dir: Path) -> list[str]:
    """按 code 分组：每个受影响文件读一次 + 按日期 upsert + 原子写一次（INV-4 幂等）。"""
    if all_days.is_empty():
        return []
    bars_dir = Path(bars_dir)
    bars_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for group in all_days.partition_by("code", maintain_order=True):
        code = group["code"][0]
        upsert_code_file(bars_dir / f"{code}.parquet", group)
        written.append(str(code))
    return written


def readback_calendar(bars_dir: Path) -> set[date]:
    """scan 全库实测日历（所有文件的日期并集）。"""
    files = sorted(Path(bars_dir).glob("*.parquet"))
    if not files:
        return set()
    s = (
        pl.scan_parquet([str(p) for p in files])
        .select(pl.col("date"))
        .unique()
        .collect()
    )
    return set(s["date"].to_list())


def _try_exchange(a: Path, b: Path) -> bool:
    """renameat2(RENAME_EXCHANGE) 原子交换；平台不支持返回 False。"""
    if not _CAN_EXCHANGE:
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.syscall(
            ctypes.c_long(_SYS_RENAMEAT2_X86_64),
            ctypes.c_int(_AT_FDCWD), os.fsencode(a),
            ctypes.c_int(_AT_FDCWD), os.fsencode(b),
            ctypes.c_uint(_RENAME_EXCHANGE),
        )
        return rc == 0
    except Exception:
        return False


def swap_in_bars(market_dir: Path) -> None:
    """单向换名：bars → bars_prev、staging → bars、重建空 staging（spec §3.6）。

    优先 renameat2 原子交换（bars 无缺失窗口）；回退两步 rename
    （毫秒级窗口，崩溃时以 bars_prev 手动恢复，§9）。
    """
    market_dir = Path(market_dir)
    bars = market_dir / "bars"
    staging = market_dir / "staging"
    prev = market_dir / "bars_prev"

    bars.mkdir(parents=True, exist_ok=True)   # 首次建库时 bars 可能尚不存在
    if prev.exists():
        shutil.rmtree(prev)
    if _try_exchange(bars, staging):
        os.replace(staging, prev)          # 交换后旧版落在 staging 位
    else:
        os.replace(bars, prev)
        os.replace(staging, bars)
    staging.mkdir(parents=True, exist_ok=True)
