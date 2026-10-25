import pytest

from ledger.cli import MAX_TEMPLATE_BYTES
from pr_agent.payload import PayloadError, parse_payload
from pr_agent.types import ReviewInput

BASE = {"Resources": {}}
HEAD = {"Resources": {"Fn": {"Type": "AWS::Lambda::Function", "Properties": {}}}}


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "base": BASE,
        "head": HEAD,
        "head_usage": None,
        "pr_title": "Replace the Glue job",
        "pr_body": "Moves the job.",
        "mode": "tool",
        "region": "us-east-1",
    }
    return {**payload, **overrides}


def test_parse_payload_builds_review_input_without_requirements() -> None:
    result = parse_payload(_payload())

    assert result == ReviewInput(
        base=BASE,
        head=HEAD,
        mode="tool",
        region="us-east-1",
        head_usage={},
        pr_title="Replace the Glue job",
        pr_body="Moves the job.",
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        _payload(mode="all"),
        _payload(region="eu-west-1"),
        _payload(extra=1),
        _payload(base="not a template"),
        _payload(head={"Pad": "a" * (MAX_TEMPLATE_BYTES + 1)}),
        _payload(pr_title="t" * 301),
        _payload(pr_body="b" * 20_001),
        _payload(pr_body=1),
        _payload(head_usage={"resources": {"Fn": {"cpu": {}}}}),
    ],
)
def test_parse_payload_rejects_invalid_payloads(payload: object) -> None:
    with pytest.raises(PayloadError):
        parse_payload(payload)


def test_parse_payload_validates_head_usage_like_usage_json() -> None:
    usage = {
        "resources": {"Fn": {"invocations_per_month": {"value": "30", "basis": "requirement"}}}
    }

    result = parse_payload(_payload(head_usage=usage))

    assert list(result.head_usage["Fn"]) == ["invocations_per_month"]


@pytest.mark.parametrize(
    "payload",
    [
        _payload(mode=["tool"]),
        _payload(region={"us-east-1": 1}),
        _payload(head={"Resources": []}),
        _payload(head={"Resources": {"Fn": {"Properties": {}}}}),
        _payload(head={"Resources": {"Fn": "x"}}),
    ],
)
def test_parse_payload_rejects_wrong_types_and_template_shapes(payload: object) -> None:
    with pytest.raises(PayloadError):
        parse_payload(payload)
