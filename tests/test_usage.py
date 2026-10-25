from decimal import Decimal
from pathlib import Path

import pytest

from ledger.usage import MAX_USAGE_BYTES, Factor, UsageError, load_usage, parse_usage


def test_parse_usage_glue_factors_reads_values_and_basis() -> None:
    data = {
        "resources": {
            "AggregateWeather": {
                "runs_per_month": {"value": "30", "basis": "requirement", "source": "§5.1"},
                "seconds_per_run": {"value": "126", "basis": "measured", "source": "jr_0001"},
            }
        }
    }

    usage = parse_usage(data)

    assert usage == {
        "AggregateWeather": {
            "runs_per_month": Factor(value=Decimal("30"), values=None, basis="requirement"),
            "seconds_per_run": Factor(value=Decimal("126"), values=None, basis="measured"),
        }
    }


def test_parse_usage_values_by_memory_reads_each_memory_size() -> None:
    data = {
        "resources": {
            "Aggregate": {
                "duration_ms_by_memory": {
                    "values": {"1024": "33600", "1536": "25571"},
                    "basis": "measured",
                }
            }
        }
    }

    usage = parse_usage(data)

    assert usage["Aggregate"]["duration_ms_by_memory"] == Factor(
        value=None, values={1024: Decimal("33600"), 1536: Decimal("25571")}, basis="measured"
    )


def _one(name: str, raw: object, logical_id: str = "Job") -> dict[str, object]:
    return {"resources": {logical_id: {name: raw}}}


def test_parse_usage_unknown_item_raises_usage_error() -> None:
    with pytest.raises(UsageError, match="cpu_per_run"):
        parse_usage(_one("cpu_per_run", {"value": "1", "basis": "assumed"}))


def test_parse_usage_json_number_value_raises_usage_error() -> None:
    with pytest.raises(UsageError, match="string"):
        parse_usage(_one("runs_per_month", {"value": 30, "basis": "requirement"}))


@pytest.mark.parametrize("text", ["1000001", "-1", "abc", "NaN"])
def test_parse_usage_value_out_of_range_or_not_decimal_raises_usage_error(text: str) -> None:
    with pytest.raises(UsageError, match="runs_per_month"):
        parse_usage(_one("runs_per_month", {"value": text, "basis": "requirement"}))


@pytest.mark.parametrize("memory", ["127", "10241", "1.5", "big"])
def test_parse_usage_memory_key_out_of_range_raises_usage_error(memory: str) -> None:
    raw = {"values": {memory: "1000"}, "basis": "measured"}

    with pytest.raises(UsageError, match="memory"):
        parse_usage(_one("duration_ms_by_memory", raw))


def test_parse_usage_unknown_basis_raises_usage_error() -> None:
    with pytest.raises(UsageError, match="basis"):
        parse_usage(_one("runs_per_month", {"value": "30", "basis": "guess"}))


@pytest.mark.parametrize("logical_id", ["Job Name", "https://x", ""])
def test_parse_usage_unsafe_logical_id_raises_usage_error(logical_id: str) -> None:
    with pytest.raises(UsageError, match="logical id"):
        parse_usage(_one("runs_per_month", {"value": "30", "basis": "requirement"}, logical_id))


def test_load_usage_file_over_limit_raises_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text(" " * (MAX_USAGE_BYTES + 1), encoding="utf-8")

    with pytest.raises(UsageError, match="too large"):
        load_usage(path)


@pytest.mark.parametrize(
    "data",
    [[], {"resources": []}, {"resources": {"Job": []}}, _one("runs_per_month", "30")],
)
def test_parse_usage_wrong_shape_raises_usage_error(data: object) -> None:
    with pytest.raises(UsageError, match="shape"):
        parse_usage(data)


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("runs_per_month", {"values": {"1024": "30"}, "basis": "requirement"}),
        ("duration_ms_by_memory", {"value": "25600", "basis": "measured"}),
    ],
)
def test_parse_usage_value_and_values_must_match_the_item(name: str, raw: object) -> None:
    with pytest.raises(UsageError, match=name):
        parse_usage(_one(name, raw))


def test_parse_usage_non_ascii_digit_memory_key_raises_usage_error() -> None:
    raw = {"values": {"²": "1000"}, "basis": "measured"}

    with pytest.raises(UsageError, match="memory"):
        parse_usage(_one("duration_ms_by_memory", raw))


def test_load_usage_not_utf8_raises_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_bytes('{"resources": {"ジョブ": {}}}'.encode("shift_jis"))

    with pytest.raises(UsageError, match="UTF-8"):
        load_usage(path)


@pytest.mark.parametrize(
    "text", [" 5 ", "1_0", "５", "1E+3", "0E+999999999", "1.1234567", "1234567890123"]
)
def test_parse_usage_value_outside_the_plain_decimal_format_raises_usage_error(text: str) -> None:
    with pytest.raises(UsageError, match="seconds_per_run"):
        parse_usage(_one("seconds_per_run", {"value": text, "basis": "measured"}))


def test_parse_usage_more_than_sixteen_memory_sizes_raises_usage_error() -> None:
    values = {str(128 + i): "1000" for i in range(17)}

    with pytest.raises(UsageError, match="16"):
        parse_usage(_one("duration_ms_by_memory", {"values": values, "basis": "measured"}))


def test_load_usage_deeply_nested_json_raises_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text("[" * 50_000 + "]" * 50_000, encoding="utf-8")

    with pytest.raises(UsageError):
        load_usage(path)
