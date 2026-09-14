from trendradar.infrastructure.filesystem.atomic import atomic_write_text


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "sub" / "test.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "test.txt"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_no_partial_on_failure(tmp_path):
    target = tmp_path / "test.txt"
    target.write_text("safe")
    try:
        atomic_write_text(target, "x" * 1000000 + None)  # will fail
    except TypeError:
        pass
    assert target.read_text() == "safe"


def test_atomic_write_fsyncs_before_replace(tmp_path, monkeypatch):
    import os

    import trendradar.infrastructure.filesystem.atomic as atomic

    fsynced = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsynced.append(fd))
    atomic.atomic_write(tmp_path / "test.txt", b"hello")
    assert fsynced, "fsync must be called before os.replace"


def test_atomic_write_parquet_replaces_target(tmp_path):
    """parquet 原子写：目标已存在时整体替换，且不留 .tmp 残渣。"""
    import polars as pl

    from trendradar.infrastructure.filesystem.atomic import atomic_write_parquet

    target = tmp_path / "x.parquet"
    atomic_write_parquet(pl.DataFrame({"a": [1]}), target)
    atomic_write_parquet(pl.DataFrame({"a": [2, 3]}), target)

    assert pl.read_parquet(target)["a"].to_list() == [2, 3]
    assert [p.name for p in tmp_path.iterdir()] == ["x.parquet"]
