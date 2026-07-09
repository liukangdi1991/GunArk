from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

from core.storage_models import ExecutionItemType, ExecutionType


def ensure_execution_type(value: ExecutionType | str) -> ExecutionType:
    try:
        return value if isinstance(value, ExecutionType) else ExecutionType(str(value))
    except ValueError as exc:
        raise ValueError(f"非法 execution type: {value}") from exc


def ensure_execution_item_type(value: ExecutionItemType | str) -> ExecutionItemType:
    try:
        return value if isinstance(value, ExecutionItemType) else ExecutionItemType(str(value))
    except ValueError as exc:
        raise ValueError(f"非法 execution item type: {value}") from exc


def execution_type_from_legacy(value: str) -> ExecutionType:
    return ExecutionType.SELECTION if str(value) == "selection" else ExecutionType.BACKTEST


def snapshots_in_order(strategy_names: list[str], strategy_snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {
        str(item.get("name") or ""): item
        for item in strategy_snapshots
        if isinstance(item, dict)
    }
    return [
        by_name.get(name, {"name": name, "class": "", "description": "", "params": {}})
        for name in strategy_names
    ]


def selection_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selected_count": summary.get("count", 0),
        "elapsed_seconds": summary.get("elapsed_seconds", 0.0),
    }


def backtest_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "strategy"}


def item_to_strategy_snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("item_name") or item.get("item_key") or "",
        "class": item.get("item_class") or "",
        "description": item.get("description") or "",
        "params": dict(item.get("params") or {}),
    }


def serialize_value(value: Any) -> tuple[str | None, str]:
    value = native_scalar(value)
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return "true" if value else "false", "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value), "integer"
    if isinstance(value, float):
        return repr(float(value)), "number"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str), "json"
    return str(value), "string"


def deserialize_value(value: Any, value_type: Any) -> Any:
    if value_type == "null":
        return None
    if value is None:
        return None
    text = str(value)
    if value_type == "boolean":
        return text.lower() == "true"
    if value_type == "integer":
        try:
            return int(text)
        except ValueError:
            return text
    if value_type == "number":
        try:
            return float(text)
        except ValueError:
            parsed = parse_legacy_numpy_scalar(text)
            return parsed if parsed is not None else text
    if value_type == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    parsed = parse_legacy_numpy_scalar(text)
    return parsed if parsed is not None else text


def native_scalar(value: Any) -> Any:
    if is_numpy_scalar(value):
        try:
            return value.item()
        except Exception:
            return value
    return value


def is_numpy_scalar(value: Any) -> bool:
    module = type(value).__module__
    return module == "numpy" or module.startswith("numpy.")


def parse_legacy_numpy_scalar(text: str) -> float | int | bool | None:
    normalized = text.strip()
    if normalized in {"np.True_", "np.bool_(True)"}:
        return True
    if normalized in {"np.False_", "np.bool_(False)"}:
        return False

    match = re.fullmatch(r"np\.(?:float\d*|int\d*)\(([-+0-9.eE]+)\)", normalized)
    if not match:
        return None
    raw = match.group(1)
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if normalized.startswith("np.int") and value.is_integer() else value


def coerce_metric(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value


def delete_result(run_type: str, *, requested: int, deleted: int) -> dict[str, Any]:
    return {
        "result_type": run_type,
        "requested": requested,
        "deleted": deleted,
        "missing": [],
        "file_errors": [],
    }


def normalize_execution_keys(execution_keys: list[str] | None) -> list[str] | None:
    if execution_keys is None:
        return None
    normalized = []
    seen = set()
    for value in execution_keys:
        execution_key = str(value or "").strip()
        if not execution_key or execution_key in seen:
            continue
        normalized.append(execution_key)
        seen.add(execution_key)
    return normalized


def placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def dedupe_storage_keys(storage_keys: list[str]) -> list[str]:
    normalized = [key for key in dict.fromkeys(storage_keys) if key]
    directories = [key.rstrip("/") for key in normalized]
    result = []
    for key in normalized:
        clean_key = key.rstrip("/")
        if any(clean_key != directory and clean_key.startswith(f"{directory}/") for directory in directories):
            continue
        result.append(key)
    return result


def scoped_dir_key(object_dir_key: str, run_type: str) -> str:
    clean_key = str(object_dir_key or "").rstrip("/")
    if not clean_key:
        return ""
    if run_type not in {ExecutionType.SELECTION.value, ExecutionType.BACKTEST.value}:
        return clean_key
    return f"{clean_key}/{run_type}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
