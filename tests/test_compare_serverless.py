from decimal import Decimal
from typing import Any

from ledger.compare import compare_templates, render_comment, render_usage_changes
from ledger.estimator import Estimate
from ledger.transform import UsageRow
from ledger.usage import Usage, parse_usage

REGION = "us-east-1"

GLUE = {
    "Type": "AWS::Glue::Job",
    "Properties": {
        "Command": {"Name": "glueetl"},
        "GlueVersion": "5.0",
        "WorkerType": "G.1X",
        "NumberOfWorkers": 2,
    },
}
LAMBDA = {
    "Type": "AWS::Lambda::Function",
    "Properties": {"Architectures": ["arm64"], "MemorySize": 1536},
}
BASE_USAGE: Usage = parse_usage(
    {
        "resources": {
            "AggregateWeather": {
                "runs_per_month": {"value": "30", "basis": "requirement"},
                "seconds_per_run": {"value": "126", "basis": "measured"},
            }
        }
    }
)
HEAD_USAGE: Usage = parse_usage(
    {
        "resources": {
            "Aggregate": {
                "invocations_per_month": {"value": "30", "basis": "requirement"},
                "duration_ms_by_memory": {"values": {"1536": "25600"}, "basis": "measured"},
            }
        }
    }
)


class PerUnitEstimator:
    """usageType ごとの単価 × 量を返す。"""

    def __init__(self, unit_prices: dict[str, str]) -> None:
        self.unit_prices = {k: Decimal(v) for k, v in unit_prices.items()}

    def __call__(self, rows: list[UsageRow]) -> Estimate:
        lines = {r.key: self.unit_prices[r.usage_type] * Decimal(str(r.amount)) for r in rows}
        return Estimate(lines=lines, total=sum(lines.values(), Decimal(0)))


PRICES = {
    "USE1-ETL-DPU-Hour": "0.44",
    "Lambda-GB-Second-ARM": "0.0000133334",
    "Request-ARM": "0.0000002",
}


def _templates() -> tuple[dict[str, Any], dict[str, Any]]:
    return {"Resources": {"AggregateWeather": GLUE}}, {"Resources": {"Aggregate": LAMBDA}}


def test_compare_templates_uses_base_usage_for_base_and_head_usage_for_head() -> None:
    base, head = _templates()

    result = compare_templates(
        base, head, REGION, PerUnitEstimator(PRICES), base_usage=BASE_USAGE, head_usage=HEAD_USAGE
    )

    # base: 2.1 DPU 時間 × 0.44 = 0.924。head: 1152 GB 秒 × 単価 + 30 回 × 単価
    assert result.base_total == Decimal("0.924")
    assert result.head_total == Decimal("1152.0") * Decimal("0.0000133334") + Decimal(
        "30.0"
    ) * Decimal("0.0000002")
    assert [c.key for c in result.changes] == [
        "AggregateWeather",
        "Aggregate.compute",
        "Aggregate.requests",
    ]


def test_render_comment_small_totals_use_four_decimal_places() -> None:
    base, head = _templates()
    comparison = compare_templates(
        base, head, REGION, PerUnitEstimator(PRICES), base_usage=BASE_USAGE, head_usage=HEAD_USAGE
    )

    comment = render_comment(comparison)

    # head: 1152 × 0.0000133334 + 30 × 0.0000002 = 0.015366... → 0.0154
    assert "| Before (base) | 0.9240 USD |" in comment
    assert "| After (head) | 0.0154 USD |" in comment
    assert "**-0.9086 USD**" in comment
    assert "| AggregateWeather: Glue G.1X x 2 | removed | 0.9240 | - | -0.9240 |" in comment


def test_render_usage_changes_lists_items_that_differ_between_base_and_head() -> None:
    head_usage = parse_usage(
        {
            "resources": {
                "AggregateWeather": {
                    "runs_per_month": {"value": "30", "basis": "requirement"},
                    "seconds_per_run": {"value": "90", "basis": "assumed"},
                },
                "Aggregate": {
                    "duration_ms_by_memory": {
                        "values": {"1024": "33600", "1536": "25600"},
                        "basis": "measured",
                    }
                },
            }
        }
    )

    text = render_usage_changes(BASE_USAGE, head_usage)

    assert text.splitlines() == [
        "Usage assumptions that changed (usage.json):",
        "",
        "| Resource | Item | Before | After | Basis |",
        "|---|---|---|---|---|",
        "| AggregateWeather | seconds_per_run | 126 | 90 | assumed |",
        "| Aggregate | duration_ms_by_memory | - | 1024MB: 33600, 1536MB: 25600 | measured |",
    ]


def test_render_usage_changes_same_usage_returns_empty() -> None:
    assert render_usage_changes(BASE_USAGE, BASE_USAGE) == ""


def test_render_comment_warns_when_a_priced_resource_drops_out_of_the_estimate() -> None:
    # head で WorkerType を対象外の値にすると、Glue の行が消えて「節約」に見える
    base = {"Resources": {"AggregateWeather": GLUE}}
    unsupported = {**GLUE, "Properties": {**GLUE["Properties"], "WorkerType": "G.4X"}}
    head = {"Resources": {"AggregateWeather": unsupported}}

    comparison = compare_templates(
        base, head, REGION, PerUnitEstimator(PRICES), base_usage=BASE_USAGE, head_usage=BASE_USAGE
    )
    comment = render_comment(comparison)

    warning = (
        "> **Warning:** priced in base but not included in the estimate for head: "
        "AggregateWeather (AWS::Glue::Job). The delta below does not include it."
    )
    assert comment.splitlines()[2] == warning
