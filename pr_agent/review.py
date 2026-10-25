"""要件レビューを1回動かし、PR コメントのエージェントの欄（Markdown）を返す。

手元の CLI（`python -m pr_agent`）と AgentCore Runtime の入口（pr_agent.runtime）が、
同じ順番（検査 → エージェント → 無害化 → 照合 → 表）で呼ぶ。
"""

import os
from typing import Any

import boto3

from ledger.agent_tool import CostTool
from ledger.cli import pricing_client
from ledger.price_list import PriceCache, estimate
from ledger.usage import Usage
from pr_agent.agent import TOOL_REMINDER, build_agent, user_message
from pr_agent.check import annotate, sanitize, unverified_numbers
from pr_agent.inputs import resource_diff, screen, untrusted_texts, usage_data
from pr_agent.report import render_alternatives
from pr_agent.types import ReviewError, ReviewInput

REQUIRED_ENV = ("BEDROCK_MODEL_ID", "GUARDRAIL_ID", "GUARDRAIL_VERSION")
BEDROCK_REGION = "us-east-1"
SKIPPED = (
    "### Requirements review\n\nThe agent was not run, because the guardrail flagged part of the "
    "pull request or the template as instructions to the model.\n"
)


def review(review_input: ReviewInput) -> str:
    """エージェントを1回動かし、エージェントの欄の Markdown を返す。"""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise ReviewError(f"Missing environment variables: {', '.join(missing)}")
    if not review_input.requirements:
        # 要件が0件だと「すべて満たす」ように見えるので、レビューしない（spec §16.11）
        raise ReviewError("No requirements were given")
    inp = review_input
    message = user_message(
        inp.pr_title,
        inp.pr_body,
        resource_diff(inp.base, inp.head),
        usage_data(inp.head_usage),
        inp.requirements,
    )
    guardrail = (os.environ["GUARDRAIL_ID"], os.environ["GUARDRAIL_VERSION"])
    runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    # モデルに渡す文章そのものと、テンプレートの中の文章の両方を検査する（指摘6）
    if screen(
        runtime, *guardrail, [message, *untrusted_texts(inp.pr_title, inp.pr_body, inp.head)]
    ):
        return SKIPPED
    return _run_agent(inp, message, guardrail)


def _run_agent(inp: ReviewInput, message: str, guardrail: tuple[str, str]) -> str:
    cost_tool = _cost_tool(inp.head, inp.region, inp.head_usage) if inp.mode == "tool" else None
    results_log: list[Any] = []
    model_id = os.environ["BEDROCK_MODEL_ID"]
    agent = build_agent(model_id, BEDROCK_REGION, *guardrail, cost_tool, results_log, inp.limits)
    answer = str(agent(message))
    if cost_tool and not results_log:
        # ツールを呼ばずに答えたら、1回だけ促す（spec §16.6）
        answer = str(agent(TOOL_REMINDER))
    text = sanitize(answer)
    if cost_tool and not results_log:
        text += "\n\n> **Note:** The agent did not call estimate_cost in this run.\n"
    # usage.json の値と要件の文章もモデルに渡した数字なので、照合では「来た数字」として扱う
    known = [results_log, usage_data(inp.head_usage), inp.requirements]
    checked = annotate(text, unverified_numbers(text, known))
    title = "after: one new tool, estimate_cost" if cost_tool else "before: no tools"
    table = render_alternatives(results_log)
    return f"### Requirements review ({title})\n\n{checked}\n" + (f"\n{table}" if table else "")


def _cost_tool(head: dict[str, Any], region: str, usage: Usage) -> CostTool:
    pricing = pricing_client()
    # 今の構成と各案で同じ単価を何度も取らない（Price List API の呼び出しの速さの上限が低い）
    cache: PriceCache = {}
    return CostTool(
        head, region, lambda rows: estimate(pricing, rows, region, cache=cache), usage=usage
    )
