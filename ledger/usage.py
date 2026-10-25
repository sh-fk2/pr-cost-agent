"""使用量のファイル（usage.json）を読む（docs/spec.md §14.1）。

テンプレートだけでは決まらない実行時間や回数を、論理 ID ごとに持つ。PR のコードから来るので、
数字と決まった名前だけを受け付ける。
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, get_args

from ledger.safe import REDACTED, safe_logical_id

Basis = Literal["requirement", "measured", "assumed"]


@dataclass(frozen=True)
class Factor:
    """使用量の1項目。value か values（メモリごとの実測）のどちらかを持つ。"""

    value: Decimal | None
    values: dict[int, Decimal] | None
    basis: Basis


Usage = dict[str, dict[str, Factor]]

# usage.json は PR のコードと一緒に来るので、大きさに上限を付ける
MAX_USAGE_BYTES = 100_000
# 値の形（ASCII の数字、小数は6桁まで）と、メモリごとの実測の件数の上限
PLAIN_DECIMAL = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,6})?")
MAX_MEMORY_SIZES = 16

# Lambda に設定できるメモリの範囲（MB）
MIN_MEMORY_MB = 128
MAX_MEMORY_MB = 10_240

# 受け付ける項目と、値の上限（spec §14.1）
LIMITS = {
    "runs_per_month": Decimal(1_000_000),
    "seconds_per_run": Decimal(172_800),
    "invocations_per_month": Decimal(100_000_000),
    "duration_ms_by_memory": Decimal(900_000),
    "max_memory_used_mb_by_memory": Decimal(10_240),
    "executions_per_month": Decimal(100_000_000),
    "transitions_per_execution": Decimal(25_000),
}


class UsageError(ValueError):
    """usage.json の形や値が仕様に合わない。"""


def load_usage(path: Path) -> Usage:
    """大きさを確かめてから usage.json を読む。"""
    size = path.stat().st_size
    if size > MAX_USAGE_BYTES:
        raise UsageError(f"usage.json is too large ({size} bytes)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise UsageError("usage.json is not UTF-8") from None
    except RecursionError:
        raise UsageError("usage.json is nested too deeply") from None
    except json.JSONDecodeError as error:
        raise UsageError(f"usage.json is not valid JSON: {error}") from None
    return parse_usage(data)


def parse_usage(data: Any) -> Usage:
    """usage.json の中身を検証して読む。"""
    resources = data.get("resources") if isinstance(data, dict) else None
    if not isinstance(resources, dict):
        raise UsageError("usage.json has the wrong shape: expected {'resources': {...}}")
    usage: Usage = {}
    for logical_id, factors in resources.items():
        if safe_logical_id(logical_id) == REDACTED:
            raise UsageError("a logical id in usage.json is not a plain identifier")
        if not isinstance(factors, dict):
            raise UsageError("usage.json has the wrong shape: items must be an object")
        usage[logical_id] = {name: _factor(name, raw) for name, raw in factors.items()}
    return usage


def _factor(name: str, raw: Any) -> Factor:
    if name not in LIMITS:
        raise UsageError(f"unknown item: {name!r}")
    if not isinstance(raw, dict):
        raise UsageError(f"{name}: wrong shape, expected an object with value and basis")
    if raw.get("basis") not in get_args(Basis):
        raise UsageError(f"{name}: basis must be one of {get_args(Basis)}")
    # メモリごとの実測（*_by_memory）は values、ほかは value で書く
    by_memory = name.endswith("_by_memory")
    if by_memory != ("values" in raw):
        form = "values" if by_memory else "value"
        raise UsageError(f"{name}: write it with {form}")
    if by_memory:
        if not isinstance(raw["values"], dict):
            raise UsageError(f"{name}: wrong shape, values must be an object")
        if len(raw["values"]) > MAX_MEMORY_SIZES:
            raise UsageError(f"{name}: at most {MAX_MEMORY_SIZES} memory sizes")
        values = {_memory(memory): _number(name, v) for memory, v in raw["values"].items()}
        return Factor(value=None, values=values, basis=raw["basis"])
    return Factor(value=_number(name, raw.get("value")), values=None, basis=raw["basis"])


def _memory(text: str) -> int:
    """values のキー（Lambda のメモリの MB）。"""
    # isdigit は "²" のような Unicode の数字も通すので、ASCII に限る
    if not (text.isascii() and text.isdigit()) or not MIN_MEMORY_MB <= int(text) <= MAX_MEMORY_MB:
        raise UsageError(f"memory must be an integer from {MIN_MEMORY_MB} to {MAX_MEMORY_MB}")
    return int(text)


def _number(name: str, text: Any) -> Decimal:
    """値は10進数の文字列だけを受け付ける（JSON の数値は浮動小数を経るため）。"""
    if not isinstance(text, str):
        raise UsageError(f"{name}: the value must be a decimal string")
    # 空白・指数表記・全角数字・区切りの _ を通さない（Decimal はどれも受け付ける）
    if not PLAIN_DECIMAL.fullmatch(text):
        raise UsageError(f"{name}: not a plain decimal number like 123 or 25.6")
    number = Decimal(text)
    if not Decimal(0) <= number <= LIMITS[name]:
        raise UsageError(f"{name}: the value must be between 0 and {LIMITS[name]}")
    return number
