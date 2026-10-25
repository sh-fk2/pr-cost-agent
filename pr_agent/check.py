"""エージェントの文章の数字を、ツールの結果と照らし合わせる（docs/spec.md §11）。"""

import re
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

# 金額とみなす形（spec §11.1）。小数2〜4桁を先に当てる（"199.14 USD" は "199.14" として拾う）。
# 4桁は合計が小さい比較の表示（spec §14.3）。時間・容量・割合の単位が続く数は金額とみなさない
MONEY = re.compile(
    r"[-+]?\d[\d,]*\.\d{2,4}(?![\d%])(?!\s?(?:hours?|minutes?|seconds?|ms|MB|GB|percent)\b)"
    r"|~?\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?USD"
)


def unverified_numbers(text: str, tool_results: list[Any]) -> list[str]:
    """文章に出てくる金額のうち、ツールの結果に無いものを返す（出てきた順、重複なし）。"""
    known = {_normalize(s) for s in _strings(tool_results) for s in MONEY.findall(s)}
    found = [m for m in MONEY.findall(text) if _normalize(m) not in known]
    return list(dict.fromkeys(found))


def _normalize(number: str) -> Decimal | None:
    """符号・桁区切り・$・USD を外し、数としての値にする（32 と 32.00 は同じ値）。"""
    digits = re.sub(r"[^\d.]", "", number)
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def annotate(text: str, unverified: list[str]) -> str:
    """ツールから来ていない数字があれば、文章の末尾に注記を付ける。文章は消さない。"""
    if not unverified:
        return text
    numbers = ", ".join(unverified)
    note = (
        f"> **Number check:** Numbers not found in the tool results: {numbers}. "
        "Do not rely on them."
    )
    return f"{text.rstrip()}\n\n{note}\n"


MAX_AGENT_TEXT = 4000
TABLE_ROW = re.compile(r"^[ \t]*\|.*\n?", re.MULTILINE)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9][A-Za-z0-9-]*)")


def sanitize(text: str) -> str:
    """エージェントの文章から、リンク・画像・HTML・URL・メンションを無効にする。

    コメントは bot の名義で出るので、プロンプトインジェクションが通った場合でも、
    誘導のリンクや通知のスパムにならないようにする（セキュリティレビューの指摘4）。
    """
    # 金額の表はツールの結果からコードが作る。モデルが書いた表は取り除く（spec §12.1）
    cleaned = TABLE_ROW.sub("", text)
    cleaned = IMAGE.sub("", cleaned)
    cleaned = LINK.sub(r"\1", cleaned)
    cleaned = REFERENCE.sub("", cleaned)
    cleaned = cleaned.replace("<", "&lt;")
    cleaned = URL.sub("[link removed]", cleaned)
    cleaned = MENTION.sub(r"`@\1`", cleaned)
    if len(cleaned) > MAX_AGENT_TEXT:
        cleaned = cleaned[:MAX_AGENT_TEXT] + "\n\n(truncated)"
    return cleaned
