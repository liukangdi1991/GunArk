import polars as pl
from trendradar.domain.strategy.formulas.volume import volume_spike_flag, volume_step_down


def test_volume_spike_flag():
    volume = pl.Series("volume", [100, 100, 250, 100, 100])
    result = volume_spike_flag(volume, multiple=2.0)
    assert len(result) == 5
    assert result.name == "volume_spike_flag"
    # Day 0: null (no prev), Day 2: 250 > 100*2 = True
    assert result[2] is True
    assert result[3] is False


def test_volume_spike_flag_custom_multiple():
    volume = pl.Series("volume", [100, 100, 150, 100, 100])
    result = volume_spike_flag(volume, multiple=1.5)
    # Day 2: 150 == 100*1.5, not strictly greater
    assert result[2] is False


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
