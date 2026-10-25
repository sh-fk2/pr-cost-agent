import json
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from ledger.cli import main
from tests.test_plan_file import valid_plan


@pytest.fixture
def options_file(tmp_path: Path) -> Path:
    path = tmp_path / "options.json"
    path.write_text(json.dumps(valid_plan()), encoding="utf-8")
    return path


def test_main_size_estimate_failure_is_written_and_exits_nonzero(
    options_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    client = boto3.client(
        "pricing",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    stubber = Stubber(client)
    stubber.add_client_error("get_products", "AccessDeniedException")
    stubber.activate()

    # Act
    code = main(["size", str(options_file)], client=client)

    # Assert
    assert code == 1
    assert "見積もりに失敗しました" in capsys.readouterr().out
    stubber.assert_no_pending_responses()


def test_main_size_invalid_options_exits_with_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    path = tmp_path / "options.json"
    path.write_text(json.dumps(valid_plan(baseline="ec2")), encoding="utf-8")

    # Act
    code = main(["size", str(path)], client=object())

    # Assert
    assert code == 2
    assert "baseline" in capsys.readouterr().err


def test_main_compare_estimate_failure_is_written_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    template = {
        "Resources": {
            "Web": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "m7g.large"}}
        }
    }
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    for path in (base, head):
        path.write_text(json.dumps(template), encoding="utf-8")
    client = boto3.client(
        "pricing",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    stubber = Stubber(client)
    stubber.add_client_error("get_products", "AccessDeniedException")
    stubber.activate()

    # Act
    args = ["compare", str(base), str(head), "--region", "ap-northeast-1"]
    code = main(args, client=client)

    # Assert
    assert code == 1
    assert "The cost estimate failed" in capsys.readouterr().out
    stubber.assert_no_pending_responses()


def test_main_compare_failure_does_not_put_error_details_in_the_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    template = {
        "Resources": {
            "Web": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "m7g.large"}}
        }
    }
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    for path in (base, head):
        path.write_text(json.dumps(template), encoding="utf-8")
    client = boto3.client(
        "pricing",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    stubber = Stubber(client)
    message = "User: arn:aws:sts::123456789012:assumed-role/Secret/x is not authorized"
    stubber.add_client_error("get_products", "AccessDeniedException", message)
    stubber.activate()

    # Act
    args = ["compare", str(base), str(head)]
    code = main(args, client=client)

    # Assert
    captured = capsys.readouterr()
    assert code == 1
    assert "123456789012" not in captured.out
    assert "Secret" not in captured.out
    assert "Secret" in captured.err


def test_main_compare_rejects_templates_over_the_size_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    from ledger.cli import MAX_TEMPLATE_BYTES

    big = tmp_path / "big.json"
    big.write_text('{"Resources": {}, "Pad": "' + "a" * MAX_TEMPLATE_BYTES + '"}', encoding="utf-8")

    # Act
    code = main(["compare", str(big), str(big)], client=object())

    # Assert
    assert code == 2
    assert "too large" in capsys.readouterr().err


def test_main_compare_invalid_usage_file_exits_with_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    template = tmp_path / "t.json"
    template.write_text(json.dumps({"Resources": {}}), encoding="utf-8")
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({"resources": {"Job": {"cpu": {}}}}), encoding="utf-8")

    # Act
    args = ["compare", str(template), str(template), "--region", "us-east-1"]
    code = main([*args, "--head-usage", str(usage)], client=object())

    # Assert
    assert code == 2
    assert "usage" in capsys.readouterr().err


def test_main_compare_unexpected_template_shape_reports_failure_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange: Resources が object ではない（PR の作者が書けるテンプレート）
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    base.write_text(json.dumps({"Resources": {}}), encoding="utf-8")
    head.write_text(json.dumps({"Resources": ["not", "an", "object"]}), encoding="utf-8")

    # Act
    code = main(["compare", str(base), str(head), "--region", "us-east-1"], client=object())

    # Assert
    captured = capsys.readouterr()
    assert code == 1
    assert "The cost estimate failed" in captured.out
