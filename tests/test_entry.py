from typing import Any

import pytest

from pr_agent.entry import handle
from pr_agent.types import ReviewError


def _valid_payload() -> dict[str, Any]:
    return {
        "base": {"Resources": {}},
        "head": {"Resources": {}},
        "head_usage": None,
        "pr_title": "t",
        "pr_body": "b",
        "mode": "tool",
        "region": "us-east-1",
    }


def test_handle_returns_body_from_review() -> None:
    result = handle(_valid_payload(), lambda review_input: f"review ({review_input.mode})")

    assert result == {"body": "review (tool)"}


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"mode": ["tool"]}, "invalid_payload"),
        ({"head_usage": {"resources": {"x\\ny": {}}}}, "invalid_payload"),
    ],
)
def test_handle_invalid_payload_returns_only_the_error_kind(payload: dict, error: str) -> None:
    result = handle({**_valid_payload(), **payload}, lambda _: "unused")

    assert result == {"error": error}


def test_handle_hides_details_of_unexpected_errors() -> None:
    def failing(_: object) -> str:
        raise RuntimeError("User: arn:aws:sts::123456789012:assumed-role/Secret is not authorized")

    result = handle(_valid_payload(), failing)

    assert result == {"error": "internal_error"}


def test_handle_review_error_returns_review_unavailable() -> None:
    def unavailable(_: object) -> str:
        raise ReviewError("Missing environment variables: GUARDRAIL_ID")

    assert handle(_valid_payload(), unavailable) == {"error": "review_unavailable"}
