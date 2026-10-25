from decimal import Decimal

from ledger.report import render_markdown
from ledger.sizing import Evaluation, OptionResult, ScaleResult, SizingOption
from tests.test_sizing import factor, plan_of, sizing_line


def sample() -> tuple[object, Evaluation]:
    runs = factor("30", name="runs", basis="requirement", source="要件 §5.1")
    seconds = factor("25.6", name="seconds", basis="measured", source="REPORT 2026-10-24")
    glue = SizingOption(id="glue", label="Glue", lines=[sizing_line("dpu", runs)])
    sfn = SizingOption(id="sfn-v2", label="SFN v2", lines=[sizing_line("gbs", runs, seconds)])
    plan = plan_of(glue, sfn, baseline="glue", adopted="sfn-v2")
    evaluation = Evaluation(
        scales={
            1: ScaleResult(
                options={
                    "glue": OptionResult(
                        Decimal("0.924"), [], Decimal(0), {"dpu": Decimal("0.924")}
                    ),
                    "sfn-v2": OptionResult(
                        Decimal("0.0169"), [], Decimal("0.9071"), {"gbs": Decimal("0.0169")}
                    ),
                },
                avoided=Decimal("0.9071"),
            )
        }
    )
    return plan, evaluation


def test_render_markdown_shows_each_factor_with_basis_and_source() -> None:
    # Arrange
    plan, evaluation = sample()

    # Act
    markdown = render_markdown(plan, evaluation)  # type: ignore[arg-type]

    # Assert
    assert "| SFN v2 | gbs の行 | seconds | 25.6 | 実測 | REPORT 2026-10-24 |" in markdown


def test_render_markdown_marks_lines_with_assumed_values() -> None:
    # Arrange
    plan, evaluation = sample()
    guess = factor("40", name="seconds", basis="assumed", source="1GB を読む時間の仮定")
    plan.options[1].lines[0].factors[1] = guess  # type: ignore[attr-defined]

    # Act
    markdown = render_markdown(plan, evaluation)  # type: ignore[arg-type]

    # Assert
    assert "| SFN v2 | gbs の行 | seconds | 40 | **仮定** | 1GB を読む時間の仮定 |" in markdown


def test_render_markdown_shows_totals_and_avoided_per_scale() -> None:
    # Arrange
    plan, evaluation = sample()

    # Act
    markdown = render_markdown(plan, evaluation)  # type: ignore[arg-type]

    # Assert
    assert "### 処理量 ×1" in markdown
    assert "| SFN v2（採用） | 0.02 | 0.91 | 作れる |" in markdown
    assert "| Glue（基準） | 0.92 | 0.00 | 作れる |" in markdown
    assert "回避額（Glue − SFN v2）: 月 0.91 USD" in markdown


def test_render_markdown_shows_usage_type_amount_and_price_of_each_line() -> None:
    # Arrange
    plan, evaluation = sample()

    # Act
    markdown = render_markdown(plan, evaluation)  # type: ignore[arg-type]

    # Assert
    assert "| SFN v2 | gbs の行 | Lambda-GB-Second-ARM | 768.0 | 0.0169 |" in markdown
