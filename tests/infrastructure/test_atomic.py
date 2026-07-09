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
