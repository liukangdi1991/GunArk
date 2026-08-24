from pathlib import Path


class ArtifactStore:
    """Artifact storage root; SignalRepository writes signals via objects_root."""

    def __init__(self, storage_root: Path):
        self.objects_root = storage_root / "objects"
