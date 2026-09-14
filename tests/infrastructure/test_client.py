"""get_pro 的 token 处理契约（复审 E2E 后加固，2026-09-14 实际事故驱动）。

事故：服务进程未带 TUSHARE_TOKEN 时，get_pro 无条件 ts.set_token("")，
把 tushare 自存凭据 ~/tk.csv 清成空 token——任何页面快照请求都会触发。
"""

from datetime import date

import pandas as pd
import pytest

from trendradar.infrastructure.tushare import client


class _RecordingTushare:
    """记录 set_token 调用的假 tushare 模块。"""

    def __init__(self):
        self.set_token_calls: list[str] = []
        self.pro_kwargs: list[dict] = []

    def set_token(self, token: str) -> None:
        self.set_token_calls.append(token)

    def pro_api(self, **kwargs):
        self.pro_kwargs.append(kwargs)
        return "PRO"


@pytest.fixture()
def ts_rec(monkeypatch) -> _RecordingTushare:
    rec = _RecordingTushare()
    monkeypatch.setattr(client, "ts", rec)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    return rec


def test_env_token_is_set(monkeypatch):
    """env 有 token → 照常 set_token。"""
    rec = _RecordingTushare()
    monkeypatch.setattr(client, "ts", rec)
    monkeypatch.setenv("TUSHARE_TOKEN", "abc123")
    assert client.get_pro() == "PRO"
    assert rec.set_token_calls == ["abc123"]


def test_env_token_unset_preserves_stored_credentials(ts_rec):
    """加固项（复审 E2E 后）：env 无 token 时**不得**调用 set_token——
    旧实现 set_token("") 会把 ~/tk.csv 里已存的凭据清空。"""
    assert client.get_pro() == "PRO"
    assert ts_rec.set_token_calls == []
