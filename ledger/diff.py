"""見積もった月額どうしの差を計算する。"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

DISPLAY_PLACES = Decimal("0.01")
# 合計が小さい比較（サーバーレスの月額など）は4桁で出す。2桁だと案どうしの差が消える（spec §14.3）
SMALL_TOTAL = Decimal(10)
SMALL_PLACES = Decimal("0.0001")

LineStatus = Literal["changed", "added", "removed"]


@dataclass(frozen=True)
class LineChange:
    """1行ぶんの変化。追加なら base が、削除なら head が None。"""

    key: str
    status: LineStatus
    base: Decimal | None
    head: Decimal | None
    delta: Decimal


def to_money(value: float | str) -> Decimal:
    """API が返した金額を Decimal にする。float の2進誤差を持ち込まない。"""
    return Decimal(str(value))


def display_places(*totals: Decimal) -> Decimal:
    """比較の合計がどれも 10 USD 未満なら小数4桁、そうでなければ2桁。1つの比較の中ではそろえる。"""
    return SMALL_PLACES if all(abs(t) < SMALL_TOTAL for t in totals) else DISPLAY_PLACES


def format_usd(amount: Decimal, places: Decimal = DISPLAY_PLACES) -> str:
    """表示用に四捨五入する。桁は display_places で決める（既定は小数2桁）。"""
    return str(amount.quantize(places, rounding=ROUND_HALF_UP))


def avoided_cost(rejected: Decimal, adopted: Decimal) -> Decimal:
    """回避額（採らなかった案の月額 − 採用案の月額）。正なら避けた額。"""
    return rejected - adopted


def pr_delta(base: Decimal, head: Decimal) -> Decimal:
    """PR の差（head の月額 − base の月額）。負なら下げた額。"""
    return head - base


def compare_lines(base: Mapping[str, Decimal], head: Mapping[str, Decimal]) -> list[LineChange]:
    """key で行を突き合わせ、行ごとの変化を出す。"""
    changes = [
        LineChange(key, "changed", cost, head[key], head[key] - cost)
        if key in head
        else LineChange(key, "removed", cost, None, -cost)
        for key, cost in base.items()
    ]
    added = [
        LineChange(key, "added", None, cost, cost) for key, cost in head.items() if key not in base
    ]
    return [*changes, *added]
