from __future__ import annotations

import json
from typing import Any

from web.core.config import ROOT


def list_strategies() -> dict[str, Any]:
    config_path = ROOT / "configs.json"
    if not config_path.exists():
        return {"strategies": []}

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    selectors = payload.get("selectors", []) if isinstance(payload, dict) else []
    strategies = []
    for item in selectors:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias", "")).strip()
        if not alias:
            continue
        strategies.append(
            {
                "name": alias,
                "class": str(item.get("class", "")).strip(),
                "description": str(item.get("_comment", "")).strip(),
                "params": item.get("params", {}),
            }
        )
    return {"strategies": strategies}
