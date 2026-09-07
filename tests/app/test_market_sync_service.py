from datetime import date, datetime
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from trendradar.app.jobs.persistence import JobStore
from trendradar.app.services.market_sync import service
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.sync_store import SyncStore

CN = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 27, 18, 0, tzinfo=CN)   # 16:00 后 → 今日可得
CODES = ["000001", "000002", "600000"]
# 官方日历：08-24/25/26/27（周一至周四）+ 年底远日（保证 MAX ≥ today）
CAL = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26),
       date(2026, 8, 27), date(2026, 12, 31)]
DONE = [date(2026, 8, 24), date(2026, 8, 25)]   # 缺口 = 26/27


class FakeResp:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self, orient="records"):
        return self._data


def _resp_cal(days):
    return FakeResp({"cal_date": [d.strftime("%Y%m%d") for d in days],
                     "is_open": [1] * len(days)})


def _resp_day(day, codes):
    n = len(codes)
    return FakeResp({
        "ts_code": [f"{c}.SZ" for c in codes],
        "trade_date": [day.strftime("%Y%m%d")] * n,
        "open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n,
        "close": [10.5] * n, "vol": [1000.0] * n,
    })


def _resp_meta(codes):
    n = len(codes)
    return FakeResp({
        "ts_code": [f"{c}.SZ" for c in codes], "symbol": list(codes),
        "name": [f"S{c}" for c in codes], "area": [""] * n,
        "industry": [""] * n, "market": [""] * n,
        "list_date": ["20100101"] * n, "delist_date": [None] * n,
    })


class FakePro:
    """可注水的 Tushare pro。daily(trade_date=) → 按日；daily(ts_code=) → 按股。"""

    def __init__(self):
        self.calendar_days = list(CAL)
        self.meta_codes = list(CODES)  # 可注水：健康轮需要足够多的股票摊薄失败率
        self.day_codes = {}        # date -> 该日返回的代码列表
        self.code_days = {}        # code -> 该股的日期列表（范围模式）
        self.raise_cal = None      # trade_cal 抛错
        self.raise_daily = None    # daily(trade_date=) 抛错
        self.raise_daily_codes = set()                        # 按股注水（范围模式）
        self.raise_daily_exc = RuntimeError("频率超限")
        self.daily_calls = 0
        self.adj_empty = False      # adj_factor 接口通但返回空
        self.adj_empty_codes = set()   # 只让这些股的 adj_factor 返回空

    def trade_cal(self, exchange=None, start_date=None, end_date=None):
        if self.raise_cal:
            raise self.raise_cal
        return _resp_cal(self.calendar_days)

    def stock_basic(self, exchange="", list_status=None, fields=None):
        if list_status == "D":
            return FakeResp({})
        return _resp_meta(self.meta_codes)

    def daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None, freq=None):
        self.daily_calls += 1
        if trade_date:
            if self.raise_daily:
                raise self.raise_daily
            day = datetime.strptime(trade_date, "%Y%m%d").date()
            codes = self.day_codes.get(day, [])
            return _resp_day(day, codes) if codes else FakeResp({})
        code = ts_code.split(".")[0]
        if code in self.raise_daily_codes:
            raise self.raise_daily_exc
        lo = datetime.strptime(start_date, "%Y%m%d").date()
        hi = datetime.strptime(end_date, "%Y%m%d").date()
        days = [d for d in self.code_days.get(code, []) if lo <= d <= hi]
        if not days:
            return FakeResp({})
        n = len(days)
        return FakeResp({
            "ts_code": [ts_code] * n,
            "trade_date": [d.strftime("%Y%m%d") for d in days],
            "open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n,
            "close": [10.5] * n, "vol": [1000.0] * n,
        })

    def adj_factor(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        """镜像 daily 的行集。真接口对 daily 给出的每行都有因子，缺行会触发
        AdjFactorUnavailable 硬失败 —— fake 不能比真接口宽松。"""
        if self.adj_empty:
            return FakeResp({})
        if trade_date:
            day = datetime.strptime(trade_date, "%Y%m%d").date()
            # 少给几只 ⇒ 覆盖不全，与真接口局部缺行的表现一致
            codes = [c for c in self.day_codes.get(day, [])
                     if c not in self.adj_empty_codes]
            if not codes:
                return FakeResp({})
            n = len(codes)
            return FakeResp({"ts_code": [f"{c}.SZ" for c in codes],
                             "trade_date": [trade_date] * n,
                             "adj_factor": [3.5] * n})
        if ts_code.split(".")[0] in self.adj_empty_codes:
            return FakeResp({})
        lo = datetime.strptime(start_date, "%Y%m%d").date()
        hi = datetime.strptime(end_date, "%Y%m%d").date()
        days = [d for d in self.code_days.get(ts_code.split(".")[0], []) if lo <= d <= hi]
        if not days:
            return FakeResp({})
        n = len(days)
        return FakeResp({"ts_code": [ts_code] * n,
                         "trade_date": [d.strftime("%Y%m%d") for d in days],
                         "adj_factor": [3.5] * n})


class FakeCtx:
    def __init__(self, job_id, store, cancel_after=None):
        self.job_id = job_id
        self.store = store
        self.logs = []
        self.status = "running"
        self.result = None
        self.error = None
        self._cancel_after = cancel_after
        self._progress_count = 0

    def log(self, message, level="INFO"):
        self.logs.append(message)

    def update_progress(self, current, total, message=""):
        self._progress_count += 1
        self.logs.append(f"[PROGRESS] {current}/{total} {message}")

    def check_cancelled(self):
        return self._cancel_after is not None and self._progress_count >= self._cancel_after

    def succeed(self, result):
        self.status, self.result = "success", result
        self.store.set_status(self.job_id, "success", result=result)

    def fail(self, error):
        self.status, self.error = "failed", error
        self.store.set_status(self.job_id, "failed", error=error)

    def cancel(self, reason="Cancelled by user"):
        self.status, self.error = "cancelled", reason
        self.store.set_status(self.job_id, "cancelled", error=reason)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    init_schema(StorageConnection(storage).connect())
    return tmp_path


@pytest.fixture
def job_store(runtime):
    # stage-1 会经 ctx.store 写 sibling job 行 → 独立 db 也要有 jobs 表
    import sqlite3

    db_path = runtime / "storage" / "jobs.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT,
            request_json TEXT,
            result_json TEXT,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            level TEXT NOT NULL DEFAULT 'INFO',
            message TEXT NOT NULL,
            UNIQUE(job_id, sequence)
        );
        """
    )
    conn.commit()
    conn.close()
    return JobStore(db_path)


@pytest.fixture
def sync_store(runtime):
    return SyncStore(runtime / "storage")


@pytest.fixture
def fake_pro(monkeypatch):
    pro = FakePro()
    monkeypatch.setattr(service, "get_pro", lambda: pro)
    import trendradar.infrastructure.tushare.stocklist as sl
    monkeypatch.setattr(sl, "get_pro", lambda: pro)
    return pro


def run_worker(fake_pro, request, job_store, cancel_after=None):
    # 生产里 worker 起跑前 jobs 行已存在（executor 建）→ 测试同样如此，
    # 否则 worker 无从得知自己的终态
    ctx = FakeCtx(job_store.create_job("market_bars_sync", request), job_store, cancel_after)
    service.bars_sync_worker(ctx, request, now_cn=NOW)
    return ctx


def _execution_status_by_type(runtime) -> dict:
    from trendradar.infrastructure.storage.connection import StorageConnection

    with StorageConnection(runtime / "storage").connection() as conn:
        return {
            r["execution_type"]: r["status"]
            for r in conn.execute("SELECT execution_type, status FROM executions").fetchall()
        }


# ---- R1 / R2 / R3：stage-1 日历 ----

def test_r1_calendar_only_grows(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days([date(2015, 1, 5), date(2026, 12, 31)])
    # 窄请求（新 schema 下已无日期字段，等价于"任何请求"）
    run_worker(fake_pro, {}, job_store)
    days = sync_store.calendar_days()
    assert days >= {date(2015, 1, 5), date(2026, 12, 31)}      # 只增不减
    assert sync_store.max_calendar_day() >= date(2026, 12, 31)  # MAX 不回退
    run_worker(fake_pro, {}, job_store)                         # 连刷幂等
    assert sync_store.calendar_days() == days | set(CAL)


def test_r2_stale_calendar_blocks(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days([date(2026, 8, 20)])        # MAX < today
    fake_pro.raise_cal = RuntimeError("Connection aborted")
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "日历" in ctx.error
    assert not (runtime / "storage" / "market" / "stock_meta.parquet").exists()


def test_r3_empty_calendar_fails_not_skip(runtime, job_store, sync_store, fake_pro):
    fake_pro.raise_cal = RuntimeError("Connection aborted")
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "日历未就绪" in ctx.error


def test_r3_stage1_linked_to_stage2(runtime, job_store, sync_store, fake_pro):
    """spec §3.9：stage-1/stage-2 两行 job 必须留下审计关联。"""
    from trendradar.infrastructure.storage.connection import StorageConnection

    sync_store.insert_calendar_days(CAL)
    run_worker(fake_pro, {}, job_store)

    with StorageConnection(runtime / "storage").connection() as conn:
        types = {
            r["execution_key"]: r["execution_type"]
            for r in conn.execute(
                "SELECT execution_key, execution_type FROM executions"
            ).fetchall()
        }
        links = conn.execute(
            "SELECT source_execution_key, target_execution_key FROM execution_links "
            "WHERE link_type = 'bars_sync_uses_calendar'"
        ).fetchall()

    assert len(links) == 1
    src = links[0]["source_execution_key"]
    tgt = links[0]["target_execution_key"]
    assert types[src] == "market_bars_sync"
    assert types[tgt] == "market_calendar_sync"


def test_execution_rows_record_success(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(CAL[:4])                           # 无缺口 → UPTODATE
    assert run_worker(fake_pro, {}, job_store).status == "success"
    assert _execution_status_by_type(runtime) == {
        "market_bars_sync": "success", "market_calendar_sync": "success"}


def test_execution_rows_record_failure(runtime, job_store, sync_store, fake_pro):
    """登记在起跑时，终态回填在收口时：stage-1 挂掉的轮次不得在审计里是绿的。"""
    sync_store.insert_calendar_days([date(2026, 8, 20)])       # MAX < today
    fake_pro.raise_cal = RuntimeError("Connection aborted")
    assert run_worker(fake_pro, {}, job_store).status == "failed"
    assert _execution_status_by_type(runtime) == {
        "market_bars_sync": "failed", "market_calendar_sync": "failed"}


# ---- R6 / R14：增量 doubtful 与自愈 ----

def test_r6_incremental_doubtful_day_written_not_booked(runtime, job_store,
                                                        sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.day_codes = {d26: CODES, d27: CODES[:2]}  # 27 日只有 2/3 → 0.667 < 0.75
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "doubtful" in ctx.error
    done = sync_store.done_days()
    assert d26 in done and d27 not in done
    assert sync_store.doubtful_days() == [d27]
    assert not sync_store.ledger_suspect()
    bars = runtime / "storage" / "market" / "bars"
    df = pl.read_parquet(bars / "000001.parquet")
    assert set(df["date"].to_list()) == {d26, d27}              # doubtful 日数据照常写盘


def test_r14_doubtful_self_heals_next_round(runtime, job_store, sync_store, fake_pro):
    test_r6_incremental_doubtful_day_written_not_booked(runtime, job_store, sync_store, fake_pro)
    d27 = date(2026, 8, 27)
    fake_pro.day_codes = {d27: CODES}                           # 重拉回升
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "success"
    assert d27 in sync_store.done_days()
    assert sync_store.doubtful_days() == []


# ---- R5：增量中止与漂移 ----

def test_r5_incremental_abort_writes_nothing(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    fake_pro.day_codes = {date(2026, 8, 26): CODES}
    fake_pro.raise_daily = RuntimeError("频率超限")               # env，立即返回不热重试
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "增量中止" in ctx.error
    assert sync_store.done_days() == set(DONE)
    bars = runtime / "storage" / "market" / "bars"
    assert not bars.exists() or not list(bars.glob("*.parquet"))


# ---- R9：BACKFILL_CODES 不碰日账本 ----

def test_r9_backfill_codes_never_touches_ledger(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    sync_store.record_skip_failure("000002", "boom", "code")
    fake_pro.code_days = {"000002": [date(2026, 8, 26), date(2026, 8, 27)]}
    ctx = run_worker(fake_pro, {"codes": ["000002"]}, job_store)
    assert ctx.status == "success"
    assert sync_store.done_days() == set(DONE)                  # INV-3
    assert sync_store.skipped_rows() == []                      # 成功销账
    assert (runtime / "storage" / "market" / "bars" / "000002.parquet").exists()


# ---- R10 / R20：全量换名 ----

def test_r10_full_success_swaps_and_keeps_prev(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    bars = runtime / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [date(2020, 1, 2)], "close": [1.0]}).write_parquet(bars / "OLD.parquet")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "success"
    assert (bars / "000001.parquet").exists()
    assert not (bars / "OLD.parquet").exists()
    assert (runtime / "storage" / "market" / "bars_prev" / "OLD.parquet").exists()
    assert not list((runtime / "storage" / "market" / "staging").glob("*.parquet"))
    assert sync_store.done_days() == set(CAL[:4])


def test_r10_full_cancel_keeps_staging_bars_untouched(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    bars = runtime / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [date(2020, 1, 2)], "close": [1.0]}).write_parquet(bars / "OLD.parquet")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store, cancel_after=1)
    assert ctx.status == "cancelled"
    assert "Cancelled" in ctx.error
    assert (bars / "OLD.parquet").exists()                      # 换名前一字未改
    assert not (runtime / "storage" / "market" / "bars_prev").exists()


def test_r20_second_force_full_pulls_everything_again(runtime, job_store, sync_store, fake_pro):
    test_r10_full_success_swaps_and_keeps_prev(runtime, job_store, sync_store, fake_pro)
    before = fake_pro.daily_calls
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "success"
    assert fake_pro.daily_calls - before >= len(CODES)          # 未被"续传"空转
    prev = runtime / "storage" / "market" / "bars_prev" / "000001.parquet"
    assert prev.exists()                                        # 上一版在 bars_prev


# ---- R7 / R8：熔断与带缺口提交 ----

def test_r7_full_env_breaker_records_nothing(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    fake_pro.code_days = {"000001": CAL[:4]}                    # 1/3 成功 → 66% > 5%
    fake_pro.raise_daily_codes = {"000002", "600000"}           # 其余股限流（立即返回）
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "熔断" in ctx.error
    assert sync_store.skipped_rows() == []                      # env 一律不计次
    assert list((runtime / "storage" / "market" / "staging").glob("*.parquet"))  # staging 保留


def test_incremental_aborts_when_adj_factor_unavailable(runtime, job_store, sync_store,
                                                        fake_pro):
    """因子取不到属环境故障：整批中止，不落账不写盘——绝不写占位 1.0 假绿。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    fake_pro.day_codes = {date(2026, 8, 26): CODES}
    fake_pro.adj_empty = True
    ctx = run_worker(fake_pro, {}, job_store)

    assert ctx.status == "failed"
    assert "增量中止" in ctx.error and "adj_factor" in ctx.error
    assert sync_store.done_days() == set(DONE)                 # 账本未推进
    assert not sync_store.ledger_suspect()
    bars = runtime / "storage" / "market" / "bars"
    assert not bars.exists() or not list(bars.glob("*.parquet"))


def test_full_breaker_when_adj_factor_unavailable(runtime, job_store, sync_store, fake_pro):
    """全量路径：因子全线取不到 → 每只失败 → 熔断，且提前中止不烧完。"""
    sync_store.insert_calendar_days(CAL)
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    fake_pro.adj_empty = True
    ctx = run_worker(fake_pro, {"force": True}, job_store)

    assert ctx.status == "failed"
    assert "整批熔断" in ctx.error
    assert "提前中止" in ctx.error                              # 结局已定即停
    # 熔断在计次之前 return（§3.7 主保险）：系统性故障不给任何一只记失败
    assert sync_store.skipped_rows() == []
    assert sync_store.done_days() == set()


def test_full_single_stock_adj_factor_empty_records_code_strike(
        runtime, job_store, sync_store, fake_pro):
    """健康轮里单只股因子取不到 = code 类：计次入 sync_skipped，带缺口提交被拦下。

    判 env 的话这只股永不入账（service 只记 code/unknown），全量批照常绿灯提交，
    bars 里留一个谁也不知道的洞 —— 出列机制正是为了让它可见、可自愈。
    """
    bad = "000002"
    fake_pro.meta_codes = list(HEALTHY_CODES)
    sync_store.insert_calendar_days(CAL)
    for c in HEALTHY_CODES:
        fake_pro.code_days[c] = CAL[:4]
    fake_pro.adj_empty_codes = {bad}          # 21 只里挂 1 只 = 4.8% ≤ 5% ⇒ 健康轮
    ctx = run_worker(fake_pro, {"force": True}, job_store)

    assert ctx.status == "failed"
    assert "accept_partial_baseline" in ctx.error               # 带缺口提交需显式确认
    rows = sync_store.skipped_rows()
    assert [r["code"] for r in rows] == [bad]
    assert rows[0]["kind"] == "code"                            # 计次，3 轮后出列
    assert "adj_factor" in rows[0]["last_error"]
    assert sync_store.done_days() == set()                      # 未落账


HEALTHY_CODES = [f"{i:06d}" for i in range(1, 22)]   # 21 只：1 只失败 = 4.8% ≤ 5% 健康轮


def _seed_healthy_full(fake_pro, sync_store, bad_code):
    """构造健康轮全量：除 bad_code 外全部成功，bad_code 抛 code 类错误（才计次）。"""
    fake_pro.meta_codes = list(HEALTHY_CODES)
    sync_store.insert_calendar_days(CAL)
    for c in HEALTHY_CODES:
        fake_pro.code_days[c] = CAL[:4]
    fake_pro.raise_daily_codes = {bad_code}
    fake_pro.raise_daily_exc = RuntimeError("参数错误")


def test_r7_three_healthy_rounds_then_excluded(runtime, job_store, sync_store, fake_pro):
    _seed_healthy_full(fake_pro, sync_store, "000002")
    for _ in range(2):   # 前 2 轮：未达 3 次门槛，不得出列
        run_worker(fake_pro, {"force": True, "accept_partial_baseline": True}, job_store)
        assert sync_store.excluded_codes() == []
    run_worker(fake_pro, {"force": True, "accept_partial_baseline": True}, job_store)
    assert sync_store.excluded_codes() == ["000002"]   # 第 3 个健康轮失败 → 出列


def test_r7_success_clears_skip_and_unblocks_commit(runtime, job_store, sync_store, fake_pro):
    """中间任一次成功即计数归零（spec §3.7）：销账后本轮全成功不需 accept_partial_baseline。"""
    fake_pro.meta_codes = list(HEALTHY_CODES)
    sync_store.insert_calendar_days(CAL)
    for c in HEALTHY_CODES:
        fake_pro.code_days[c] = CAL[:4]
    sync_store.record_skip_failure("000002", "old", "code")   # attempts=1
    sync_store.record_skip_failure("000002", "old", "code")   # attempts=2

    ctx = run_worker(fake_pro, {"force": True}, job_store)    # 本轮 21/21 成功

    assert sync_store.skipped_rows() == []                    # 成功即销账，attempts 不残留
    assert ctx.status == "success"                            # 名单已空 → 不卡带缺口提交


def test_r8_env_residue_rejects_partial_baseline(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    # 残留 code 不在有效清单内 → 本轮不拉取、不销账，门槛才可被独立验证
    sync_store.record_skip_failure("999999", "每分钟最多访问该接口", "env")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True, "accept_partial_baseline": True}, job_store)
    assert ctx.status == "failed"
    assert "env" in ctx.error
    assert sync_store.done_days() == set()
    assert not (runtime / "storage" / "market" / "bars" / "000001.parquet").exists()


def test_r8_no_confirm_keeps_staging(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.record_skip_failure("999999", "无此股票", "code")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "accept_partial_baseline" in ctx.error
    assert list((runtime / "storage" / "market" / "staging").glob("*.parquet"))


def test_backfill_submit_rejected_leaves_attempts_alone(runtime, sync_store, fake_pro):
    """被互斥拒绝的请求不得留下副作用（清零只属于真正起跑的 worker）。"""
    import time

    from trendradar.app.jobs.executor import JobExecutor
    from trendradar.app.jobs.persistence import JobStore
    from trendradar.app.services.market_service import submit_market_backfill_codes

    for _ in range(3):
        sync_store.record_skip_failure("000002", "无此股票", "code")

    ex = JobExecutor(JobStore(runtime / "storage" / "app.db"))
    blocking = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    fut = ex._jobs[blocking].future   # 先抓住 future：worker 完成后 _run 会弹出条目
    try:
        with pytest.raises(RuntimeError, match="conflicts"):
            submit_market_backfill_codes(ex, {})
        assert [r["attempts"] for r in sync_store.skipped_rows()] == [3]
    finally:
        fut.result(timeout=5)
        ex.shutdown(wait=True)


def test_backfill_worker_backfills_excluded_list(runtime, job_store, sync_store, fake_pro):
    """通道二不传 codes → 按 attempts>=3 名单补齐；清零必须在取名单之后。"""
    sync_store.insert_calendar_days(CAL)
    for _ in range(3):
        sync_store.record_skip_failure("000002", "无此股票", "code")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]

    ctx = FakeCtx("20260827_180000_market_backfill_codes_t1", job_store)
    service.backfill_codes_worker(ctx, {}, now_cn=NOW)

    assert ctx.status == "success"
    assert (runtime / "storage" / "market" / "bars" / "000002.parquet").exists()
    assert sync_store.skipped_rows() == []          # 成功即销账


# ---- 补齐入口：不可投递的代码必须可见 ----

def test_explicit_backfill_rejects_bse_code(runtime, job_store, sync_store, fake_pro):
    """北交所行情物理不可得：显式请求不能记一句"无可拉代码"就绿灯。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    ctx = run_worker(fake_pro, {"codes": ["920001"]}, job_store)
    assert ctx.status == "failed"
    assert "920001" in ctx.error
    assert "北交所" in ctx.error


def test_explicit_backfill_rejects_code_outside_effective_list(runtime, job_store,
                                                              sync_store, fake_pro):
    ctx = run_worker(fake_pro, {"codes": ["000003"]}, job_store)   # 清单里没有这只
    assert ctx.status == "failed"
    assert "000003" in ctx.error
    assert "有效清单" in ctx.error


def test_explicit_backfill_partial_fails_but_keeps_what_it_fetched(runtime, job_store,
                                                                  sync_store, fake_pro):
    """能拉的照常落盘，终态仍是 failed 且点名拉不到的那只。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    fake_pro.code_days = {"000002": [date(2026, 8, 26)]}
    ctx = run_worker(fake_pro, {"codes": ["000002", "920001"]}, job_store)
    assert ctx.status == "failed"
    assert "920001" in ctx.error
    assert (runtime / "storage" / "market" / "bars" / "000002.parquet").exists()


def test_auto_backfill_skips_unfetchable_without_failing(runtime, job_store, sync_store,
                                                        fake_pro):
    """通道二自动模式取的是 sync_skipped 名单，可能含已出表的历史残留项：
    这类项记为 skipped 但不翻转终态，否则补齐作业被永久卡死。"""
    sync_store.insert_calendar_days(CAL)
    for _ in range(3):
        sync_store.record_skip_failure("000002", "无此股票", "code")
        sync_store.record_skip_failure("920001", "无此股票", "code")
    fake_pro.code_days = {"000002": CAL[:4]}

    ctx = FakeCtx("20260827_180000_market_backfill_codes_t2", job_store)
    service.backfill_codes_worker(ctx, {}, now_cn=NOW)

    assert ctx.status == "success"
    assert [s["code"] for s in ctx.result["skipped"]] == ["920001"]
    assert (runtime / "storage" / "market" / "bars" / "000002.parquet").exists()
    assert [r["code"] for r in sync_store.skipped_rows()] == ["920001"]


# ---- R13：全量批单日语义 ----

def test_r13_full_doubtful_day_swapped_but_not_booked(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    # 26 日只有 2/3 只 → 0.667 < 0.75 → doubtful；其余日齐全
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    fake_pro.code_days["600000"] = [d for d in CAL[:4] if d != date(2026, 8, 26)]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "doubtful" in ctx.error
    done = sync_store.done_days()
    assert date(2026, 8, 26) not in done
    assert done == set(CAL[:4]) - {date(2026, 8, 26)}
    assert sync_store.doubtful_days() == [date(2026, 8, 26)]
    assert not sync_store.ledger_suspect()
    bars = runtime / "storage" / "market" / "bars"
    assert (bars / "600000.parquet").exists()                   # 换名照常入库


# ---- R17：换名成功但账本事务失败 ----

def test_r17_commit_failure_after_swap(runtime, job_store, sync_store, fake_pro, monkeypatch):
    sync_store.insert_calendar_days(CAL)
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]

    def boom(*args, **kwargs):
        raise RuntimeError("txn boom")

    monkeypatch.setattr(service, "commit_full", boom)
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "事务" in ctx.error
    bars = runtime / "storage" / "market" / "bars"
    assert (bars / "000001.parquet").exists()                   # 文件已新
    assert sync_store.done_days() == set()                      # 账本仍旧
    assert not sync_store.ledger_suspect()                      # 不置 suspect
    # 账本为空 → 下轮重判「首次建库」重跑全量，文案不得谎称增量自愈
    assert "首次建库" in ctx.error


def test_r17_full_doubtful_survives_commit_failure(runtime, job_store, sync_store,
                                                   fake_pro, monkeypatch):
    """doubtful 与 done_days 同在 commit_full 一个事务里，回滚不该把它一起吞掉：
    换名后它已在盘上，「确认入账」入口应当轮就可用，而不是等下一轮重算。"""
    sync_store.insert_calendar_days(CAL)
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    # 26 日只有 2/3 只 → 0.667 < 0.75 → doubtful
    fake_pro.code_days["600000"] = [d for d in CAL[:4] if d != date(2026, 8, 26)]

    def boom(*args, **kwargs):
        raise RuntimeError("txn boom")

    monkeypatch.setattr(service, "commit_full", boom)
    ctx = run_worker(fake_pro, {"force": True}, job_store)

    assert ctx.status == "failed"
    assert sync_store.doubtful_days() == [date(2026, 8, 26)]   # 未被事务回滚吞掉
    assert sync_store.done_days() == set()                      # 账本仍未推进


def test_r17_incremental_doubtful_survives_commit_failure(runtime, job_store, sync_store,
                                                          fake_pro, monkeypatch):
    """增量批同一机制：commit_incremental 也把 doubtful 与 done_days 写在一个事务里。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.day_codes = {d26: CODES, d27: CODES[:2]}           # 27 日 0.667 < 0.75

    def boom(*args, **kwargs):
        raise RuntimeError("txn boom")

    monkeypatch.setattr(service, "commit_incremental", boom)
    ctx = run_worker(fake_pro, {}, job_store)                   # 外层兜底 → failed

    assert ctx.status == "failed" and "txn boom" in ctx.error
    assert sync_store.doubtful_days() == [d27]                  # 未被事务回滚吞掉
    assert sync_store.done_days() == set(DONE)                  # 账本仍未推进


# ---- R18：UPTODATE 尾部补齐失败不翻转终态 ----

def test_r18_uptodate_tail_backfill_env_failure_keeps_success(runtime, job_store,
                                                              sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(CAL[:4])                           # 无缺口 → UPTODATE
    sync_store.record_skip_failure("000002", "old", "code")
    sync_store.record_skip_failure("000002", "old", "code")
    sync_store.record_skip_failure("000002", "old", "code")     # attempts=3
    fake_pro.raise_daily_codes = {"000002"}                     # 补齐必挂（限流，立即返回）
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "success"                              # 主作业终态不翻转
    rows = sync_store.skipped_rows()
    assert len(rows) == 1 and rows[0]["code"] == "000002"       # 缺口与失败记录保留


# ---- R21：全量自检失败的 staging 处置 ----

def test_r21_full_staging_corruption_discards(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    staging = runtime / "storage" / "market" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    # 预置一个"续传残留"的坏文件（重复日期 → 断言③失败）
    pl.DataFrame({
        "date": [date(2026, 8, 24), date(2026, 8, 24)],
        "open": [1.0] * 2, "high": [1.0] * 2, "low": [1.0] * 2, "close": [1.0] * 2,
    }).write_parquet(staging / "600000.parquet")
    for c in CODES[:2]:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "③" in ctx.error
    assert sync_store.ledger_suspect()
    assert not list(staging.glob("*.parquet"))                  # 丢弃，不续传坏文件
    # 下一轮从头重拉（含 600000）且成功
    fake_pro.code_days = {c: CAL[:4] for c in CODES}
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "success"


def test_r21_full_readback_missing_day_discards(runtime, job_store, sync_store,
                                                fake_pro, monkeypatch):
    sync_store.insert_calendar_days(CAL)
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    real_readback = service.readback_calendar
    monkeypatch.setattr(service, "readback_calendar",
                        lambda d: real_readback(d) - {date(2026, 8, 25)})
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "②" in ctx.error
    assert sync_store.ledger_suspect()
    assert not list((runtime / "storage" / "market" / "staging").glob("*.parquet"))
