from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

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

logger = logging.getLogger(__name__)

# ─────────────────────────── 配置 ─────────────────────────── #

# 策略类名到 emoji 的映射
STRATEGY_EMOJIS = {
    "BBIKDJSelector": "🔥",
    "SuperB1Selector": "⚡",
    "PeakKDJSelector": "🎫",
    "BBIShortLongSelector": "🕳️",
    "MA60CrossVolumeWaveSelector": "📈",
    "ZXDKXBalanceSelector": "⚖️",
    "PerfectB1Selector": "🌟",
    "BigBullishVolumeSelector": "💪",
    "VolumeSpikeBalanceSelector": "⚖️",
}

# 类名到类的映射
SELECTOR_CLASSES = {
    "BBIKDJSelector": BBIKDJSelector,
    "SuperB1Selector": SuperB1Selector,
    "PeakKDJSelector": PeakKDJSelector,
    "BBIShortLongSelector": BBIShortLongSelector,
    "MA60CrossVolumeWaveSelector": MA60CrossVolumeWaveSelector,
    "ZXDKXBalanceSelector": ZXDKXBalanceSelector,
    "PerfectB1Selector": PerfectB1Selector,
    "BigBullishVolumeSelector": BigBullishVolumeSelector,
    "VolumeSpikeBalanceSelector": VolumeSpikeBalanceSelector,
}

FALLBACK_DEFAULT_STRATEGY_ALIASES = ["B1战法", "B1战法（V2）"]


def load_config_raw(cfg_path: Path) -> Any:
    """读取 configs.json 原始结构。"""
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as f:
        return json.load(f)


def load_default_strategy_aliases(cfg_path: Path) -> List[str]:
    """从配置读取默认策略；未配置时回退到内置默认值。"""
    cfg_raw = load_config_raw(cfg_path)
    if isinstance(cfg_raw, dict):
        aliases = cfg_raw.get("default_strategies")
        if isinstance(aliases, list):
            normalized = [str(x).strip() for x in aliases if str(x).strip()]
            if normalized:
                return normalized
    return list(FALLBACK_DEFAULT_STRATEGY_ALIASES)


def load_config(cfg_path: Path) -> List[Dict[str, Any]]:
    """从 configs.json 加载策略配置"""
    cfg_raw = load_config_raw(cfg_path)

    # 兼容三种结构：单对象、对象数组、或带 selectors 键
    if isinstance(cfg_raw, list):
        cfgs = cfg_raw
    elif isinstance(cfg_raw, dict) and "selectors" in cfg_raw:
        cfgs = cfg_raw["selectors"]
    else:
        cfgs = [cfg_raw]

    if not cfgs:
        raise ValueError("configs.json 未定义任何 Selector")

    return cfgs


def instantiate_selector(cfg: Dict[str, Any]):
    """动态加载 Selector 类并实例化"""
    cls_name: str = cfg.get("class")
    if not cls_name:
        raise ValueError("缺少 class 字段")

    cls = SELECTOR_CLASSES.get(cls_name)
    if cls is None:
        raise ImportError(f"未知的 Selector 类: {cls_name}")

    params = cfg.get("params", {})
    alias = cfg.get("alias", cls_name)
    emoji = STRATEGY_EMOJIS.get(cls_name, "📊")
    return alias, cls(**params), emoji


def load_strategies_from_config(cfg_path: Path) -> Dict[str, Dict[str, Any]]:
    """从配置文件加载策略，返回 {策略名: {selector, emoji}} 结构。"""
    cfgs = load_config(cfg_path)
    strategies = {}
    for cfg in cfgs:
        if cfg.get("activate", True) is False:
            continue
        try:
            alias, selector, emoji = instantiate_selector(cfg)
            strategies[alias] = {
                "selector": selector,
                "emoji": emoji,
            }
        except Exception as e:
            logger.warning("跳过无效策略配置 %s: %s", cfg, e)
    return strategies
