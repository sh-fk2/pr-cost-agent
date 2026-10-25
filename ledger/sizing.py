"""設計段階の試算: 案ごとの使用量の元から、使用量・月額・回避額を出す。"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from ledger.diff import avoided_cost
from ledger.estimator import Estimate
from ledger.transform import UsageRow

Basis = Literal["requirement", "measured", "assumed"]


@dataclass(frozen=True)
class Factor:
    """使用量の元になる値。行の量はこれらの積。"""

    name: str
    value: Decimal
    basis: Basis
    source: str
    divisor: Decimal | None = None
    scales: bool = False
    limit: Decimal | None = None


def scaled_value(factor: Factor, scale: int) -> Decimal:
    """倍率を掛けた値（scales が true のときだけ掛ける）。divisor で割る前の値。"""
    return factor.value * scale if factor.scales else factor.value


def line_amount(factors: Sequence[Factor], scale: int) -> Decimal:
    """値の積から行の量を出す。"""
    amount = Decimal(1)
    for f in factors:
        value = scaled_value(f, scale)
        amount *= value if f.divisor is None else value / f.divisor
    return amount


def exceeded_limits(factors: Sequence[Factor], scale: int) -> list[str]:
    """倍率を掛けた値が limit を超えた値の名前。空なら作れる。"""
    return [f.name for f in factors if f.limit is not None and scaled_value(f, scale) > f.limit]


@dataclass(frozen=True)
class SizingLine:
    """使用量の行。量は factors の積。"""

    key: str
    service_code: str
    usage_type: str
    operation: str
    label: str
    factors: list[Factor]


@dataclass(frozen=True)
class SizingOption:
    """構成の案。"""

    id: str
    label: str
    lines: list[SizingLine]


@dataclass(frozen=True)
class Plan:
    """skill が書いた試算の入力（options.json）。"""

    requirement: str
    scales: list[int]
    baseline: str
    adopted: str
    options: list[SizingOption]


@dataclass(frozen=True)
class OptionResult:
    """ある倍率での、ある案の結果。"""

    total: Decimal
    exceeded: list[str]
    # baseline との差。この案か baseline が作れないなら None
    diff_from_baseline: Decimal | None
    # 行の key ごとの見積もり額。出力から行の量と単価をたどるために残す
    line_prices: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class ScaleResult:
    """ある倍率での結果。"""

    options: dict[str, OptionResult]
    avoided: Decimal | None


@dataclass(frozen=True)
class Evaluation:
    """試算の結果。"""

    scales: dict[int, ScaleResult]


EstimateFn = Callable[[list[UsageRow]], Estimate]


def evaluate(plan: Plan, estimate: EstimateFn) -> Evaluation:
    """全倍率・全案の行を見積もり、案の月額と回避額を出す。"""
    rows = [
        UsageRow(
            key=_row_key(scale, option.id, line.key),
            service_code=line.service_code,
            usage_type=line.usage_type,
            operation=line.operation,
            amount=float(line_amount(line.factors, scale)),
            label=line.label,
        )
        for scale in plan.scales
        for option in plan.options
        for line in option.lines
    ]
    prices = estimate(rows).lines
    return Evaluation(scales={scale: _scale_result(plan, prices, scale) for scale in plan.scales})


def _scale_result(plan: Plan, prices: dict[str, Decimal], scale: int) -> ScaleResult:
    line_prices = {
        option.id: {line.key: prices[_row_key(scale, option.id, line.key)] for line in option.lines}
        for option in plan.options
    }
    totals = {
        option_id: sum(lines.values(), Decimal(0)) for option_id, lines in line_prices.items()
    }
    exceeded = {
        # 同じ値を複数の行で使うことがあるので、名前の重複を除く（順序は保つ）
        option.id: list(
            dict.fromkeys(
                name for line in option.lines for name in exceeded_limits(line.factors, scale)
            )
        )
        for option in plan.options
    }
    baseline_feasible = not exceeded[plan.baseline]
    options = {
        option_id: OptionResult(
            total=total,
            exceeded=exceeded[option_id],
            diff_from_baseline=(
                avoided_cost(totals[plan.baseline], total)
                if baseline_feasible and not exceeded[option_id]
                else None
            ),
            line_prices=line_prices[option_id],
        )
        for option_id, total in totals.items()
    }
    return ScaleResult(options=options, avoided=options[plan.adopted].diff_from_baseline)


def _row_key(scale: int, option_id: str, line_key: str) -> str:
    return f"x{scale}/{option_id}/{line_key}"
