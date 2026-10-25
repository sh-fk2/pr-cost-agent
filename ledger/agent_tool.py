"""② のエージェントが持つただ1つのツール estimate_cost の中身（docs/spec.md §9）。

Strands の @tool で包むのは agent 側。ここはモデルの入力を検証し、見積もりの結果を
数字と検査済みの名前だけにして返す。
"""

import copy
import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeGuard

from botocore.exceptions import BotoCoreError, ClientError

from ledger.compare import EstimateFn, compare_templates
from ledger.diff import display_places, format_usd
from ledger.estimator import EstimateError
from ledger.limits import Limit
from ledger.malformed import MALFORMED_INPUT_ERRORS
from ledger.safe import safe_logical_id, safe_resource_type
from ledger.transform import LAMBDA_DEFAULT_MEMORY_MB
from ledger.usage import MAX_MEMORY_MB, MIN_MEMORY_MB, Usage

logger = logging.getLogger(__name__)

# 変えてよいプロパティ（spec §9.2.2）
SETTABLE = {
    "AWS::EC2::Instance": {"InstanceType"},
    "AWS::EC2::Volume": {"VolumeType", "Size"},
    # 所要時間は usage.json の実測から引く（spec §14.4）
    "AWS::Lambda::Function": {"MemorySize"},
}


# 1回の呼び出しの変更の件数と、1回の実行で呼べる回数（spec §9.2.6）
MAX_CHANGES_PER_CALL = 5
MAX_CALLS_PER_RUN = 5
# 外してよいリソース（spec §9.2.4）
REMOVABLE = frozenset({"AWS::EC2::NatGateway", "AWS::EC2::VPCEndpoint"})
INSTANCE_TYPE = re.compile(r"[a-z0-9]+\.[a-z0-9]+")
VOLUME_TYPES = frozenset({"gp2", "gp3", "io1", "io2", "st1", "sc1", "standard"})
MAX_VOLUME_GIB = 65536


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_value(prop: str, value: Any) -> bool:
    """値の形を確かめる（spec §9.2.3）。bool は int の一種なので先に除く。"""
    if prop == "InstanceType":
        return isinstance(value, str) and INSTANCE_TYPE.fullmatch(value) is not None
    if prop == "VolumeType":
        return isinstance(value, str) and value in VOLUME_TYPES
    if prop == "MemorySize":
        return _is_int(value) and MIN_MEMORY_MB <= value <= MAX_MEMORY_MB
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_VOLUME_GIB


class CostTool:
    """head のテンプレートに「もしも」の変更を当てて見積もる。"""

    def __init__(
        self,
        head: dict[str, Any],
        region: str,
        estimate: EstimateFn,
        *,
        usage: Usage | None = None,
    ) -> None:
        self._head = head
        self._region = region
        self._estimate = estimate
        # 案も head と同じ使用量の前提で見積もる
        self._usage = usage or {}
        self._calls = 0

    def estimate_cost(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """変更を当てた版を見積もり、head との差を返す。"""
        if self._calls >= MAX_CALLS_PER_RUN:
            return {"error": f"The call limit ({MAX_CALLS_PER_RUN}) for this run is used up."}
        # 入力の誤りで呼び直し続けないよう、検証で弾いた呼び出しも数える
        self._calls += 1
        if len(changes) > MAX_CHANGES_PER_CALL:
            return {"error": f"Pass at most {MAX_CHANGES_PER_CALL} changes per call."}
        error = _validate(self._head, changes, self._usage)
        if error is not None:
            # モデルに直させるため、例外ではなく結果として返す（spec §9.2）
            return {"error": error}
        changed = _apply(self._head, changes)
        try:
            comparison = compare_templates(
                self._head,
                changed,
                self._region,
                self._estimate,
                base_usage=self._usage,
                head_usage=self._usage,
            )
        except (EstimateError, ClientError, BotoCoreError, *MALFORMED_INPUT_ERRORS):
            # エラーの文面にはアカウント ID やロール名が入りうるので、モデルには種別だけを返す
            logger.warning("estimate failed", exc_info=True)
            return {"error": "estimate failed"}
        # head の合計だけで桁を決め、呼び出し（今の構成と各案）をまたいでそろえる（spec §14.3）
        places = display_places(comparison.base_total)
        return {
            "head_total": format_usd(comparison.base_total, places),
            "changed_total": format_usd(comparison.head_total, places),
            "delta": format_usd(comparison.delta, places),
            "lines": [
                {
                    "key": safe_logical_id(c.key),
                    "change": c.status,
                    "before": _money(c.base, places),
                    "after": _money(c.head, places),
                    "delta": format_usd(c.delta, places),
                }
                for c in comparison.changes
            ],
            # メモリの余裕はモデルに計算させず、実測からここで出す（spec §14.4）
            "memory": _memory_headroom(changed, self._usage),
            # 実測が無く、メモリの空きを判定できない Lambda（spec §16.10）
            "unmeasured_lambdas": _unmeasured_lambdas(changed, self._usage),
            # 理由の文は返さない。名前と種別だけにする（spec §9.3）
            "not_included": [
                {
                    "logical_id": safe_logical_id(f.logical_id),
                    "type": safe_resource_type(f.resource_type),
                    "kind": f.kind,
                }
                for f in comparison.findings
            ],
        }


def _validate(head: dict[str, Any], changes: list[dict[str, Any]], usage: Usage) -> str | None:
    resources = head.get("Resources", {})
    for change in changes:
        logical_id = change.get("logical_id")
        if logical_id not in resources:
            return f"Unknown logical_id: {logical_id}. Use a resource that exists in the template."
        if "Condition" in resources[logical_id]:
            return f"{logical_id} has a Condition, so this tool does not change it."
        resource_type = resources[logical_id]["Type"]
        action = change.get("action")
        if action == "remove" and resource_type not in REMOVABLE:
            return f"{resource_type} cannot be removed. Removable: {sorted(REMOVABLE)}."
        if action not in ("set", "remove"):
            return f"Unknown action: {action}. Use 'set' or 'remove'."
        if action == "set":
            prop = change.get("property")
            if prop not in SETTABLE.get(resource_type, set()):
                allowed = sorted(SETTABLE.get(resource_type, set()))
                return f"{prop} cannot be changed on {resource_type}. Allowed: {allowed}."
            if not _valid_value(prop, change.get("value")):
                return f"The value for {prop} is not valid."
            if prop == "MemorySize":
                error = _measured_memory_error(usage, str(logical_id), change["value"])
                if error is not None:
                    return error
    return None


def _memory_headroom(template: dict[str, Any], usage: Usage) -> list[dict[str, Any]]:
    """Lambda ごとの、設定したメモリと実測の最大使用量と余裕（%）。実測が無いものは出さない。"""
    result = []
    for logical_id, resource in template.get("Resources", {}).items():
        if resource.get("Type") != "AWS::Lambda::Function":
            continue
        memory = resource.get("Properties", {}).get("MemorySize", LAMBDA_DEFAULT_MEMORY_MB)
        if not _is_int(memory):
            continue
        factor = usage.get(logical_id, {}).get("max_memory_used_mb_by_memory")
        peak = (factor.values or {}).get(memory) if factor else None
        if peak is None:
            continue
        headroom = ((memory - peak) / memory * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        result.append(
            {
                "logical_id": safe_logical_id(logical_id),
                "memory_mb": memory,
                "peak_mb": str(peak),
                "headroom_percent": int(headroom),
            }
        )
    return result


def _unmeasured_lambdas(template: dict[str, Any], usage: Usage) -> list[str]:
    measured = {m["logical_id"] for m in _memory_headroom(template, usage)}
    return [
        safe_logical_id(logical_id)
        for logical_id, resource in template.get("Resources", {}).items()
        if resource.get("Type") == "AWS::Lambda::Function"
        and safe_logical_id(logical_id) not in measured
    ]


def _measured_memory_error(usage: Usage, logical_id: str, memory: int) -> str | None:
    """所要時間はメモリで変わるので、実測のあるメモリだけを受け付ける（spec §14.4）。"""
    factor = usage.get(logical_id, {}).get("duration_ms_by_memory")
    measured = sorted(factor.values or {}) if factor else []
    if memory in measured:
        return None
    return f"No measured duration for MemorySize {memory}. Measured sizes: {measured}."


def _apply(head: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """変更を当てた新しいテンプレートを返す。head は書き換えない。"""
    changed = copy.deepcopy(head)
    for change in changes:
        if change["action"] == "remove":
            del changed["Resources"][change["logical_id"]]
            continue
        resource = changed["Resources"][change["logical_id"]]
        resource["Properties"][change["property"]] = change["value"]
    return changed


def _money(amount: Decimal | None, places: Decimal) -> str | None:
    return None if amount is None else format_usd(amount, places)


def requirement_checks(result: dict[str, Any], limits: dict[str, Limit]) -> dict[str, Any]:
    """ツールの結果を、要件の閾値のファイルの値と比べる（spec §16.9、§16.10）。

    Nova 2 Lite は「空き 1%」を「10% 以上空いている」と判定したので、比べるのはコードが行う。
    閾値はモデルから受け取らない（PR の本文で誘導されたモデルが緩い値を渡せるため）。
    """
    checks: dict[str, Any] = {}
    cost_limit = limits.get("max_monthly_usd")
    if cost_limit is not None:
        total = result.get("changed_total")
        checks["cost"] = {
            "ok": None if total is None else Decimal(str(total)) < cost_limit.value,
            "limit": str(cost_limit.value),
            "requirement": cost_limit.requirement,
            # 値段を付けられなかったリソースは月額に入っていない
            "not_priced": len(result.get("not_included") or []),
        }
    memory_limit = limits.get("min_free_memory_percent")
    if memory_limit is not None:
        checks["memory"] = {
            "ok": _memory_ok(result, memory_limit.value),
            "limit": str(memory_limit.value),
            "requirement": memory_limit.requirement,
        }
    return checks


def _memory_ok(result: dict[str, Any], min_free_percent: Decimal) -> bool | None:
    """すべての Lambda で空きが下限以上か。実測の無い Lambda があれば分からない（None）。"""
    memory = result.get("memory") or []
    if not memory or result.get("unmeasured_lambdas"):
        return None
    # 丸める前の値で比べる（9.5% を 10% として通さない）
    return all(
        (Decimal(m["memory_mb"]) - Decimal(m["peak_mb"])) / Decimal(m["memory_mb"]) * 100
        >= min_free_percent
        for m in memory
    )
