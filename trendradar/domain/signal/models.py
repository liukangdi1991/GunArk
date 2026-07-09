from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
import json


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_name: str
    group_ids: list[str] = field(default_factory=list)
    primary_group_id: str = ""
    signal_date: date | None = None
    codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SignalSet:
    schema_version: str = "2.0"
    execution_key: str = ""
    signal_from: date | None = None
    signal_to: date | None = None
    strategies_snapshot: list[dict] = field(default_factory=list)
    strategy_groups_snapshot: list[dict] = field(default_factory=list)
    signals: list[StrategySignal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "execution_key": self.execution_key,
            "signal_from": str(self.signal_from) if self.signal_from else None,
            "signal_to": str(self.signal_to) if self.signal_to else None,
            "strategies_snapshot": self.strategies_snapshot,
            "strategy_groups_snapshot": self.strategy_groups_snapshot,
            "signals": [
                {
                    "strategy_id": s.strategy_id,
                    "strategy_name": s.strategy_name,
                    "group_ids": s.group_ids,
                    "primary_group_id": s.primary_group_id,
                    "signal_date": str(s.signal_date) if s.signal_date else None,
                    "codes": s.codes,
                }
                for s in self.signals
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> SignalSet:
        signals = [
            StrategySignal(
                strategy_id=s["strategy_id"],
                strategy_name=s["strategy_name"],
                group_ids=s.get("group_ids", []),
                primary_group_id=s.get("primary_group_id", ""),
                signal_date=date.fromisoformat(s["signal_date"]) if s.get("signal_date") else None,
                codes=s.get("codes", []),
            )
            for s in data.get("signals", [])
        ]
        return cls(
            schema_version=data.get("schema_version", "2.0"),
            execution_key=data.get("execution_key", ""),
            signal_from=date.fromisoformat(data["signal_from"]) if data.get("signal_from") else None,
            signal_to=date.fromisoformat(data["signal_to"]) if data.get("signal_to") else None,
            strategies_snapshot=data.get("strategies_snapshot", []),
            strategy_groups_snapshot=data.get("strategy_groups_snapshot", []),
            signals=signals,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> SignalSet:
        return cls.from_dict(json.loads(text))
