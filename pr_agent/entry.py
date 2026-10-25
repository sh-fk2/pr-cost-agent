"""AgentCore Runtime の入口の中身（SDK に依存しない部分。docs/spec.md §17.2）。

返すのは本文か、決まった種別のエラーだけ。エラーの詳細（アカウント ID やロール名を含みうる）は
CloudWatch のログにだけ出す（セキュリティレビュー M-1）。
"""

import logging
from collections.abc import Callable
from typing import Any

from pr_agent.payload import PayloadError, parse_payload
from pr_agent.types import ReviewError, ReviewInput

logger = logging.getLogger(__name__)


def handle(payload: Any, run: Callable[[ReviewInput], str]) -> dict[str, str]:
    """payload を検証して run（要件と閾値を足してレビューする関数）に渡す。"""
    try:
        review_input = parse_payload(payload)
    except PayloadError:
        logger.warning("invalid payload", exc_info=True)
        return {"error": "invalid_payload"}
    try:
        return {"body": run(review_input)}
    except ReviewError:
        logger.warning("review unavailable", exc_info=True)
        return {"error": "review_unavailable"}
    except Exception:
        # 想定外の例外も、文面は返さない（ログにだけ出す）
        logger.exception("review failed")
        return {"error": "internal_error"}
