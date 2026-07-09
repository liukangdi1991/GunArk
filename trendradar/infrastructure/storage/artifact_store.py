import json
import hashlib
from pathlib import Path


class ArtifactStore:
    def __init__(self, storage_root: Path):
        self.objects_root = storage_root / "objects"

    def _exec_dir(self, execution_key: str) -> Path:
        return self.objects_root / "executions" / execution_key

    def write_json(self, execution_key: str, artifact_type: str, data: dict) -> str:
        path = self._exec_dir(execution_key) / artifact_type / "data.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, default=str)
        path.write_text(content, encoding="utf-8")
        return self._storage_key(path)

    def _storage_key(self, path: Path) -> str:
        return str(path.relative_to(self.objects_root))
