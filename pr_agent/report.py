"""ツールの結果から、案ごとの金額の表を作る（docs/spec.md §12）。

エージェントには数字を書かせず、金額はここでツールの結果からそのまま写す。
"""

from typing import Any

from ledger.safe import safe_logical_id, safe_value


def render_alternatives(results_log: list[dict[str, Any]]) -> str:
    """最後のツールの結果を、案ごとの金額の表にする。ツールが呼ばれていなければ空。"""
    if not results_log:
        return ""
    last = results_log[-1]
    current = last["current"]
    results = [current, *(a["result"] for a in last["alternatives"])]
    # Lambda のメモリの実測があるときだけ、メモリの欄を足す（spec §14.4）
    with_memory = any(r.get("memory") for r in results)
    # 要件の閾値の判定（spec §16.8）があるときだけ、その欄を足す
    with_checks = any(r.get("checks") for r in results)
    header, rule = "| # | Changes | Monthly | Delta vs. this PR |", "|---|---|---|---|"
    if with_memory:
        header, rule = header + " Memory (peak / set) |", rule + "---|"
    if with_checks:
        header, rule = header + " Meets requirement limits |", rule + "---|"
    lines = [
        "#### Alternatives the agent tried (numbers from the AWS Price List API)",
        "",
        header,
        rule,
        _row("Current (head)", "-", current, with_memory, with_checks),
    ]
    for number, alternative in enumerate(last["alternatives"], start=1):
        changes = "; ".join(_describe(change) for change in alternative["changes"])
        lines.append(_row(str(number), changes, alternative["result"], with_memory, with_checks))
    if with_memory or with_checks:
        # 実測は PR の作者が書ける usage.json の値なので、レビューする人に出どころを示す（指摘 M-3）
        lines += [
            "",
            "Measured durations and peak memory come from usage.json in this pull request.",
        ]
    return "\n".join(lines) + "\n"


def _row(
    label: str, changes: str, result: dict[str, Any], with_memory: bool, with_checks: bool
) -> str:
    failed = "error" in result
    if failed:
        cells = [label, changes, "could not be estimated", "-"]
    else:
        cells = [label, changes, str(result.get("changed_total", "-")), _signed(result)]
    if with_memory:
        cells.append("-" if failed else _memory(result))
    if with_checks:
        cells.append("-" if failed else _checks(result))
    return "| " + " | ".join(cells) + " |"


def _checks(result: dict[str, Any]) -> str:
    """要件の閾値の判定。閾値と要件 ID も出す（spec §16.9）。値はコードが作ったものだけ。"""
    checks = result.get("checks") or {}
    words = {True: "yes", False: "no", None: "unknown"}
    parts = []
    cost = checks.get("cost")
    if cost:
        text = (
            f"{safe_value(str(cost['requirement']))} cost under"
            f" {safe_value(str(cost['limit']))} USD: {words[cost['ok']]}"
        )
        if cost.get("not_priced"):
            text += f" ({int(cost['not_priced'])} resources not priced)"
        parts.append(text)
    memory = checks.get("memory")
    if memory:
        parts.append(
            f"{safe_value(str(memory['requirement']))} memory free at least"
            f" {safe_value(str(memory['limit']))}%: {words[memory['ok']]}"
        )
    return "; ".join(parts) or "-"


def _signed(result: dict[str, Any]) -> str:
    """増える案もあるので、正の差には + を付けて減る案と見分けやすくする。"""
    delta = str(result.get("delta", "-"))
    if delta[:1].isdigit() and delta.strip("0.") != "":
        return f"+{delta}"
    return delta


def _memory(result: dict[str, Any]) -> str:
    entries = result.get("memory") or []
    text = "; ".join(
        f"{safe_logical_id(str(m['logical_id']))}: {safe_value(str(m['peak_mb']))}"
        f" / {safe_value(str(m['memory_mb']))} MB,"
        f" {safe_value(str(m['headroom_percent']))}% free"
        for m in entries
    )
    return text or "-"


def _describe(change: dict[str, Any]) -> str:
    """モデルが渡した変更を短く書く。名前と値は検査してから出す（spec §12.3）。"""
    logical_id = safe_logical_id(str(change.get("logical_id", "")))
    if change.get("action") == "remove":
        return f"{logical_id}: remove"
    prop = safe_value(str(change.get("property", "")))
    value = safe_value(str(change.get("value", "")))
    return f"{logical_id}: {prop} → {value}"
