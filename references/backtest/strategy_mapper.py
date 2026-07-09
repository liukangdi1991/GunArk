from __future__ import annotations

import json
import re
from functools import lru_cache

from core.runtime import runtime_root


@lru_cache(maxsize=1)
def _load_alias_to_class() -> dict[str, str]:
    cfg_path = runtime_root() / "configs.json"
    if not cfg_path.exists():
        return {}
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}

    selectors = payload.get("selectors", []) if isinstance(payload, dict) else []
    mapping: dict[str, str] = {}
    for item in selectors:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias", "")).strip()
        cls_name = str(item.get("class", "")).strip()
        if alias and cls_name:
            mapping[alias] = cls_name
    return mapping


def strategy_english_name(strategy_name: str) -> str:
    mapping = _load_alias_to_class()
    if strategy_name in mapping:
        return mapping[strategy_name]

    ascii_text = strategy_name.encode("ascii", errors="ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    if slug:
        return slug
    return "UnknownStrategy"
