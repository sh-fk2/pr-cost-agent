"""試算の結果を、ADR に貼る Markdown にする。"""

from ledger.diff import format_usd
from ledger.sizing import Evaluation, Plan, ScaleResult, line_amount

# 仮定は確かめていない値なので、表で目立たせる
BASIS_LABELS = {"requirement": "要件", "measured": "実測", "assumed": "**仮定**"}


def render_markdown(plan: Plan, evaluation: Evaluation) -> str:
    """案ごとの月額・回避額・使用量の元の表を作る。"""
    sections = [_scale_section(plan, scale, result) for scale, result in evaluation.scales.items()]
    body = [line for section in sections for line in section]
    tables = ["### 行ごとの量と額（×1）", "", *_line_table(plan, evaluation.scales[1]), ""]
    return "\n".join([*body, *tables, "### 使用量の元", "", *_factor_table(plan)]) + "\n"


def _line_table(plan: Plan, result: ScaleResult) -> list[str]:
    # 額は丸めずに出す。量と額から単価をたどれるようにするため
    lines = ["| 案 | 行 | usageType | 量 | 月額（USD） |", "|---|---|---|---|---|"]
    for option in plan.options:
        prices = result.options[option.id].line_prices
        for line in option.lines:
            lines.append(
                f"| {option.label} | {line.label} | {line.usage_type}"
                f" | {line_amount(line.factors, 1)} | {prices[line.key]} |"
            )
    return lines


def _scale_section(plan: Plan, scale: int, result: ScaleResult) -> list[str]:
    labels = {option.id: option.label for option in plan.options}
    lines = [
        f"### 処理量 ×{scale}",
        "",
        "| 案 | 月額（USD） | 基準との差（USD） | 作れるか |",
        "|---|---|---|---|",
    ]
    for option_id, option in result.options.items():
        role = {plan.baseline: "（基準）", plan.adopted: "（採用）"}.get(option_id, "")
        diff = "—" if option.diff_from_baseline is None else format_usd(option.diff_from_baseline)
        feasible = "作れる" if not option.exceeded else f"作れない（{', '.join(option.exceeded)}）"
        lines.append(
            f"| {labels[option_id]}{role} | {format_usd(option.total)} | {diff} | {feasible} |"
        )
    pair = f"{labels[plan.baseline]} − {labels[plan.adopted]}"
    avoided = (
        "比べられない（作れない案がある）"
        if result.avoided is None
        else (f"月 {format_usd(result.avoided)} USD")
    )
    return [*lines, "", f"回避額（{pair}）: {avoided}", ""]


def _factor_table(plan: Plan) -> list[str]:
    lines = [
        "| 案 | 行 | 値の名前 | 値 | 根拠 | 出典 |",
        "|---|---|---|---|---|---|",
    ]
    for option in plan.options:
        for line in option.lines:
            for f in line.factors:
                value = str(f.value) if f.divisor is None else f"{f.value} ÷ {f.divisor}"
                lines.append(
                    f"| {option.label} | {line.label} | {f.name} | {value}"
                    f" | {BASIS_LABELS[f.basis]} | {f.source} |"
                )
    return lines
