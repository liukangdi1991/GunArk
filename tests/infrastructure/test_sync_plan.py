"""decide_mode: full vs incremental decision (pure, no I/O)."""
from trendradar.infrastructure.tushare.syncer import decide_mode


def test_retry_codes_force_full():
    assert decide_mode(missing_days=1, force=False, retry_codes=["000001"]) == "full"


def test_force_always_full():
    assert decide_mode(missing_days=0, force=True, retry_codes=[]) == "full"


def test_large_gap_is_full():
    assert decide_mode(missing_days=21, force=False, retry_codes=[]) == "full"


def test_small_gap_is_incremental():
    assert decide_mode(missing_days=20, force=False, retry_codes=[]) == "incremental"


def test_zero_gap_is_incremental():
    assert decide_mode(missing_days=0, force=False, retry_codes=[]) == "incremental"
