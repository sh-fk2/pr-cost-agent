"""見積もりの結果の型。月額は ledger.price_list が Price List API の単価から出す（spec §15）。

Pricing Calculator の workload estimate を使う版は、月50件の上限のため 2026-10-25 にやめた。
"""

from dataclasses import dataclass
from decimal import Decimal


class EstimateError(RuntimeError):
    """見積もりが確定しなかった（単価が決まらない、量が範囲を超えるなど）。"""


@dataclass(frozen=True)
class Estimate:
    """見積もりの結果。lines は rows の key ごとの月額。"""

    lines: dict[str, Decimal]
    total: Decimal
