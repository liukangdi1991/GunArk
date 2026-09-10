import pytest


@pytest.fixture
def patching_sleep(monkeypatch):
    """屏蔽 fetch 层限频退避的真实 sleep（复审 P1：service 侧用例共用）。"""
    import trendradar.infrastructure.tushare.fetch as fetch_module
    monkeypatch.setattr(fetch_module.time, "sleep", lambda s: None)
