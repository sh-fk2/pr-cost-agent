"""options.json（skill が書いた試算の入力）を検証して Plan にする。"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, get_args

from ledger.sizing import Basis, Factor, Plan, SizingLine, SizingOption

BASES = frozenset(get_args(Basis))
# 行の key は倍率・案と "/" でつないで一意にするので、"/" などを含めない（spec §8.1）
IDENTIFIER = re.compile(r"[A-Za-z0-9-]+")


class PlanError(ValueError):
    """options.json の中身が仕様（docs/spec.md §8.1・§8.3）に合わない。"""


def parse_plan(data: dict[str, Any]) -> Plan:
    """options.json を読み込んだ dict を検証し、Plan にする。"""
    try:
        return _plan(data)
    except KeyError as error:
        raise PlanError(f"必須の項目がありません: {error.args[0]}") from error
    except (TypeError, AttributeError) as error:
        # 配列のところに文字列がある、など形が仕様と違う
        raise PlanError(f"入力の形が仕様（docs/spec.md §8.1）と違います: {error}") from error


def _plan(data: dict[str, Any]) -> Plan:
    scales = _scales(data["scales"])
    options = [_option(o) for o in data["options"]]
    ids = {o.id for o in options}
    if len(ids) != len(options):
        raise PlanError("案の id が重複しています")
    for role in ("baseline", "adopted"):
        if data[role] not in ids:
            raise PlanError(f"{role} の案がありません: {data[role]}")
    return Plan(
        requirement=data["requirement"],
        scales=scales,
        baseline=data["baseline"],
        adopted=data["adopted"],
        options=options,
    )


def _scales(value: Any) -> list[int]:
    if not isinstance(value, list):
        raise PlanError(f"scales は配列にしてください: {value!r}")
    # bool は int の一種なので先に除く
    bad = [s for s in value if isinstance(s, bool) or not isinstance(s, int) or s < 1]
    if bad:
        raise PlanError(f"scales は 1 以上の整数にしてください: {bad}")
    scales: list[int] = value
    if len(set(scales)) != len(scales):
        raise PlanError(f"scales に重複があります: {scales}")
    if 1 not in scales:
        raise PlanError("scales に 1 がありません（今の処理量の試算が要ります）")
    return scales


def _identifier(kind: str, value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PlanError(f"{kind} は英数字とハイフンにしてください: {value!r}")
    return value


def _option(data: dict[str, Any]) -> SizingOption:
    option_id = _identifier("案の id", data["id"])
    lines = [_line(line) for line in data["lines"]]
    keys = [line.key for line in lines]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise PlanError(f"案 {data['id']} の行の key が重複しています: {duplicated}")
    return SizingOption(id=option_id, label=data["label"], lines=lines)


def _line(data: dict[str, Any]) -> SizingLine:
    if not data["factors"]:
        raise PlanError(f"行 {data['key']} の factors が空です")
    return SizingLine(
        key=_identifier("行の key", data["key"]),
        service_code=data["serviceCode"],
        usage_type=data["usageType"],
        operation=data["operation"],
        label=data["label"],
        factors=[_factor(f) for f in data["factors"]],
    )


def _factor(data: dict[str, Any]) -> Factor:
    name = data["name"]
    if data["basis"] not in BASES:
        raise PlanError(f"{name} の basis が {sorted(BASES)} のどれでもありません: {data['basis']}")
    divisor = _optional_decimal(name, data.get("divisor"))
    if divisor == 0:
        raise PlanError(f"{name} の divisor が 0 です")
    return Factor(
        name=name,
        value=_decimal(name, data["value"]),
        basis=data["basis"],
        source=data["source"],
        divisor=divisor,
        scales=_flag(name, data.get("scales", False)),
        limit=_optional_decimal(name, data.get("limit")),
    )


def _flag(name: str, value: Any) -> bool:
    # 文字列の "false" を真として扱わないよう、JSON の真偽値だけを受け付ける
    if not isinstance(value, bool):
        raise PlanError(f"{name} の scales は true か false にしてください: {value!r}")
    return value


def _decimal(name: str, value: Any) -> Decimal:
    # JSON の数値は浮動小数を経るので受け付けない（spec §2.4）
    if not isinstance(value, str):
        raise PlanError(f"{name} の値は 10 進数の文字列で書いてください: {value!r}")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise PlanError(f"{name} の値が数ではありません: {value!r}") from error
    if not number.is_finite():
        raise PlanError(f"{name} の値が数ではありません: {value!r}")
    return number


def _optional_decimal(name: str, value: Any) -> Decimal | None:
    return None if value is None else _decimal(name, value)
