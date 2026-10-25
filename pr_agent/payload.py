"""AgentCore Runtime の入口が受け取る payload の検証（docs/spec.md §17.2）。

payload は PR から来る信用できない入力なので、形・大きさ・値をここで確かめてから
ReviewInput にする。要件と閾値は payload から受け取らない（入口が同梱のファイルから読む）。
"""

import json
from typing import Any

from ledger.cli import MAX_TEMPLATE_BYTES
from ledger.transform import REGION_PREFIXES
from ledger.usage import MAX_USAGE_BYTES, UsageError, parse_usage
from pr_agent.types import ReviewInput

KEYS = frozenset({"base", "head", "head_usage", "pr_title", "pr_body", "mode", "region"})
MODES = frozenset({"none", "tool"})
MAX_TITLE_CHARS = 300
MAX_BODY_CHARS = 20_000


class PayloadError(ValueError):
    """payload の形や値が仕様に合わない。"""


def parse_payload(payload: Any) -> ReviewInput:
    """payload を検証して ReviewInput にする（requirements と limits は空のまま）。"""
    if not isinstance(payload, dict):
        raise PayloadError("payload must be an object")
    unknown = set(payload) - KEYS
    if unknown:
        raise PayloadError(f"unknown keys: {sorted(map(repr, unknown))}")
    mode, region = payload.get("mode"), payload.get("region")
    # リストや dict は in で比べると例外になるので、先に文字列かを見る（指摘 L-3）
    if not isinstance(mode, str) or mode not in MODES:
        raise PayloadError("mode must be 'none' or 'tool'")
    if not isinstance(region, str) or region not in REGION_PREFIXES:
        raise PayloadError("unsupported region")
    return ReviewInput(
        base=_template(payload.get("base"), "base"),
        head=_template(payload.get("head"), "head"),
        mode="tool" if mode == "tool" else "none",
        region=region,
        head_usage=_usage(payload.get("head_usage")),
        pr_title=_text(payload.get("pr_title", ""), "pr_title", MAX_TITLE_CHARS),
        pr_body=_text(payload.get("pr_body", ""), "pr_body", MAX_BODY_CHARS),
    )


def _template(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PayloadError(f"{name} must be a template object")
    # 日本語の多いテンプレートを実ファイルより大きく数えないよう、ensure_ascii=False で数える
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise PayloadError(f"{name} is too large")
    resources = value.get("Resources", {})
    if not isinstance(resources, dict) or not all(
        isinstance(r, dict) and isinstance(r.get("Type"), str) for r in resources.values()
    ):
        raise PayloadError(f"{name} must have Resources with a Type for each resource")
    return value


def _usage(value: Any) -> Any:
    if value is None:
        return {}
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_USAGE_BYTES:
        raise PayloadError("head_usage is too large")
    try:
        return parse_usage(value)
    except UsageError as error:
        raise PayloadError(f"head_usage: {error}") from None


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise PayloadError(f"{name} must be a string of at most {limit} characters")
    return value
