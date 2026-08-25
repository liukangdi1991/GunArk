from __future__ import annotations
from pathlib import Path
from trendradar.domain.signal.models import SignalSet
from trendradar.infrastructure.storage.artifact_store import ArtifactStore


class SignalRepository:
    def __init__(self, artifact_store: ArtifactStore):
        self._store = artifact_store

    def _path_for(self, execution_key: str) -> Path:
        if "/" in execution_key or "\\" in execution_key or ".." in execution_key:
            raise ValueError(f"Invalid execution_key: {execution_key!r}")
        return self._store.objects_root / "executions" / execution_key / "selection" / "signals.json"

    def save(self, signal_set: SignalSet, execution_key: str) -> str:
        path = self._path_for(execution_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(signal_set.to_json(), encoding="utf-8")
        return str(path)

    def load(self, execution_key: str) -> SignalSet | None:
        path = self._path_for(execution_key)
        if not path.exists():
            return None
        return SignalSet.from_json(path.read_text(encoding="utf-8"))
