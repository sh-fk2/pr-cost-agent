"""エージェントに渡す前の、外から来た入力の準備（docs/spec.md §10）。"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ears_trace.parser import parse

from ledger.safe import REDACTED, safe_logical_id, safe_resource_type, safe_schedule, safe_value
from ledger.usage import Usage

# ApplyGuardrail の1回に入れる文字数の目安。テンプレートの説明文が多くても分けて送る
MAX_CHARS_PER_CALL = 20_000
# 差分の要約で値を出す、価格に効くプロパティ（spec §10.3）
PRICE_PROPERTIES = (
    "InstanceType",
    "VolumeType",
    "Size",
    # サーバーレス（spec §14.4）
    "WorkerType",
    "NumberOfWorkers",
    "MemorySize",
    "StateMachineType",
    # 要件のレビュー（締め切り、/tmp、起動の時刻。spec §16.4）
    "Timeout",
    "TimeoutSeconds",
    "EphemeralStorage",
    "ScheduleExpression",
    "Schedule",
)
SCHEDULE_PROPERTIES = frozenset({"ScheduleExpression", "Schedule"})
# 差分の要約に出す値の、入れ子の深さと要素数の上限
MAX_VALUE_DEPTH = 3
MAX_VALUE_ITEMS = 20
TOO_LARGE = "[too large]"


def untrusted_texts(pr_title: str, pr_body: str, head: dict[str, Any]) -> list[str]:
    """Guardrail で検査する文章を集める。

    空白のない文字列（識別子や ARN）は文章として指示を書きにくいので集めない。
    それらはツールの結果でも名前の検査を通してから渡す（spec §9.3）。
    """
    template_texts = [s for s in _strings(head) if any(c.isspace() for c in s)]
    return [t for t in (pr_title, pr_body, *template_texts) if t.strip()]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def screen(client: Any, guardrail_id: str, guardrail_version: str, texts: list[str]) -> bool:
    """文章を ApplyGuardrail にかけ、止めるべきなら True を返す。"""
    for batch in _batches(texts):
        response = client.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=guardrail_version,
            source="INPUT",
            content=[{"text": {"text": text}} for text in batch],
        )
        if response["action"] == "GUARDRAIL_INTERVENED":
            return True
    return False


def _batches(texts: list[str]) -> Iterator[list[str]]:
    """1回の呼び出しに入れる文字数を抑えて分ける。"""
    batch: list[str] = []
    size = 0
    # 1つで上限を超える文章は、上限の長さで切って別々に送る
    pieces = [
        text[i : i + MAX_CHARS_PER_CALL]
        for text in texts
        for i in range(0, max(len(text), 1), MAX_CHARS_PER_CALL)
    ]
    for text in pieces:
        if batch and size + len(text) > MAX_CHARS_PER_CALL:
            yield batch
            batch, size = [], 0
        batch.append(text)
        size += len(text)
    if batch:
        yield batch


def resource_diff(base: dict[str, Any], head: dict[str, Any]) -> list[dict[str, Any]]:
    """テンプレートの差分を、論理 ID ごとに要約する。変わっていないリソースは出さない。"""
    before = base.get("Resources", {})
    after = head.get("Resources", {})
    entries = []
    for logical_id in [*before, *(k for k in after if k not in before)]:
        old, new = before.get(logical_id), after.get(logical_id)
        if old == new:
            continue
        change = "added" if old is None else "removed" if new is None else "modified"
        entries.append(
            {
                "logical_id": safe_logical_id(logical_id),
                "type": safe_resource_type(str((new or old or {}).get("Type"))),
                "change": change,
                "price_properties": _price_properties(old, new),
            }
        )
    return entries


def _price_properties(old: Any, new: Any) -> dict[str, dict[str, Any]]:
    def props(resource: Any) -> dict[str, Any]:
        return resource.get("Properties", {}) if resource else {}

    result = {}
    for name in PRICE_PROPERTIES:
        values = (props(old).get(name), props(new).get(name))
        if values != (None, None) and values[0] != values[1]:
            before, after = (_safe_value(name, v) for v in values)
            result[name] = {"before": before, "after": after}
    return result


def _safe_value(name: str, value: Any, depth: int = 1) -> Any:
    """値もテンプレートから来るので検査する。dict と list は中まで見る。数値はそのまま。

    深さと要素数に上限を付け、許されない dict のキーは番号付きの伏せ字にして衝突させない
    （セキュリティレビュー M-2、L-5）。
    """
    if isinstance(value, dict | list) and (depth > MAX_VALUE_DEPTH or len(value) > MAX_VALUE_ITEMS):
        return TOO_LARGE
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items(), start=1):
            safe_key = safe_value(str(key))
            label = f"[redacted-{index}]" if safe_key == REDACTED else safe_key
            result[label] = _safe_value(name, item, depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_value(name, v, depth + 1) for v in value]
    if isinstance(value, str):
        return safe_schedule(value) if name in SCHEDULE_PROPERTIES else safe_value(value)
    return value


def usage_data(usage: Usage) -> dict[str, Any]:
    """エージェントに渡す使用量の前提（spec §14.4）。

    source（自由記述）は外から来た文章なので渡さない。値は検証済みの数と、決まった名前だけ。
    """
    data: dict[str, Any] = {}
    for logical_id, factors in usage.items():
        data[safe_logical_id(logical_id)] = {
            name: (
                {"values": {str(m): str(v) for m, v in sorted(f.values.items())}, "basis": f.basis}
                if f.values is not None
                else {"value": str(f.value), "basis": f.basis}
            )
            for name, f in factors.items()
        }
    return data


def requirements_data(path: Path) -> list[dict[str, str]]:
    """EARS の要件を、ears-trace の parser で {id, pattern, text} の一覧にする（spec §16.1）。

    要件のファイルは main のチェックアウトから読むので、PR の作者には変えられない。
    """
    return [
        {"id": r.id, "pattern": r.pattern.value, "text": r.text}
        for r in parse(path.read_text(encoding="utf-8"))
    ]
