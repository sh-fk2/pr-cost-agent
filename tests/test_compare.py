from decimal import Decimal
from typing import Any

from ledger.compare import compare_templates, render_comment
from ledger.estimator import Estimate
from ledger.transform import UsageRow

REGION = "ap-northeast-1"


def instance(instance_type: str) -> dict[str, Any]:
    return {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": instance_type}}


def template(**resources: dict[str, Any]) -> dict[str, Any]:
    return {"Resources": resources}


class FakeEstimator:
    """usageType ごとに決めた月額を返す。呼ばれた行を残す。"""

    def __init__(self, prices: dict[str, str]) -> None:
        self.prices = {k: Decimal(v) for k, v in prices.items()}
        self.calls: list[list[UsageRow]] = []

    def __call__(self, rows: list[UsageRow]) -> Estimate:
        self.calls.append(rows)
        lines = {row.key: self.prices[row.usage_type] for row in rows}
        return Estimate(lines=lines, total=sum(lines.values(), Decimal(0)))


def test_compare_templates_returns_base_head_and_delta() -> None:
    # Arrange
    base = template(Web=instance("m7g.large"))
    head = template(Web=instance("m7g.xlarge"))
    estimator = FakeEstimator({"APN1-BoxUsage:m7g.large": "100", "APN1-BoxUsage:m7g.xlarge": "130"})

    # Act
    result = compare_templates(base, head, REGION, estimator)

    # Assert
    assert (result.base_total, result.head_total, result.delta) == (
        Decimal("100"),
        Decimal("130"),
        Decimal("30"),
    )


def test_compare_templates_sends_base_and_head_in_one_estimate_with_prefixed_keys() -> None:
    # Arrange
    base = template(Web=instance("m7g.large"))
    head = template(Web=instance("m7g.xlarge"))
    estimator = FakeEstimator({"APN1-BoxUsage:m7g.large": "100", "APN1-BoxUsage:m7g.xlarge": "130"})

    # Act
    compare_templates(base, head, REGION, estimator)

    # Assert
    assert len(estimator.calls) == 1
    assert [(row.key, row.usage_type) for row in estimator.calls[0]] == [
        ("base/Web", "APN1-BoxUsage:m7g.large"),
        ("head/Web", "APN1-BoxUsage:m7g.xlarge"),
    ]


def nat() -> dict[str, Any]:
    return {"Type": "AWS::EC2::NatGateway", "Properties": {}}


def test_compare_templates_reports_changed_added_and_removed_lines() -> None:
    # Arrange
    base = template(Web=instance("m7g.large"), Old=instance("t4g.small"))
    head = template(Web=instance("m7g.xlarge"), NatA=nat())
    estimator = FakeEstimator(
        {
            "APN1-BoxUsage:m7g.large": "100",
            "APN1-BoxUsage:t4g.small": "15",
            "APN1-BoxUsage:m7g.xlarge": "130",
            "APN1-NatGateway-Hours": "45",
        }
    )

    # Act
    result = compare_templates(base, head, REGION, estimator)

    # Assert
    assert [(c.key, c.status, c.delta) for c in result.changes] == [
        ("Web", "changed", Decimal("30")),
        ("Old", "removed", Decimal("-15")),
        ("NatA", "added", Decimal("45")),
    ]


def test_render_comment_shows_totals_signed_delta_and_line_table() -> None:
    # Arrange
    base = template(Web=instance("m7g.large"), Old=instance("t4g.small"))
    head = template(Web=instance("m7g.xlarge"), NatA=nat())
    estimator = FakeEstimator(
        {
            "APN1-BoxUsage:m7g.large": "100",
            "APN1-BoxUsage:t4g.small": "15",
            "APN1-BoxUsage:m7g.xlarge": "130",
            "APN1-NatGateway-Hours": "45",
        }
    )
    result = compare_templates(base, head, REGION, estimator)

    # Act
    markdown = render_comment(result)

    # Assert
    assert "| Before (base) | 115.00 USD |" in markdown
    assert "| After (head) | 175.00 USD |" in markdown
    assert "This PR changes the monthly cost by **+60.00 USD**." in markdown
    assert "| Web: EC2 m7g.xlarge | changed | 100.00 | 130.00 | +30.00 |" in markdown
    assert "| Old: EC2 t4g.small | removed | 15.00 | - | -15.00 |" in markdown
    assert "| NatA: NAT Gateway | added | - | 45.00 | +45.00 |" in markdown


def test_render_comment_lists_head_findings() -> None:
    # Arrange
    base = template(Web=instance("m7g.large"))
    head = template(Web=instance("m7g.large"), Queue={"Type": "AWS::SQS::Queue"})
    estimator = FakeEstimator({"APN1-BoxUsage:m7g.large": "100"})
    result = compare_templates(base, head, REGION, estimator)

    # Act
    markdown = render_comment(result)

    # Assert
    assert "| Queue | AWS::SQS::Queue | excluded |" in markdown


def test_render_comment_says_no_change_in_one_line() -> None:
    # Arrange
    same = template(Web=instance("m7g.large"))
    estimator = FakeEstimator({"APN1-BoxUsage:m7g.large": "100"})
    result = compare_templates(same, same, REGION, estimator)

    # Act
    markdown = render_comment(result)

    # Assert
    assert "This PR does not change the monthly cost (100.00 USD)." in markdown
    assert "| Line |" not in markdown


def test_render_comment_sanitizes_template_derived_values() -> None:
    # Arrange
    evil = "X | removed | 0 |\n[Click to approve](https://evil.example) @maintainer"
    base = template(Web=instance("m7g.large"))
    head = template(Web=instance("m7g.large"), **{evil: {"Type": "AWS::SQS::Queue"}})
    estimator = FakeEstimator({"APN1-BoxUsage:m7g.large": "100"})
    result = compare_templates(base, head, REGION, estimator)

    # Act
    markdown = render_comment(result)

    # Assert
    assert "evil.example" not in markdown
    assert "@maintainer" not in markdown
    assert "[Click" not in markdown
    assert "| [redacted] | AWS::SQS::Queue | excluded |" in markdown
