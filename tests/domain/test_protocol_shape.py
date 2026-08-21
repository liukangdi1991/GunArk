from dataclasses import FrozenInstanceError
import pytest
import polars as pl
from trendradar.domain.strategy.protocol import (
    SelectionContext, SelectionResult, WarmupResult, SelectionStrategy,
)


def test_warmup_result_is_frozen_dataclass():
    w = WarmupResult(grouped={"000001": pl.DataFrame()})
    with pytest.raises(FrozenInstanceError):
        w.grouped = {}
    assert set(WarmupResult.__dataclass_fields__) == {"grouped"}


def test_selection_context_has_no_get_data_dict():
    import dataclasses
    assert "get_data_dict" not in {f.name for f in dataclasses.fields(SelectionContext)}


def test_selection_result_fields_unchanged():
    import dataclasses
    assert {f.name for f in dataclasses.fields(SelectionResult)} == {
        "strategy_id", "strategy_name", "trade_date", "selected_codes", "elapsed_seconds",
    }


def test_protocol_has_warmup_and_select_day():
    assert callable(getattr(SelectionStrategy, "warmup", None))
    assert callable(getattr(SelectionStrategy, "select_day", None))
