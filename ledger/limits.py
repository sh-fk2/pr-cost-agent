"""要件の閾値のファイル（<要件>.limits.json）を読む（docs/spec.md §16.9）。

閾値をモデルに渡させると、PR の本文などで誘導されたモデルが緩い値を渡せてしまう。
要件と同じく main に置いたファイルから、コードが読む。
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Limit:
    """閾値の1つと、その出どころの要件 ID。"""

    value: Decimal
    requirement: str


class LimitsError(ValueError):
    """閾値のファイルの形や値が仕様に合わない。"""


# 受け付ける閾値と、値の範囲（下限は含まない / 含む、上限は含む）
RANGES = {
    "max_monthly_usd": (Decimal(0), False, Decimal(1_000_000)),
    "min_free_memory_percent": (Decimal(0), True, Decimal(100)),
}
PLAIN_DECIMAL = re.compile(r"[0-9]{1,7}(?:\.[0-9]{1,4})?")
REQUIREMENT_ID = re.compile(r"[A-Z]{1,5}-[0-9]{1,4}")


def load_limits(path: Path) -> dict[str, Limit]:
    """閾値のファイルを検証して読む。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LimitsError(f"limits file is not valid JSON: {error!r}") from None
    if not isinstance(data, dict):
        raise LimitsError("limits file must be an object")
    return {name: _limit(name, raw) for name, raw in data.items()}


def _limit(name: str, raw: object) -> Limit:
    if name not in RANGES:
        raise LimitsError(f"unknown limit: {name!r}")
    if not isinstance(raw, dict):
        raise LimitsError(f"{name}: expected an object with value and requirement")
    text, requirement = raw.get("value"), raw.get("requirement")
    if not isinstance(text, str) or not PLAIN_DECIMAL.fullmatch(text):
        raise LimitsError(f"{name}: value must be a plain decimal string")
    if not isinstance(requirement, str) or not REQUIREMENT_ID.fullmatch(requirement):
        raise LimitsError(f"{name}: requirement must be an ID such as R-05")
    value = Decimal(text)
    low, low_inclusive, high = RANGES[name]
    if not (value >= low if low_inclusive else value > low) or value > high:
        raise LimitsError(f"{name}: value is out of range")
    return Limit(value=value, requirement=requirement)
