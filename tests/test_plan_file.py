from decimal import Decimal
from typing import Any

import pytest

from ledger.plan_file import PlanError, parse_plan


def valid_plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "requirement": "docs/requirements/weather-daily.md",
        "scales": [1, 10],
        "baseline": "glue",
        "adopted": "sfn-v2",
        "options": [
            {
                "id": "glue",
                "label": "Glue 2 DPU",
                "lines": [
                    {
                        "key": "dpu",
                        "serviceCode": "AWSGlue",
                        "usageType": "USE1-ETL-DPU-Hour",
                        "operation": "Jobrun",
                        "label": "Glue の DPU 時間",
                        "factors": [
                            {
                                "name": "runs",
                                "value": "30",
                                "basis": "requirement",
                                "source": "§5.1",
                            },
                            {
                                "name": "dpu_seconds",
                                "value": "252",
                                "divisor": "3600",
                                "basis": "measured",
                                "source": "jr_ea4b",
                                "scales": True,
                            },
                        ],
                    }
                ],
            },
            {
                "id": "sfn-v2",
                "label": "Step Functions と Lambda",
                "lines": [
                    {
                        "key": "gbs",
                        "serviceCode": "AWSLambda",
                        "usageType": "Lambda-GB-Second-ARM",
                        "operation": "",
                        "label": "Lambda の実行時間",
                        "factors": [
                            {
                                "name": "seconds",
                                "value": "25.6",
                                "basis": "assumed",
                                "source": "1GB の CSV を読む時間の仮定",
                                "scales": True,
                                "limit": "900",
                            }
                        ],
                    }
                ],
            },
        ],
    }
    plan.update(overrides)
    return plan


def test_parse_plan_valid_input_becomes_plan_with_decimals() -> None:
    # Arrange
    data = valid_plan()

    # Act
    plan = parse_plan(data)

    # Assert
    assert plan.baseline == "glue"
    assert plan.scales == [1, 10]
    dpu = plan.options[0].lines[0].factors[1]
    assert (dpu.value, dpu.divisor, dpu.basis, dpu.scales) == (
        Decimal("252"),
        Decimal("3600"),
        "measured",
        True,
    )
    seconds = plan.options[1].lines[0].factors[0]
    assert (seconds.limit, seconds.scales) == (Decimal("900"), True)
    assert plan.options[1].lines[0].usage_type == "Lambda-GB-Second-ARM"


def test_parse_plan_unknown_baseline_raises() -> None:
    # Arrange
    data = valid_plan(baseline="ec2")

    # Act / Assert
    with pytest.raises(PlanError, match="baseline"):
        parse_plan(data)


def test_parse_plan_unknown_adopted_raises() -> None:
    # Arrange
    data = valid_plan(adopted="ec2")

    # Act / Assert
    with pytest.raises(PlanError, match="adopted"):
        parse_plan(data)


def test_parse_plan_duplicate_option_id_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][1]["id"] = "glue"
    data["adopted"] = "glue"

    # Act / Assert
    with pytest.raises(PlanError, match="重複"):
        parse_plan(data)


def test_parse_plan_scales_without_one_raises() -> None:
    # Arrange
    data = valid_plan(scales=[10, 100])

    # Act / Assert
    with pytest.raises(PlanError, match="scales"):
        parse_plan(data)


def test_parse_plan_line_without_factors_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"] = []

    # Act / Assert
    with pytest.raises(PlanError, match="factors"):
        parse_plan(data)


def test_parse_plan_non_numeric_value_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"][0]["value"] = "thirty"

    # Act / Assert
    with pytest.raises(PlanError, match="runs"):
        parse_plan(data)


def test_parse_plan_unknown_basis_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"][0]["basis"] = "guess"

    # Act / Assert
    with pytest.raises(PlanError, match="basis"):
        parse_plan(data)


def test_parse_plan_zero_divisor_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"][1]["divisor"] = "0"

    # Act / Assert
    with pytest.raises(PlanError, match="divisor"):
        parse_plan(data)


def test_parse_plan_missing_required_item_raises() -> None:
    # Arrange
    data = valid_plan()
    del data["options"][0]["lines"][0]["usageType"]

    # Act / Assert
    with pytest.raises(PlanError, match="usageType"):
        parse_plan(data)


def test_parse_plan_duplicate_line_key_in_option_raises() -> None:
    # Arrange
    data = valid_plan()
    lines = data["options"][0]["lines"]
    lines.append({**lines[0]})

    # Act / Assert
    with pytest.raises(PlanError, match="dpu"):
        parse_plan(data)


def test_parse_plan_duplicate_scales_raises() -> None:
    # Arrange
    data = valid_plan(scales=[1, 1, 10])

    # Act / Assert
    with pytest.raises(PlanError, match="scales"):
        parse_plan(data)


@pytest.mark.parametrize("bad", ["10", True, 0, 2.5, -1])
def test_parse_plan_scale_that_is_not_positive_integer_raises(bad: object) -> None:
    # Arrange
    data = valid_plan(scales=[1, bad])

    # Act / Assert
    with pytest.raises(PlanError, match="scales"):
        parse_plan(data)


@pytest.mark.parametrize("bad", ["glue/v2", "glue v2", ""])
def test_parse_plan_option_id_with_invalid_characters_raises(bad: str) -> None:
    # Arrange
    data = valid_plan(baseline=bad)
    data["options"][0]["id"] = bad

    # Act / Assert
    with pytest.raises(PlanError, match="id"):
        parse_plan(data)


def test_parse_plan_line_key_with_invalid_characters_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["key"] = "dpu/hours"

    # Act / Assert
    with pytest.raises(PlanError, match="key"):
        parse_plan(data)


def test_parse_plan_top_level_array_raises() -> None:
    # Act / Assert
    with pytest.raises(PlanError):
        parse_plan([valid_plan()])  # type: ignore[arg-type]


def test_parse_plan_factor_that_is_not_object_raises() -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"] = ["30"]

    # Act / Assert
    with pytest.raises(PlanError):
        parse_plan(data)


@pytest.mark.parametrize("field", ["value", "divisor", "limit"])
@pytest.mark.parametrize("bad", [0.1, 30, True])
def test_parse_plan_numeric_field_that_is_not_string_raises(field: str, bad: object) -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"][1][field] = bad

    # Act / Assert
    with pytest.raises(PlanError, match="文字列"):
        parse_plan(data)


@pytest.mark.parametrize("bad", ["false", "true", 1])
def test_parse_plan_factor_scales_that_is_not_boolean_raises(bad: object) -> None:
    # Arrange
    data = valid_plan()
    data["options"][0]["lines"][0]["factors"][0]["scales"] = bad

    # Act / Assert
    with pytest.raises(PlanError, match="scales"):
        parse_plan(data)
