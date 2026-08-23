from trendradar.domain.strategy.models import StrategyDefinition
from trendradar.domain.strategy.registry import register
from trendradar.domain.strategy.selectors.bbi_kdj_b1 import BBIKDJSelector
from trendradar.domain.strategy.selectors.super_b1 import SuperB1Selector
from trendradar.domain.strategy.selectors.bbi_short_long import BBIShortLongSelector
from trendradar.domain.strategy.selectors.peak_kdj import PeakKDJSelector
from trendradar.domain.strategy.selectors.ma60_volume_wave import MA60VolumeWaveSelector
from trendradar.domain.strategy.selectors.zxdkx_balance import ZXDKXBalanceSelector
from trendradar.domain.strategy.selectors.perfect_b1 import PerfectB1Selector
from trendradar.domain.strategy.selectors.big_bullish_volume import BigBullishVolumeSelector
from trendradar.domain.strategy.selectors.volume_spike_balance import VolumeSpikeBalanceSelector


def register_all():
    register(StrategyDefinition(
        strategy_id="bbi_kdj_b1", name="B1战法",
        description="BBI 上行 + KDJ 低位 + 趋势过滤",
        selector_class=BBIKDJSelector,
        default_params={"j_threshold": 15, "bbi_min_window": 20, "max_window": 120,
                        "bbi_q_threshold": 0.2, "j_q_threshold": 0.10},
    ))

    register(StrategyDefinition(
        strategy_id="super_b1", name="SuperB1战法",
        description="缩量下跌 + KDJ 低位反弹",
        selector_class=SuperB1Selector,
        default_params={"lookback_n": 10, "close_vol_pct": 0.02,
                        "price_drop_pct": 0.02, "j_threshold": 10},
    ))

    register(StrategyDefinition(
        strategy_id="bbi_short_long", name="补票战法",
        description="短期均线金叉 + BBI 上行",
        selector_class=BBIShortLongSelector,
        default_params={"n_short": 5, "n_long": 21, "m": 5,
                        "bbi_min_window": 2, "max_window": 120},
    ))

    register(StrategyDefinition(
        strategy_id="peak_kdj", name="填坑战法",
        description="KDJ 低位 + 高波动 + 大回撤",
        selector_class=PeakKDJSelector,
        default_params={"j_threshold": 10, "max_window": 120,
                        "fluc_threshold": 0.03, "gap_threshold": 0.2},
    ))

    register(StrategyDefinition(
        strategy_id="ma60_volume_wave", name="上穿60放量战法",
        description="放量突破 MA60 + KDJ 低位",
        selector_class=MA60VolumeWaveSelector,
        default_params={"lookback_n": 25, "vol_multiple": 1.8, "j_threshold": 15},
    ))

    register(StrategyDefinition(
        strategy_id="zxdkx_balance", name="多空平衡选股策略",
        description="ZX 粘合 + 收盘在长线下方",
        selector_class=ZXDKXBalanceSelector,
        default_params={"zx_stick_limit_threshold": 0.04,
                        "close_vs_long_term_bull_bear_line_limit_threshold": 0.95},
    ))

    register(StrategyDefinition(
        strategy_id="perfect_b1_v2", name="B1战法（V2）",
        description="完美 B1：KDJ 低位 + 振幅限制 + 涨跌幅限制",
        selector_class=PerfectB1Selector,
        default_params={"j_threshold": 13, "amplitude_limit": 0.07,
                        "pct_chg_upper": 0.02, "pct_chg_lower": -0.02},
    ))

    register(StrategyDefinition(
        strategy_id="big_bullish_volume", name="暴力K战法",
        description="大阳线 + 小上影 + 倍量",
        selector_class=BigBullishVolumeSelector,
        default_params={"up_pct_threshold": 0.06, "upper_wick_pct_max": 0.02,
                        "vol_multiple": 2.5},
    ))

    register(StrategyDefinition(
        strategy_id="volume_spike_balance", name="倍量多空平衡策略",
        description="倍量信号后缩量回调至长线下方",
        selector_class=VolumeSpikeBalanceSelector,
        default_params={"volume_spike_lookback": 30, "volume_spike_multiple": 2.0,
                        "min_spike_elapsed_days": 20},
    ))
