from __future__ import annotations


def test_cancel_execution_marks_running_job_as_cancelling(tmp_path, monkeypatch):
    from core.storage import AppStorage
    from web.services import execution_service

    monkeypatch.setattr(execution_service, "storage", AppStorage(tmp_path / "storage"))
    state = execution_service.ExecutionState(
        execution_id="job_cancel_me",
        execution_type="market_data_sync",
        status="running",
        created_at="2026-05-07T10:00:00",
        updated_at="2026-05-07T10:00:00",
        request={},
        progress_current=3,
        progress_total=100,
        progress_message="正在拉取 000001 平安银行",
        started_at="2026-05-07T10:00:01",
    )
    execution_service._execution_dir(state.execution_id).mkdir(parents=True)
    execution_service._log_path(state.execution_id).write_text("", encoding="utf-8")
    execution_service._save_state(state)

    result = execution_service.cancel_execution(state.execution_id)
    saved = execution_service.get_execution(state.execution_id)
    console = execution_service.read_console(state.execution_id, offset=0)

    assert result["status"] == "cancelling"
    assert saved is not None
    assert saved["status"] == "cancelling"
    assert saved["progress_current"] == 3
    assert console is not None
    assert console["more"] is True
    assert "收到停止请求" in console["text"]
