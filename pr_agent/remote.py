"""GitHub Actions のランナーから、AgentCore Runtime の要件レビューを呼ぶ（docs/spec.md §17.3）。

ランナーが持つのは、ランタイムを呼ぶ権限だけ。エージェント（Strands のループとツール）と
Bedrock・Price List API の呼び出しは、ランタイムの中で動く。
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ledger.cli import DEFAULT_TEMPLATE_REGION, load_template
from ledger.usage import MAX_USAGE_BYTES
from pr_agent.payload import MAX_BODY_CHARS, MAX_TITLE_CHARS

AGENTCORE_REGION = "us-east-1"
# エージェントは 20〜40 秒かかる。再試行すると二重に動くので、再試行はしない
AGENTCORE_CONFIG = Config(read_timeout=300, retries={"total_max_attempts": 1})


# ランタイムが返すエラーの種別（pr_agent.entry）
ERROR_KINDS = frozenset({"invalid_payload", "review_unavailable", "internal_error"})
TRUNCATED = "\n\n(truncated)"


class RemoteError(RuntimeError):
    """ランタイムがエラーを返した。"""


def session_id(pr_number: str, run_id: str, attempt: str, mode: str) -> str:
    """PR・実行・mode から、33 文字以上のセッション ID を作る。毎回新しくする。"""
    return f"pr-{pr_number}-run-{run_id}-{attempt}-{mode}-{uuid.uuid4().hex}"


def invoke_review(client: Any, runtime_arn: str, session: str, payload: dict[str, Any]) -> str:
    """ランタイムを呼び、エージェントの欄の Markdown を返す。"""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    result = json.loads(response["response"].read())
    if "error" in result:
        # 種別の許可リストだけを出す。ほかの文面はログに出さない（セキュリティレビュー M-1）
        kind = result["error"]
        raise RemoteError(kind if kind in ERROR_KINDS else "unknown_error")
    return str(result["body"])


def build_payload(
    base: dict[str, Any],
    head: dict[str, Any],
    usage: Any,
    pr_title: str,
    pr_body: str,
    mode: str,
    region: str,
) -> dict[str, Any]:
    """ランタイムに送る payload。PR のタイトルと本文は上限で切り詰める（指摘 L-3）。"""
    if len(pr_body) > MAX_BODY_CHARS:
        pr_body = pr_body[: MAX_BODY_CHARS - len(TRUNCATED)] + TRUNCATED
    return {
        "base": base,
        "head": head,
        "head_usage": usage,
        "pr_title": pr_title[:MAX_TITLE_CHARS],
        "pr_body": pr_body,
        "mode": mode,
        "region": region,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    usage = None
    if args.head_usage:
        if args.head_usage.stat().st_size > MAX_USAGE_BYTES:
            print("usage.json is too large", file=sys.stderr)
            return 2
        usage = json.loads(args.head_usage.read_text(encoding="utf-8"))
    payload = build_payload(
        load_template(args.base),
        load_template(args.head),
        usage,
        args.pr_title,
        args.pr_body_file.read_text(encoding="utf-8") if args.pr_body_file else "",
        args.mode,
        args.region,
    )
    session = session_id(
        os.environ.get("PR_NUMBER", "0"),
        os.environ.get("GITHUB_RUN_ID", "0"),
        os.environ.get("GITHUB_RUN_ATTEMPT", "0"),
        args.mode,
    )
    client = boto3.client(
        "bedrock-agentcore", region_name=AGENTCORE_REGION, config=AGENTCORE_CONFIG
    )
    try:
        body = invoke_review(client, args.runtime_arn, session, payload)
    except RemoteError as error:
        print(f"the review runtime returned an error: {error}", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as error:
        # 文面にはアカウント ID やロール名が入りうるので、種類と HTTP の状態だけを出す
        status = getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        print(
            f"calling the review runtime failed: {type(error).__name__} {status}", file=sys.stderr
        )
        return 1
    args.out.write_text(body, encoding="utf-8")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr_agent.remote")
    parser.add_argument("--runtime-arn", required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--head-usage", type=Path)
    parser.add_argument("--pr-title", default="")
    parser.add_argument("--pr-body-file", type=Path)
    parser.add_argument("--mode", choices=["tool", "none"], default="tool")
    parser.add_argument("--region", default=DEFAULT_TEMPLATE_REGION)
    parser.add_argument("--out", type=Path, required=True)
    return parser


if __name__ == "__main__":
    sys.exit(main())
