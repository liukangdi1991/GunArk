from __future__ import annotations

from typing import Any

from selection.selectors import (
    BBIKDJSelector,
    BBIShortLongSelector,
    BigBullishVolumeSelector,
    MA60CrossVolumeWaveSelector,
    PeakKDJSelector,
    PerfectB1Selector,
    SuperB1Selector,
    VolumeSpikeBalanceSelector,
    ZXDKXBalanceSelector,
)
from selection.execution.base import StrategySelectionRunner
from selection.execution.runners.bbi_kdj import BBIKDJSelectionRunner
from selection.execution.default import DefaultSelectionRunner
from selection.execution.runners import (
    BBIShortLongSelectionRunner,
    BigBullishVolumeSelectionRunner,
    MA60CrossVolumeWaveSelectionRunner,
    PeakKDJSelectionRunner,
    PerfectB1SelectionRunner,
    SuperB1SelectionRunner,
    VolumeSpikeBalanceSelectionRunner,
    ZXDKXBalanceSelectionRunner,
)


def build_strategy_runner(selector: Any) -> StrategySelectionRunner:
    """根据 selector 类型构建对应 Runner。"""
    if isinstance(selector, BBIKDJSelector):
        return BBIKDJSelectionRunner(selector)
    if isinstance(selector, SuperB1Selector):
        return SuperB1SelectionRunner(selector)
    if isinstance(selector, PeakKDJSelector):
        return PeakKDJSelectionRunner(selector)
    if isinstance(selector, BBIShortLongSelector):
        return BBIShortLongSelectionRunner(selector)
    if isinstance(selector, MA60CrossVolumeWaveSelector):
        return MA60CrossVolumeWaveSelectionRunner(selector)
    if isinstance(selector, ZXDKXBalanceSelector):
        return ZXDKXBalanceSelectionRunner(selector)
    if isinstance(selector, PerfectB1Selector):
        return PerfectB1SelectionRunner(selector)
    if isinstance(selector, BigBullishVolumeSelector):
        return BigBullishVolumeSelectionRunner(selector)
    if isinstance(selector, VolumeSpikeBalanceSelector):
        return VolumeSpikeBalanceSelectionRunner(selector)
    return DefaultSelectionRunner(selector)
