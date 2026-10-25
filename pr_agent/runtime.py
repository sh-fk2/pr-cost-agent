"""AgentCore Runtime の入口（docs/spec.md §17）。

GitHub Actions のランナーから InvokeAgentRuntime で呼ばれ、要件レビューを1回動かして
エージェントの欄の Markdown を返す。要件と閾値は zip に同梱したファイル（デプロイしたときの
main）から読み、payload からは受け取らない。
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ledger.limits import load_limits
from pr_agent.entry import handle
from pr_agent.inputs import requirements_data
from pr_agent.review import review
from pr_agent.types import ReviewInput

# zip の根に置く（build スクリプトがコピーする）
REQUIREMENTS = Path(__file__).resolve().parent.parent / "docs" / "requirements"
REQUIREMENTS_FILE = REQUIREMENTS / "weather-daily.ears.md"
LIMITS_FILE = REQUIREMENTS / "weather-daily.limits.json"

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: Any) -> dict[str, str]:
    """payload を検証してレビューし、{"body": Markdown} か {"error": 種別} を返す。"""
    return handle(payload, _review_with_requirements)


def _review_with_requirements(review_input: ReviewInput) -> str:
    # 要件と閾値は payload から受け取らず、zip に同梱したファイルから読む（spec §17.1）
    return review(
        replace(
            review_input,
            requirements=requirements_data(REQUIREMENTS_FILE),
            limits=load_limits(LIMITS_FILE),
        )
    )


if __name__ == "__main__":
    app.run()
