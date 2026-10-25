"""レビューの入力と例外の型（Strands などの重い依存を持たない。ランナーからも import する）。"""

from dataclasses import dataclass, field
from typing import Any, Literal

from ledger.limits import Limit
from ledger.usage import Usage

Mode = Literal["none", "tool"]


class ReviewError(ValueError):
    """レビューを始められない（設定や入力が足りない）。"""


@dataclass(frozen=True)
class ReviewInput:
    """レビューの入力。要件と閾値は main（またはデプロイしたときの main）から読んだもの。"""

    base: dict[str, Any]
    head: dict[str, Any]
    mode: Mode
    region: str
    head_usage: Usage = field(default_factory=dict)
    pr_title: str = ""
    pr_body: str = ""
    requirements: list[dict[str, str]] = field(default_factory=list)
    limits: dict[str, Limit] = field(default_factory=dict)
