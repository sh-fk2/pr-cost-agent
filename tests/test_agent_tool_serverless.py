from decimal import Decimal
from typing import Any

from ledger.agent_tool import CostTool, requirement_checks
from ledger.limits import Limit
from ledger.usage import parse_usage
from tests.test_compare_serverless import LAMBDA, PRICES, REGION, PerUnitEstimator

USAGE = parse_usage(
    {
        "resources": {
            "Aggregate": {
                "invocations_per_month": {"value": "30", "basis": "requirement"},
                "duration_ms_by_memory": {
                    "values": {"1024": "33600", "1536": "25600"},
                    "basis": "measured",
                },
                "max_memory_used_mb_by_memory": {
                    "values": {"1024": "1012", "1536": "1187"},
                    "basis": "measured",
                },
            }
        }
    }
)


def _tool() -> CostTool:
    head = {"Resources": {"Aggregate": LAMBDA}}
    return CostTool(head, REGION, PerUnitEstimator(PRICES), usage=USAGE)


def _memory(value: Any) -> dict[str, Any]:
    return {"logical_id": "Aggregate", "action": "set", "property": "MemorySize", "value": value}


def test_estimate_cost_set_lambda_memory_uses_the_measured_duration() -> None:
    result = _tool().estimate_cost([_memory(1024)])

    # head: 1536MB で 30 回 × 25.6 秒 = 1152 GB 秒 → 0.0154（4桁、spec §14.3）
    # 1024MB: 30 回 × 33.6 秒 × 1 GB = 1008 GB 秒 → 0.013440 + 0.000006 = 0.0134
    assert (result["head_total"], result["changed_total"], result["delta"]) == (
        "0.0154",
        "0.0134",
        "-0.0019",
    )


def test_estimate_cost_memory_without_measurement_returns_error_with_measured_sizes() -> None:
    result = _tool().estimate_cost([_memory(2048)])

    assert result == {
        "error": "No measured duration for MemorySize 2048. Measured sizes: [1024, 1536]."
    }


def test_estimate_cost_reports_measured_peak_memory_and_headroom() -> None:
    tool = _tool()

    current = tool.estimate_cost([])
    smaller = tool.estimate_cost([_memory(1024)])

    # 余裕 = (メモリ − 実測の最大) ÷ メモリ。整数の % に四捨五入する
    assert current["memory"] == [
        {"logical_id": "Aggregate", "memory_mb": 1536, "peak_mb": "1187", "headroom_percent": 23}
    ]
    assert smaller["memory"] == [
        {"logical_id": "Aggregate", "memory_mb": 1024, "peak_mb": "1012", "headroom_percent": 1}
    ]


def test_estimate_cost_memory_headroom_skips_memory_that_is_not_a_value() -> None:
    head = {
        "Resources": {
            "Aggregate": {
                "Type": "AWS::Lambda::Function",
                "Properties": {"Architectures": ["arm64"], "MemorySize": {"Ref": "Mem"}},
            }
        }
    }
    tool = CostTool(head, REGION, PerUnitEstimator(PRICES), usage=USAGE)

    result = tool.estimate_cost([])

    assert result["memory"] == []


LIMITS = {
    "max_monthly_usd": Limit(Decimal("1.00"), "R-05"),
    "min_free_memory_percent": Limit(Decimal("10"), "R-04"),
}


def test_requirement_checks_compare_cost_and_memory_with_the_limits_file_in_code() -> None:
    result = {
        "changed_total": "0.0215",
        "not_included": [{"logical_id": "Bucket"}, {"logical_id": "Logs"}],
        "memory": [{"logical_id": "Aggregate", "memory_mb": 1536, "peak_mb": "1523"}],
        "unmeasured_lambdas": [],
    }

    checks = requirement_checks(result, LIMITS)

    assert checks == {
        "cost": {"ok": True, "limit": "1.00", "requirement": "R-05", "not_priced": 2},
        "memory": {"ok": False, "limit": "10", "requirement": "R-04"},
    }


def test_requirement_checks_memory_is_unknown_when_a_lambda_has_no_measurement() -> None:
    result = {
        "changed_total": "0.02",
        "not_included": [],
        "memory": [{"logical_id": "Aggregate", "memory_mb": 2048, "peak_mb": "1564"}],
        "unmeasured_lambdas": ["Other"],
    }

    assert requirement_checks(result, LIMITS)["memory"]["ok"] is None


def test_requirement_checks_compares_headroom_before_rounding() -> None:
    # 9.5% 空き（丸めると 10%）は「10% 以上」を満たさない
    result = {
        "changed_total": "0.02",
        "not_included": [],
        "memory": [{"logical_id": "Aggregate", "memory_mb": 1000, "peak_mb": "905"}],
        "unmeasured_lambdas": [],
    }

    assert requirement_checks(result, LIMITS)["memory"]["ok"] is False


def test_requirement_checks_without_limits_returns_no_checks() -> None:
    assert requirement_checks({"changed_total": "0.02", "memory": []}, {}) == {}
