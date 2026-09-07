"""同步机制重做：常量与数据结构（纯，零 I/O）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

BASELINE_START = date(2015, 1, 1)
DATA_CUTOFF_HOUR = 16
SHANGHAI = ZoneInfo("Asia/Shanghai")
BSE_PREFIXES = ("92", "4", "8")     # 92 为新代码段，4/8 为原三板平移而来


def is_bse_code(code: str) -> bool:
    """裸代码是否属北交所 —— Tushare daily 物理不提供其行情。"""
    return str(code).split(".")[0].zfill(6).startswith(BSE_PREFIXES)


class PlanKind(str, Enum):
    BLOCKED = "blocked"
    REBUILD_REQUIRED = "rebuild_required"
    FULL = "full"
    INCREMENTAL = "incremental"
    UPTODATE = "uptodate"
    BACKFILL_CODES = "backfill_codes"


class FailureKind(str, Enum):
    ENV = "env"
    CODE = "code"
    UNKNOWN = "unknown"
    OK_EMPTY = "ok_empty"


@dataclass(frozen=True)
class SyncPlan:
    kind: PlanKind
    latest_tradeable: date | None
    missing_days: list[date]
    stale_days: int
    reason: str


def latest_tradeable_day(trade_days: set[date], now_cn: datetime) -> date | None:
    """最近一个数据已可得（北京 16:00 截止）的交易日。

    语义沿用旧 syncer.latest_tradeable_day；日历为空时返回 None
    （旧实现返回 today 的分支由 planner 的 BLOCKED 行承接）。
    """
    if not trade_days:
        return None
    today = now_cn.date()
    if now_cn.hour >= DATA_CUTOFF_HOUR and today in trade_days:
        return today
    past = sorted(d for d in trade_days if d < today)
    return past[-1] if past else None
