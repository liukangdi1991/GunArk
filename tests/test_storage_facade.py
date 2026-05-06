from __future__ import annotations

from core.storage import AppStorage, SelectionResultInUseError


def test_storage_facade_initializes_schema(tmp_path):
    storage = AppStorage(tmp_path / "storage")

    storage.ensure_ready()

    assert storage.db_path.exists()
    assert storage.objects_root.exists()


def test_storage_facade_exports_selection_in_use_error():
    assert issubclass(SelectionResultInUseError, ValueError)
