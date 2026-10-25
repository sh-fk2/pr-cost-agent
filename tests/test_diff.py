from decimal import Decimal

from ledger.diff import (
    LineChange,
    avoided_cost,
    compare_lines,
    display_places,
    format_usd,
    pr_delta,
    to_money,
)


def test_avoided_cost_rejected_more_expensive_returns_positive_difference() -> None:
    # Arrange
    rejected = Decimal("150.00")
    adopted = Decimal("100.00")

    # Act
    result = avoided_cost(rejected, adopted)

    # Assert
    assert result == Decimal("50.00")


def test_avoided_cost_rejected_cheaper_returns_negative() -> None:
    # Arrange
    rejected = Decimal("80.00")
    adopted = Decimal("100.00")

    # Act
    result = avoided_cost(rejected, adopted)

    # Assert
    assert result == Decimal("-20.00")


def test_pr_delta_head_cheaper_returns_negative_reduction() -> None:
    # Arrange
    base = Decimal("142.02")
    head = Decimal("120.00")

    # Act
    result = pr_delta(base, head)

    # Assert
    assert result == Decimal("-22.02")


def test_pr_delta_head_more_expensive_returns_positive_increase() -> None:
    # Arrange
    base = Decimal("100.00")
    head = Decimal("130.50")

    # Act
    result = pr_delta(base, head)

    # Assert
    assert result == Decimal("30.50")


def test_compare_lines_same_key_returns_changed_with_delta() -> None:
    # Arrange
    base = {"WebServer": Decimal("90.52")}
    head = {"WebServer": Decimal("76.94")}

    # Act
    result = compare_lines(base, head)

    # Assert
    assert result == [
        LineChange(
            key="WebServer",
            status="changed",
            base=Decimal("90.52"),
            head=Decimal("76.94"),
            delta=Decimal("-13.58"),
        )
    ]


def test_compare_lines_one_sided_keys_return_added_and_removed() -> None:
    # Arrange
    base = {"WebServer": Decimal("90.52"), "OldNat": Decimal("45.26")}
    head = {"WebServer": Decimal("90.52"), "Endpoint": Decimal("20.44")}

    # Act
    result = compare_lines(base, head)

    # Assert
    assert result == [
        LineChange("WebServer", "changed", Decimal("90.52"), Decimal("90.52"), Decimal("0.00")),
        LineChange("OldNat", "removed", Decimal("45.26"), None, Decimal("-45.26")),
        LineChange("Endpoint", "added", None, Decimal("20.44"), Decimal("20.44")),
    ]


def test_pr_delta_float_amounts_from_api_have_no_binary_error() -> None:
    # Arrange
    base = to_money(0.1)
    head = to_money(0.3)

    # Act
    result = pr_delta(base, head)

    # Assert
    assert result == Decimal("0.2")


def test_format_usd_rounds_to_two_decimal_places() -> None:
    # Arrange
    amount = Decimal("142.022")

    # Act
    result = format_usd(amount)

    # Assert
    assert result == "142.02"


def test_format_usd_exact_half_rounds_up() -> None:
    # Arrange
    amount = Decimal("0.125")

    # Act
    result = format_usd(amount)

    # Assert
    assert result == "0.13"


def test_compare_lines_both_empty_returns_no_changes() -> None:
    # Arrange
    base: dict[str, Decimal] = {}
    head: dict[str, Decimal] = {}

    # Act
    result = compare_lines(base, head)

    # Assert
    assert result == []


def test_display_places_small_totals_use_four_places() -> None:
    places = display_places(Decimal("0.924"), Decimal("0.0169"))

    assert format_usd(Decimal("0.0169"), places) == "0.0169"
    assert format_usd(Decimal("-0.90714"), places) == "-0.9071"


def test_display_places_total_of_ten_or_more_uses_two_places() -> None:
    places = display_places(Decimal("134.68"), Decimal("0.5"))

    assert format_usd(Decimal("0.0169"), places) == "0.02"
