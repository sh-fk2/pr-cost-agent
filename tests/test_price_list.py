import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from ledger.estimator import EstimateError
from ledger.price_list import estimate
from ledger.transform import UsageRow

FIXTURES = Path(__file__).parent / "fixtures" / "price_list"


def _client() -> Any:
    return boto3.client(
        "pricing",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _products(*names: str) -> dict[str, Any]:
    """実物の応答を削った fixture を、GetProducts の応答の形（JSON 文字列の配列）にする。"""
    price_list = [json.dumps(json.loads((FIXTURES / f"{n}.json").read_text())) for n in names]
    return {"FormatVersion": "aws_v1", "PriceList": price_list}


def _filters(region: str, usage_type: str, operation: str = "") -> list[dict[str, str]]:
    filters = [
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        {"Type": "TERM_MATCH", "Field": "usagetype", "Value": usage_type},
    ]
    if operation:
        filters.append({"Type": "TERM_MATCH", "Field": "operation", "Value": operation})
    return filters


GLUE_ROW = UsageRow("Job", "AWSGlue", "USE1-ETL-DPU-Hour", "Jobrun", 2.1, "Job: Glue")


def test_estimate_glue_row_is_unit_price_times_amount() -> None:
    # Arrange
    client = _client()
    stubber = Stubber(client)
    stubber.add_response(
        "get_products",
        _products("glue_etl_dpu_hour"),
        {"ServiceCode": "AWSGlue", "Filters": _filters("us-east-1", "USE1-ETL-DPU-Hour", "Jobrun")},
    )
    stubber.activate()

    # Act
    result = estimate(client, [GLUE_ROW], "us-east-1")

    # Assert: 0.44 USD × 2.1 DPU 時間
    assert result.lines == {"Job": Decimal("0.924")}
    assert result.total == Decimal("0.924")
    stubber.assert_no_pending_responses()


def _stubbed(*responses: tuple[str, dict[str, Any]]) -> tuple[Any, Stubber]:
    client = _client()
    stubber = Stubber(client)
    for fixture, params in responses:
        stubber.add_response("get_products", _products(fixture), params)
    stubber.activate()
    return client, stubber


def test_estimate_ec2_matches_the_pricing_calculator_value() -> None:
    # Pricing Calculator では m7g.large の 730 時間は 76.94 USD だった（PR #2）
    params = {
        "ServiceCode": "AmazonEC2",
        "Filters": _filters("ap-northeast-1", "APN1-BoxUsage:m7g.large", "RunInstances"),
    }
    client, _ = _stubbed(("ec2_m7g_large", params))
    row = UsageRow("Web", "AmazonEC2", "APN1-BoxUsage:m7g.large", "RunInstances", 730, "Web")

    result = estimate(client, [row], "ap-northeast-1")

    assert result.lines["Web"] == Decimal("76.942")


def test_estimate_tiered_price_uses_the_first_tier_without_operation_filter() -> None:
    params = {"ServiceCode": "AWSLambda", "Filters": _filters("us-east-1", "Lambda-GB-Second-ARM")}
    client, stubber = _stubbed(("lambda_gb_second_arm", params))
    row = UsageRow("Fn.compute", "AWSLambda", "Lambda-GB-Second-ARM", "", 1152.0, "Fn")

    result = estimate(client, [row], "us-east-1")

    # 最初の区間（0〜75億 GB 秒）の 0.0000133334 USD
    assert result.lines["Fn.compute"] == Decimal("1152.0") * Decimal("0.0000133334")
    stubber.assert_no_pending_responses()


def test_estimate_amount_over_the_first_tier_raises_estimate_error() -> None:
    params = {"ServiceCode": "AWSLambda", "Filters": _filters("us-east-1", "Lambda-GB-Second-ARM")}
    client, _ = _stubbed(("lambda_gb_second_arm", params))
    row = UsageRow("Fn.compute", "AWSLambda", "Lambda-GB-Second-ARM", "", 7_500_000_001, "Fn")

    with pytest.raises(EstimateError, match="first price tier"):
        estimate(client, [row], "us-east-1")


def test_estimate_same_usage_type_calls_get_products_once() -> None:
    params = {
        "ServiceCode": "AWSGlue",
        "Filters": _filters("us-east-1", "USE1-ETL-DPU-Hour", "Jobrun"),
    }
    client, stubber = _stubbed(("glue_etl_dpu_hour", params))
    other = UsageRow("base/Job", "AWSGlue", "USE1-ETL-DPU-Hour", "Jobrun", 1.0, "Job")

    # 応答は1つしか積んでいないので、2回目に API を呼ぶと Stubber が例外を出す
    result = estimate(client, [GLUE_ROW, other], "us-east-1")

    assert result.lines == {"Job": Decimal("0.924"), "base/Job": Decimal("0.44")}
    assert result.total == Decimal("1.364")
    stubber.assert_no_pending_responses()


GLUE_PARAMS = {
    "ServiceCode": "AWSGlue",
    "Filters": _filters("us-east-1", "USE1-ETL-DPU-Hour", "Jobrun"),
}


def _glue_with(product: dict[str, Any] | None, count: int = 1) -> Any:
    client = _client()
    stubber = Stubber(client)
    price_list = [] if product is None else [json.dumps(product)] * count
    stubber.add_response(
        "get_products", {"FormatVersion": "aws_v1", "PriceList": price_list}, GLUE_PARAMS
    )
    stubber.activate()
    return client


def _glue_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "glue_etl_dpu_hour.json").read_text())


@pytest.mark.parametrize(("product", "count"), [(None, 0), (_glue_fixture(), 2)])
def test_estimate_not_exactly_one_product_raises_estimate_error(
    product: dict[str, Any] | None, count: int
) -> None:
    client = _glue_with(product, count)

    with pytest.raises(EstimateError, match="exactly one product"):
        estimate(client, [GLUE_ROW], "us-east-1")


def _without_usd() -> dict[str, Any]:
    product = _glue_fixture()
    for term in product["terms"]["OnDemand"].values():
        for dimension in term["priceDimensions"].values():
            dimension["pricePerUnit"] = {"CNY": "3.0"}
    return product


@pytest.mark.parametrize(
    "product",
    [
        _without_usd(),
        {**_glue_fixture(), "terms": {}},
        {**_glue_fixture(), "terms": {"OnDemand": {}}},
    ],
)
def test_estimate_without_usd_on_demand_price_raises_estimate_error(
    product: dict[str, Any],
) -> None:
    client = _glue_with(product)

    with pytest.raises(EstimateError, match="on-demand USD price"):
        estimate(client, [GLUE_ROW], "us-east-1")


def test_estimate_shared_cache_avoids_calling_get_products_again() -> None:
    client, stubber = _stubbed(("glue_etl_dpu_hour", GLUE_PARAMS))
    cache: dict[Any, Any] = {}

    first = estimate(client, [GLUE_ROW], "us-east-1", cache=cache)
    second = estimate(client, [GLUE_ROW], "us-east-1", cache=cache)

    assert first.total == second.total == Decimal("0.924")
    stubber.assert_no_pending_responses()
