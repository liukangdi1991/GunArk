from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from web.core.config import storage
from web.schemas.backtest import BacktestFromSelectionRequest, BacktestRequest, SelectionBacktestRequest
from web.schemas.execution import ExecutionRequest
from web.schemas.market import FetchMarketRequest
from web.schemas.selection import BatchSelectionRequest, SelectionRequest
from web.services import backtest_service, market_service, selection_service


ExecutionWorker = Callable[["ExecutionContext"], dict[str, Any]]

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gunark-exec")
_lock = RLock()


@dataclass
class ExecutionState:
    execution_id: str
    execution_type: str
    status: str
    created_at: str
    updated_at: str
    request: dict[str, Any]
    progress_current: int = 0
    progress_total: int = 0
    progress_message: str = "等待执行"
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    result_url: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionContext:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    def start(self) -> None:
        now = _now()
        state = _load_state(self.execution_id)
        state.status = "running"
        state.started_at = now
        state.updated_at = now
        state.progress_message = "开始执行"
        _save_state(state)
        self.log("开始执行")

    def update(self, current: int, total: int, message: str) -> None:
        state = _load_state(self.execution_id)
        state.progress_current = max(0, int(current))
        state.progress_total = max(0, int(total))
        state.progress_message = message
        state.updated_at = _now()
        _save_state(state)

    def log(self, message: str, level: str = "INFO") -> None:
        _append_log(self.execution_id, message, level=level)

    def finish(self, result: dict[str, Any]) -> None:
        now = _now()
        state = _load_state(self.execution_id)
        state.status = "success"
        state.finished_at = now
        state.updated_at = now
        state.progress_current = state.progress_total
        state.progress_message = "执行完成"
        state.result = result
        result_meta = _build_result_meta(state.execution_type, result)
        state.resource_type = result_meta["resource_type"]
        state.resource_id = result_meta["resource_id"]
        state.result_url = result_meta["result_url"]
        _save_state(state)
        self.log("执行完成")
        if state.result_url:
            self.log(f"结果页面: {state.result_url}")

    def fail(self, error: str) -> None:
        now = _now()
        state = _load_state(self.execution_id)
        state.status = "failed"
        state.finished_at = now
        state.updated_at = now
        state.progress_message = "执行失败"
        state.error_message = error
        _save_state(state)
        self.log(f"执行失败: {error}", level="ERROR")


def submit_execution(payload: ExecutionRequest) -> dict[str, Any]:
    params = dict(payload.params or {})
    if payload.type == "selection_latest":
        return submit_selection(
            SelectionRequest(
                date=None,
                strategies=params.get("strategies"),
                tickers=params.get("tickers"),
            ),
            execution_type="selection_latest",
        )
    if payload.type == "selection_single":
        return submit_selection(SelectionRequest(**params))
    if payload.type == "selection_batch":
        return submit_batch_selection(BatchSelectionRequest(**params))
    if payload.type == "backtest":
        return submit_backtest(BacktestRequest(**params))
    if payload.type == "backtest_from_selection":
        return submit_backtest_from_selection(BacktestFromSelectionRequest(**params))
    if payload.type == "selection_backtest":
        return submit_selection_backtest(SelectionBacktestRequest(**params))
    if payload.type == "market_data_sync":
        return submit_market_fetch(
            FetchMarketRequest(**params),
            execution_type="market_data_sync",
        )
    raise ValueError(f"不支持的执行类型: {payload.type}")


def submit_selection(
    payload: SelectionRequest,
    *,
    execution_type: str = "selection_single",
) -> dict[str, Any]:
    return _submit(
        execution_type=execution_type,
        request=_payload_to_dict(payload),
        worker=lambda ctx: selection_service.create_selection(
            payload,
            progress=ctx.update,
            log=ctx.log,
        ),
    )


def submit_batch_selection(payload: BatchSelectionRequest) -> dict[str, Any]:
    return _submit(
        execution_type="selection_batch",
        request=_payload_to_dict(payload),
        worker=lambda ctx: selection_service.create_batch_selection(
            payload,
            progress=ctx.update,
            log=ctx.log,
        ),
    )


def submit_backtest(payload: BacktestRequest) -> dict[str, Any]:
    return _submit(
        execution_type="backtest",
        request=_payload_to_dict(payload),
        worker=lambda ctx: backtest_service.create_backtest(
            payload,
            progress=ctx.update,
            log=ctx.log,
        ),
    )


def submit_backtest_from_selection(payload: BacktestFromSelectionRequest) -> dict[str, Any]:
    return _submit(
        execution_type="backtest_from_selection",
        request=_payload_to_dict(payload),
        worker=lambda ctx: backtest_service.create_backtest_from_selection(
            payload,
            progress=ctx.update,
            log=ctx.log,
        ),
    )


def submit_selection_backtest(payload: SelectionBacktestRequest) -> dict[str, Any]:
    return _submit(
        execution_type="selection_backtest",
        request=_payload_to_dict(payload),
        worker=lambda ctx: backtest_service.create_selection_backtest(
            payload,
            progress=ctx.update,
            log=ctx.log,
        ),
    )


def submit_market_fetch(
    payload: FetchMarketRequest,
    *,
    execution_type: str = "market_fetch",
) -> dict[str, Any]:
    return _submit(
        execution_type=execution_type,
        request=_payload_to_dict(payload),
        worker=lambda ctx: market_service.fetch_market_data(
            payload,
            progress=ctx.update,
            log=ctx.log,
        ),
    )


def get_execution(execution_id: str) -> dict[str, Any] | None:
    try:
        return _load_state(execution_id).to_dict()
    except FileNotFoundError:
        return None


def read_console(execution_id: str, offset: int = 0) -> dict[str, Any] | None:
    try:
        state = _load_state(execution_id)
    except FileNotFoundError:
        return None

    log_path = _log_path(execution_id)
    log_path.touch(exist_ok=True)
    size = log_path.stat().st_size
    safe_offset = max(0, min(int(offset), size))
    with log_path.open("rb") as f:
        f.seek(safe_offset)
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    new_offset = safe_offset + len(raw)
    return {
        "execution": state.to_dict(),
        "offset": new_offset,
        "more": state.status in {"queued", "running"},
        "text": text,
    }


def _submit(
    *,
    execution_type: str,
    request: dict[str, Any],
    worker: ExecutionWorker,
) -> dict[str, Any]:
    execution_id = _build_execution_id(execution_type)
    now = _now()
    _execution_dir(execution_id).mkdir(parents=True, exist_ok=True)
    _log_path(execution_id).write_text("", encoding="utf-8")
    state = ExecutionState(
        execution_id=execution_id,
        execution_type=execution_type,
        status="queued",
        created_at=now,
        updated_at=now,
        request=request,
    )
    _save_state(state)
    _append_log(execution_id, f"任务已提交: {execution_type}")
    _executor.submit(_execute, execution_id, worker)
    payload = state.to_dict()
    payload["console_url"] = f"/console/{execution_id}"
    return payload


def _execute(execution_id: str, worker: ExecutionWorker) -> None:
    ctx = ExecutionContext(execution_id)
    ctx.start()
    try:
        result = worker(ctx)
    except Exception as exc:  # noqa: BLE001 - errors should be visible in console.
        ctx.fail(str(exc))
        return
    ctx.finish(result)


def _execution_dir(execution_id: str) -> Path:
    return storage.objects_root / "jobs" / execution_id


def _state_path(execution_id: str) -> Path:
    return _execution_dir(execution_id) / "state.json"


def _log_path(execution_id: str) -> Path:
    return _execution_dir(execution_id) / "console.log"


def _save_state(state: ExecutionState) -> None:
    with _lock:
        state_path = _state_path(state.execution_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_name(f"{state_path.name}.tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(state_path)


def _load_state(execution_id: str) -> ExecutionState:
    state_path = _state_path(execution_id)
    if not state_path.exists():
        raise FileNotFoundError(execution_id)
    for attempt in range(3):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            break
        except json.JSONDecodeError:
            if attempt == 2:
                raise
            time.sleep(0.05)
    return ExecutionState(**payload)


def _append_log(execution_id: str, message: str, level: str = "INFO") -> None:
    line = f"{_now()} [{level.upper()}] {message}\n"
    with _lock:
        log_path = _log_path(execution_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)


def _build_result_meta(execution_type: str, result: dict[str, Any]) -> dict[str, str | None]:
    if execution_type in {"backtest", "backtest_from_selection", "selection_backtest"} and result.get("execution_key"):
        execution_key = str(result["execution_key"])
        return {
            "resource_type": "backtest_result",
            "resource_id": execution_key,
            "result_url": f"/backtests/{execution_key}",
        }
    if execution_type in {"selection_latest", "selection_single"} and result.get("execution_key"):
        execution_key = str(result["execution_key"])
        return {
            "resource_type": "selection_result",
            "resource_id": execution_key,
            "result_url": f"/selections/{execution_key}",
        }
    if execution_type == "selection_batch":
        results = result.get("results") or []
        if results:
            execution_key = results[-1].get("execution_key")
            if execution_key:
                return {
                    "resource_type": "selection_result",
                    "resource_id": str(execution_key),
                    "result_url": f"/selections/{execution_key}",
                }
    if execution_type in {"market_data_sync", "market_fetch"}:
        return {
            "resource_type": "market_data",
            "resource_id": None,
            "result_url": "/market-data",
        }
    return {
        "resource_type": None,
        "resource_id": None,
        "result_url": None,
    }


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(by_alias=True)
    return payload.dict(by_alias=True)


def _build_execution_id(execution_type: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid4().hex[:8]
    return f"{ts}_{execution_type}_{suffix}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
