"""base と head のテンプレートを見積もり、PR コメントの確定値の欄を作る。"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from ledger.diff import LineChange, compare_lines, display_places, format_usd, pr_delta
from ledger.estimator import Estimate
from ledger.safe import safe_label, safe_logical_id, safe_resource_type
from ledger.transform import Finding, UsageRow, transform
from ledger.usage import Factor, Usage

EstimateFn = Callable[[list[UsageRow]], Estimate]


@dataclass(frozen=True)
class Comparison:
    """base と head の見積もりの比較。"""

    base_total: Decimal
    head_total: Decimal
    delta: Decimal
    changes: list[LineChange]
    # 行の key ごとの説明。head にある行は head の説明を使う
    labels: dict[str, str]
    # head で見積もりに入れなかったもの（excluded）と、注記（note）
    findings: list[Finding]
    # base では値段が付いたのに head では対象外になったもの（偽の節約に見えるので警告する）
    dropped: list[Finding] = field(default_factory=list)


def compare_templates(
    base: dict[str, Any],
    head: dict[str, Any],
    region: str,
    estimate: EstimateFn,
    *,
    base_usage: Usage | None = None,
    head_usage: Usage | None = None,
) -> Comparison:
    """2つのテンプレートを1つの見積もりにまとめて送り、月額と差を出す。

    使用量は側ごとのファイルを使う（head の前提で base を見積もらない。spec §14.1）。
    """
    sides = {
        "base": transform(base, region, base_usage),
        "head": transform(head, region, head_usage),
    }
    rows = [
        replace(row, key=f"{side}/{row.key}")
        for side, result in sides.items()
        for row in result.rows
    ]
    labels = {row.key: row.label for side in ("base", "head") for row in sides[side].rows}
    prices = estimate(rows).lines
    base_lines = _side(prices, "base")
    head_lines = _side(prices, "head")
    base_total = sum(base_lines.values(), Decimal(0))
    head_total = sum(head_lines.values(), Decimal(0))
    return Comparison(
        base_total=base_total,
        head_total=head_total,
        delta=pr_delta(base_total, head_total),
        changes=compare_lines(base_lines, head_lines),
        labels=labels,
        findings=sides["head"].findings,
        dropped=_dropped(sides["base"].rows, sides["head"].findings),
    )


def _dropped(base_rows: list[UsageRow], head_findings: list[Finding]) -> list[Finding]:
    """base で行を出したリソースのうち、head で excluded になったもの（指摘3）。"""
    # 行の key は「論理 ID」「論理 ID/dev/…」「論理 ID.compute」などなので、論理 ID に戻す
    priced = {row.key.split("/")[0].split(".")[0] for row in base_rows}
    return [f for f in head_findings if f.kind == "excluded" and f.logical_id in priced]


def _side(prices: dict[str, Decimal], side: str) -> dict[str, Decimal]:
    """`base/` か `head/` で始まる行を、接頭辞を外した key で取り出す（順序は保つ）。"""
    prefix = f"{side}/"
    return {k.removeprefix(prefix): v for k, v in prices.items() if k.startswith(prefix)}


def render_comment(comparison: Comparison) -> str:
    """PR コメントの確定値の欄（英語の Markdown）を作る。"""
    places = display_places(comparison.base_total, comparison.head_total)
    lines = [
        "### Cost estimate (AWS Price List API, on-demand list prices)",
        "",
        "| | Monthly |",
        "|---|---|",
        f"| Before (base) | {format_usd(comparison.base_total, places)} USD |",
        f"| After (head) | {format_usd(comparison.head_total, places)} USD |",
    ]
    if comparison.dropped:
        names = ", ".join(
            f"{safe_logical_id(f.logical_id)} ({safe_resource_type(f.resource_type)})"
            for f in comparison.dropped
        )
        warning = (
            f"> **Warning:** priced in base but not included in the estimate for head: {names}."
            " The delta below does not include it."
        )
        lines = [*lines[:2], warning, "", *lines[2:]]
    # compare_lines は額が変わらない行も "changed" で返すので、表からは外す
    shown = [c for c in comparison.changes if not (c.status == "changed" and c.delta == 0)]
    if comparison.delta == 0 and not shown:
        total = format_usd(comparison.head_total, places)
        lines += ["", f"This PR does not change the monthly cost ({total} USD)."]
    else:
        lines += [
            "",
            f"This PR changes the monthly cost by **{_signed(comparison.delta, places)} USD**.",
            "",
            "| Line | Change | Before | After | Delta |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {safe_label(comparison.labels[c.key])} | {c.status} | {_money(c.base, places)}"
            f" | {_money(c.head, places)} | {_signed(c.delta, places)} |"
            for c in shown
        ]
    if comparison.findings:
        # 理由は transform が日本語で持っているので、英語のコメントには出さない
        lines += ["", "Not included in the estimate (head):", "", "| Resource | Type | Kind |"]
        lines.append("|---|---|---|")
        for f in comparison.findings:
            resource, kind = safe_logical_id(f.logical_id), safe_resource_type(f.resource_type)
            lines.append(f"| {resource} | {kind} | {f.kind} |")
    return "\n".join(lines) + "\n"


def _signed(amount: Decimal, places: Decimal) -> str:
    text = format_usd(amount, places)
    return f"+{text}" if amount > 0 else text


def _money(amount: Decimal | None, places: Decimal) -> str:
    return "-" if amount is None else format_usd(amount, places)


def render_usage_changes(base_usage: Usage, head_usage: Usage) -> str:
    """usage.json の値が base と head で違う項目の表（spec §14.4）。同じなら空。

    使用量の前提は PR の作者が変えられるので、変わったことをレビューする人に見せる。
    source（自由記述）は出さない。
    """
    rows = []
    for logical_id in [*base_usage, *(k for k in head_usage if k not in base_usage)]:
        before, after = base_usage.get(logical_id, {}), head_usage.get(logical_id, {})
        for name in [*before, *(k for k in after if k not in before)]:
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            basis = (new or old).basis  # type: ignore[union-attr]
            rows.append(
                f"| {safe_logical_id(logical_id)} | {name} | {_factor_text(old)}"
                f" | {_factor_text(new)} | {basis} |"
            )
    if not rows:
        return ""
    header = [
        "Usage assumptions that changed (usage.json):",
        "",
        "| Resource | Item | Before | After | Basis |",
        "|---|---|---|---|---|",
    ]
    return "\n".join([*header, *rows]) + "\n"


def _factor_text(factor: Factor | None) -> str:
    if factor is None:
        return "-"
    if factor.values is not None:
        return ", ".join(f"{memory}MB: {value}" for memory, value in sorted(factor.values.items()))
    return str(factor.value)
