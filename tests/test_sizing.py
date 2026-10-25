from collections.abc import Sequence
from decimal import Decimal

from ledger.estimator import Estimate
from ledger.sizing import (
    Factor,
    Plan,
    SizingLine,
    SizingOption,
    evaluate,
    exceeded_limits,
    line_amount,
)
from ledger.transform import UsageRow


def factor(value: str, **kwargs: object) -> Factor:
    defaults: dict[str, object] = {"name": "x", "basis": "requirement", "source": "§5.1"}
    return Factor(value=Decimal(value), **{**defaults, **kwargs})  # type: ignore[arg-type]


def test_line_amount_single_factor_returns_its_value() -> None:
    # Arrange
    factors = [factor("30")]

    # Act
    amount = line_amount(factors, scale=1)

    # Assert
    assert amount == Decimal("30")


def test_line_amount_multiple_factors_returns_their_product() -> None:
    # Arrange
    factors = [factor("30"), factor("25.6"), factor("1.5")]

    # Act
    amount = line_amount(factors, scale=1)

    # Assert
    assert amount == Decimal("1152")


def test_line_amount_divisor_divides_the_value() -> None:
    # Arrange
    factors = [factor("252", divisor=Decimal("3600")), factor("30")]

    # Act
    amount = line_amount(factors, scale=1)

    # Assert
    assert amount == Decimal("2.1")


def test_line_amount_decimal_values_have_no_float_error() -> None:
    # Arrange
    factors = [factor("0.1"), factor("0.2"), factor("3")]

    # Act
    amount = line_amount(factors, scale=1)

    # Assert
    assert amount == Decimal("0.06")


def test_line_amount_scale_multiplies_only_scaling_factors() -> None:
    # Arrange
    factors = [factor("30"), factor("25.6", scales=True)]

    # Act
    amount = line_amount(factors, scale=10)

    # Assert
    assert amount == Decimal("7680")


def test_exceeded_limits_scaled_value_over_limit_is_reported() -> None:
    # Arrange
    seconds = factor("25.6", name="seconds", scales=True, limit=Decimal("900"))
    factors = [factor("30"), seconds]

    # Act
    exceeded = exceeded_limits(factors, scale=100)

    # Assert
    assert exceeded == ["seconds"]


def test_exceeded_limits_value_equal_to_limit_is_feasible() -> None:
    # Arrange
    factors = [factor("9", name="seconds", scales=True, limit=Decimal("900"))]

    # Act
    exceeded = exceeded_limits(factors, scale=100)

    # Assert
    assert exceeded == []


def sizing_line(key: str, *factors: Factor) -> SizingLine:
    return SizingLine(
        key=key,
        service_code="AWSLambda",
        usage_type="Lambda-GB-Second-ARM",
        operation="",
        label=f"{key} の行",
        factors=list(factors) or [factor("1")],
    )


def plan_of(
    *options: SizingOption, baseline: str, adopted: str, scales: Sequence[int] = (1,)
) -> Plan:
    return Plan(
        requirement="docs/requirements/x.md",
        scales=list(scales),
        baseline=baseline,
        adopted=adopted,
        options=list(options),
    )


class FakeEstimator:
    """行の key ごとに決めた額を返す。呼ばれた行を残す。"""

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self.prices = prices
        self.calls: list[list[UsageRow]] = []

    def __call__(self, rows: list[UsageRow]) -> Estimate:
        self.calls.append(rows)
        lines = {row.key: self.prices[row.key] for row in rows}
        return Estimate(lines=lines, total=sum(lines.values(), Decimal(0)))


def test_evaluate_option_total_is_sum_of_its_line_prices() -> None:
    # Arrange
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu"), sizing_line("logs")])
    plan = plan_of(glue, baseline="glue", adopted="glue")
    estimator = FakeEstimator({"x1/glue/dpu": Decimal("0.924"), "x1/glue/logs": Decimal("0.01")})

    # Act
    result = evaluate(plan, estimator)

    # Assert
    assert result.scales[1].options["glue"].total == Decimal("0.934")


def two_options(**kwargs: object) -> tuple[Plan, FakeEstimator]:
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu")])
    sfn = SizingOption(id="sfn-v2", label="SFN v2", lines=[sizing_line("gbs")])
    plan = plan_of(glue, sfn, baseline="glue", adopted="sfn-v2", **kwargs)  # type: ignore[arg-type]
    prices = {"x1/glue/dpu": Decimal("0.924"), "x1/sfn-v2/gbs": Decimal("0.0169")}
    return plan, FakeEstimator(prices)


def test_evaluate_avoided_is_baseline_minus_adopted() -> None:
    # Arrange
    plan, estimator = two_options()

    # Act
    result = evaluate(plan, estimator)

    # Assert
    assert result.scales[1].avoided == Decimal("0.9071")


def test_evaluate_non_adopted_option_also_has_diff_from_baseline() -> None:
    # Arrange
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu")])
    v1 = SizingOption(id="sfn-v1", label="SFN v1", lines=[sizing_line("gbs")])
    v2 = SizingOption(id="sfn-v2", label="SFN v2", lines=[sizing_line("gbs")])
    plan = plan_of(glue, v1, v2, baseline="glue", adopted="sfn-v2")
    estimator = FakeEstimator(
        {
            "x1/glue/dpu": Decimal("0.924"),
            "x1/sfn-v1/gbs": Decimal("0.0664"),
            "x1/sfn-v2/gbs": Decimal("0.0169"),
        }
    )

    # Act
    result = evaluate(plan, estimator)

    # Assert
    assert result.scales[1].options["sfn-v1"].diff_from_baseline == Decimal("0.8576")


def test_evaluate_infeasible_option_is_excluded_from_avoided() -> None:
    # Arrange
    seconds = factor("25.6", name="seconds", scales=True, limit=Decimal("900"))
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu")])
    sfn = SizingOption(id="sfn-v2", label="SFN v2", lines=[sizing_line("gbs", seconds)])
    plan = plan_of(glue, sfn, baseline="glue", adopted="sfn-v2", scales=[1, 100])
    estimator = FakeEstimator(
        {
            "x1/glue/dpu": Decimal("0.924"),
            "x1/sfn-v2/gbs": Decimal("0.0169"),
            "x100/glue/dpu": Decimal("0.924"),
            "x100/sfn-v2/gbs": Decimal("1.69"),
        }
    )

    # Act
    result = evaluate(plan, estimator)

    # Assert
    at_100 = result.scales[100]
    assert at_100.options["sfn-v2"].exceeded == ["seconds"]
    assert at_100.options["sfn-v2"].diff_from_baseline is None
    assert at_100.avoided is None
    assert result.scales[1].avoided == Decimal("0.9071")


def test_evaluate_sends_all_scales_and_options_in_one_estimate() -> None:
    # Arrange
    glue = SizingOption(
        id="glue", label="Glue", lines=[sizing_line("dpu", factor("2.1", scales=True))]
    )
    sfn = SizingOption(id="sfn-v2", label="SFN v2", lines=[sizing_line("gbs", factor("1152"))])
    plan = plan_of(glue, sfn, baseline="glue", adopted="sfn-v2", scales=[1, 10])
    estimator = FakeEstimator(
        {
            "x1/glue/dpu": Decimal("0.924"),
            "x1/sfn-v2/gbs": Decimal("0.0169"),
            "x10/glue/dpu": Decimal("9.24"),
            "x10/sfn-v2/gbs": Decimal("0.0169"),
        }
    )

    # Act
    evaluate(plan, estimator)

    # Assert
    assert len(estimator.calls) == 1
    sent = {(row.key, row.usage_type, row.amount) for row in estimator.calls[0]}
    assert sent == {
        ("x1/glue/dpu", "Lambda-GB-Second-ARM", 2.1),
        ("x1/sfn-v2/gbs", "Lambda-GB-Second-ARM", 1152.0),
        ("x10/glue/dpu", "Lambda-GB-Second-ARM", 21.0),
        ("x10/sfn-v2/gbs", "Lambda-GB-Second-ARM", 1152.0),
    }


def test_evaluate_shared_factor_over_limit_is_reported_once() -> None:
    # Arrange
    seconds = factor("25.6", name="seconds", scales=True, limit=Decimal("900"))
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu")])
    sfn = SizingOption(
        id="sfn", label="SFN", lines=[sizing_line("gbs", seconds), sizing_line("tmp", seconds)]
    )
    plan = plan_of(glue, sfn, baseline="glue", adopted="sfn", scales=[1, 100])
    prices = {
        f"x{s}/{k}": Decimal("1") for s in (1, 100) for k in ("glue/dpu", "sfn/gbs", "sfn/tmp")
    }

    # Act
    result = evaluate(plan, FakeEstimator(prices))

    # Assert
    assert result.scales[100].options["sfn"].exceeded == ["seconds"]


def test_evaluate_keeps_price_of_each_line() -> None:
    # Arrange
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu"), sizing_line("logs")])
    plan = plan_of(glue, baseline="glue", adopted="glue")
    estimator = FakeEstimator({"x1/glue/dpu": Decimal("0.924"), "x1/glue/logs": Decimal("0.01")})

    # Act
    result = evaluate(plan, estimator)

    # Assert
    assert result.scales[1].options["glue"].line_prices == {
        "dpu": Decimal("0.924"),
        "logs": Decimal("0.01"),
    }
