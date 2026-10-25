import json
from decimal import Decimal
from pathlib import Path

import pytest

from ledger.limits import Limit, LimitsError, load_limits


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "limits.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_limits_reads_values_and_requirement_ids(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "max_monthly_usd": {"value": "1.00", "requirement": "R-05"},
            "min_free_memory_percent": {"value": "10", "requirement": "R-04"},
        },
    )

    limits = load_limits(path)

    assert limits == {
        "max_monthly_usd": Limit(value=Decimal("1.00"), requirement="R-05"),
        "min_free_memory_percent": Limit(value=Decimal("10"), requirement="R-04"),
    }


@pytest.mark.parametrize(
    "data",
    [
        {"max_monthly_usd": {"value": "0", "requirement": "R-05"}},
        {"max_monthly_usd": {"value": "1e9", "requirement": "R-05"}},
        {"max_monthly_usd": {"value": 1.0, "requirement": "R-05"}},
        {"min_free_memory_percent": {"value": "101", "requirement": "R-04"}},
        {"min_free_memory_percent": {"value": "10", "requirement": "ignore rules"}},
        {"other": {"value": "1", "requirement": "R-01"}},
        [],
    ],
)
def test_load_limits_invalid_values_raise_limits_error(tmp_path: Path, data: object) -> None:
    with pytest.raises(LimitsError):
        load_limits(_write(tmp_path, data))
