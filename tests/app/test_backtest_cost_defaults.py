"""`_build_config` 的兜底费率必须等于 `CostConfig` 的默认费率。

两处各写一份字面量就会漂移：2026-09-05 之前 `_build_config` 的卖出印花税兜底是
0.0001，而 `CostConfig` 是 0.0005（现行 A 股税率）。因为服务提交路径永远显式传
`CostConfig(...)`，正确那份默认值对真实任务不可达——所有实跑回测的卖出成本少收 4bp。
兜底值本身保留（`_build_config` 明确列出默认值是可读的），这条测试只钉住"两处一致"。
"""

from dataclasses import fields

import pytest

from trendradar.app.services.backtest_service import _build_config
from trendradar.domain.backtest.config import CostConfig


@pytest.mark.parametrize("field", fields(CostConfig), ids=lambda f: f.name)
def test_build_config_cost_fallback_matches_dataclass_default(field):
    assert getattr(_build_config({}).costs, field.name) == field.default, field.name


def test_stamp_duty_matches_current_a_share_rate():
    """现行 A 股卖出印花税 0.05%（2023-08-28 起，之前 0.1%），不是万1。"""
    assert _build_config({}).costs.stamp_duty_rate_sell == pytest.approx(0.0005)


def test_request_override_still_wins():
    """钉住兜底没把覆盖能力吃掉：低佣金账户这类自定义费率仍可传入。"""
    costs = _build_config({"costs": {
        "commission_rate": 0.0001, "stamp_duty_rate_sell": 0.001,
    }}).costs
    assert costs.commission_rate == pytest.approx(0.0001)
    assert costs.stamp_duty_rate_sell == pytest.approx(0.001)
    assert costs.transfer_fee_rate == pytest.approx(
        CostConfig().transfer_fee_rate)   # 没覆盖的仍走默认
