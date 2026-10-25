import io
import json

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from pr_agent.remote import RemoteError, build_payload, invoke_review, session_id

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/review-abc"


def test_session_id_is_at_least_33_characters_and_unique_per_mode() -> None:
    none, tool = (
        session_id("3", "40171064963", "1", "none"),
        session_id("3", "40171064963", "1", "tool"),
    )

    assert len(none) >= 33 and len(tool) >= 33
    assert none != tool


def test_invoke_review_sends_payload_and_returns_body() -> None:
    # Arrange
    client = boto3.client(
        "bedrock-agentcore",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    stubber = Stubber(client)
    data = json.dumps({"body": "### Requirements review"}).encode()
    stubber.add_response(
        "invoke_agent_runtime",
        {"response": StreamingBody(io.BytesIO(data), len(data)), "contentType": "application/json"},
        {
            "agentRuntimeArn": ARN,
            "runtimeSessionId": "pr-3-run-1-1-tool-xxxxxxxxxxxxxxxxxxxxxxx",
            "contentType": "application/json",
            "accept": "application/json",
            "payload": ANY,
        },
    )
    stubber.activate()

    # Act
    body = invoke_review(client, ARN, "pr-3-run-1-1-tool-xxxxxxxxxxxxxxxxxxxxxxx", {"mode": "tool"})

    # Assert
    assert body == "### Requirements review"
    stubber.assert_no_pending_responses()


def test_build_payload_truncates_long_pull_request_text() -> None:
    payload = build_payload(
        {"Resources": {}}, {"Resources": {}}, None, "t" * 400, "b" * 30_000, "tool", "us-east-1"
    )

    assert len(payload["pr_title"]) == 300
    assert len(payload["pr_body"]) == 20_000
    assert payload["pr_body"].endswith("(truncated)")


def test_invoke_review_reports_only_known_error_kinds() -> None:
    client = boto3.client(
        "bedrock-agentcore",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    stubber = Stubber(client)
    data = json.dumps({"error": "arn:aws:iam::123456789012:role/Secret denied"}).encode()
    stubber.add_response(
        "invoke_agent_runtime",
        {"response": StreamingBody(io.BytesIO(data), len(data)), "contentType": "application/json"},
    )
    stubber.activate()

    with pytest.raises(RemoteError) as raised:
        invoke_review(client, ARN, "s" * 40, {"mode": "tool"})

    assert str(raised.value) == "unknown_error"
