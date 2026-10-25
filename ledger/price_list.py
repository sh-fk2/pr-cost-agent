"""使用量の行に、Price List API の定価を掛けて月額を出す（docs/spec.md §15）。

Pricing Calculator の workload estimate は月50件までしか作れないので、単価だけを API から取り、
掛け算と合計はここで行う。
"""

import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ledger.estimator import Estimate, EstimateError
from ledger.transform import UsageRow

PriceCache = dict[tuple[str, str, str], tuple[Decimal, Decimal | None]]


def estimate(
    client: Any, rows: Sequence[UsageRow], region: str, *, cache: PriceCache | None = None
) -> Estimate:
    """行ごとの月額と合計を返す。

    同じ実行の中では、同じ (サービス, usageType, operation) の単価を1回だけ取る（spec §15.1）。
    cache を渡すと、呼び出しをまたいで使う（ツールの今の構成と各案など）。
    """
    lines = {}
    prices: PriceCache = {} if cache is None else cache
    for row in rows:
        amount = Decimal(str(row.amount))
        key = (row.service_code, row.usage_type, row.operation)
        if key not in prices:
            prices[key] = _unit_price(client, row, region)
        price, tier_end = prices[key]
        if tier_end is not None and amount > tier_end:
            raise EstimateError(f"{row.key}: the amount is above the first price tier")
        lines[row.key] = price * amount
    return Estimate(lines=lines, total=sum(lines.values(), Decimal(0)))


def _unit_price(client: Any, row: UsageRow, region: str) -> tuple[Decimal, Decimal | None]:
    """最初の区間の単価と、その区間の上限（無ければ None）。"""
    filters = [
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        {"Type": "TERM_MATCH", "Field": "usagetype", "Value": row.usage_type},
    ]
    if row.operation:
        filters.append({"Type": "TERM_MATCH", "Field": "operation", "Value": row.operation})
    pages = client.get_paginator("get_products").paginate(
        ServiceCode=row.service_code, Filters=filters
    )
    products = [json.loads(item) for page in pages for item in page["PriceList"]]
    if len(products) != 1:
        raise EstimateError(
            f"{row.usage_type}: expected exactly one product in the price list, got {len(products)}"
        )
    [product] = products
    terms = list(product.get("terms", {}).get("OnDemand", {}).values())
    # 段階料金はアカウント全体の月の合計にかかるので、最小版では最初の区間だけを使う（spec §15.3）
    first = [
        d
        for term in terms[:1]
        for d in term.get("priceDimensions", {}).values()
        if Decimal(d.get("beginRange", "0")) == 0 and "USD" in d.get("pricePerUnit", {})
    ]
    if len(terms) != 1 or len(first) != 1:
        raise EstimateError(f"{row.usage_type}: no single on-demand USD price in the price list")
    [dimension] = first
    end = dimension.get("endRange", "Inf")
    return Decimal(dimension["pricePerUnit"]["USD"]), None if end == "Inf" else Decimal(end)
