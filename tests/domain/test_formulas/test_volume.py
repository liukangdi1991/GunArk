import polars as pl
from trendradar.domain.strategy.formulas.volume import volume_spike_flag, volume_step_down, recent_low


def test_volume_spike_flag():
    close = pl.Series("close", [100, 101, 102, 103, 104])
    volume = pl.Series("volume", [100, 100, 250, 100, 100])
    result = volume_spike_flag(close, volume, multiple=2.0)
    assert len(result) == 5
    assert result.name == "volume_spike_flag"
    # Day 0: null (no prev), Day 2: 250 > 100*2 = True
    assert result[2] is True
    assert result[3] is False


def test_volume_spike_flag_custom_multiple():
    close = pl.Series("close", [100, 101, 102, 103, 104])
    volume = pl.Series("volume", [100, 100, 150, 100, 100])
    result = volume_spike_flag(close, volume, multiple=1.5)
    # Day 2: 150 == 100*1.5, not strictly greater
    assert result[2] is False


def test_recent_low():
    column = pl.Series("values", [5, 4, 3, 2, 1, 2, 1])
    result = recent_low(column, window=3)
    assert len(result) == 7
    assert result.name == "recent_low"
    # window=3 rolling min: [null,null,3,2,1,1,1], idx 2: 3==3 True, idx 5: 2!=1 False
    assert result[2] is True
    assert result[5] is False
    assert result[6] is True


def test_volume_step_down():
    volume = pl.Series("volume", [100, 90, 80, 70, 60])
    result = volume_step_down(volume, window=3)
    assert len(result) == 5
    assert result.name == "volume_step_down"


def test_volume_step_down_flat():
    volume = pl.Series("volume", [100, 100, 100, 100, 100])
    result = volume_step_down(volume, window=3)
    # All diffs are 0, not < 0, so rolling sum should be 0
    valid = result.drop_nulls()
    for v in valid:
        assert v == 0
