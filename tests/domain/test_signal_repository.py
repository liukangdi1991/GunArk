"""SignalRepository must reject traversal-style execution keys.

The GET result/report routes and POST /api/backtests all load signals through
SignalRepository._path_for; a malicious key like "../other" must never be
joined into a filesystem path. (HTTP-layer probes with %2F cannot be driven
through TestClient — its ASGI transport decodes the path — but the guard is
verified at the domain layer here and at the route layer via real ASGI.)
"""

import pytest

from trendradar.domain.signal.repository import SignalRepository
from trendradar.infrastructure.storage.artifact_store import ArtifactStore


@pytest.mark.parametrize("bad_key", ["../foo", "a/b", "a\\b", "..", "..%2Ffoo"])
def test_repository_rejects_traversal_keys(tmp_path, bad_key):
    repo = SignalRepository(ArtifactStore(tmp_path / "storage"))
    with pytest.raises(ValueError):
        repo.load(bad_key)


def test_repository_accepts_normal_key(tmp_path):
    repo = SignalRepository(ArtifactStore(tmp_path / "storage"))
    # normal keys simply resolve to a non-existent file (returns None)
    assert repo.load("20260820_100000_selection_a1b2") is None
