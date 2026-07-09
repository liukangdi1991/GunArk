import argparse
import pytest


def test_init_v2_creates_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.cli import cmd_init_v2

    args = argparse.Namespace(reset_runtime=False, confirm_reset=False)
    cmd_init_v2(args)

    root = storage_root()
    assert (root / "app.db").exists()
    assert (root / "market" / "bars").is_dir()
    assert (root / "objects" / "jobs").is_dir()


def test_init_v2_reset_requires_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    from trendradar.cli import cmd_init_v2

    args = argparse.Namespace(reset_runtime=True, confirm_reset=False)
    with pytest.raises(SystemExit):
        cmd_init_v2(args)


def test_reset_deletes_old_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "trendradar.cli.source_root",
        lambda: tmp_path,
    )
    from trendradar.cli import cmd_init_v2

    args = argparse.Namespace(reset_runtime=False, confirm_reset=False)
    cmd_init_v2(args)

    (tmp_path / "db").mkdir(exist_ok=True)
    (tmp_path / "db" / "test.parquet").write_text("old")
    (tmp_path / "storage" / "app.db").write_text("should be deleted")

    args = argparse.Namespace(reset_runtime=True, confirm_reset=True)
    cmd_init_v2(args)

    assert not (tmp_path / "db").exists()
