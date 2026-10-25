"""`python -m pr_agent`: 要件レビューのエージェントを手元で1回動かし、コメントの欄を書く。

確定値の欄（ledger compare）はワークフローが別に貼る。ここが書くのはエージェントの欄だけ。
CI では、同じ review() を AgentCore Runtime（pr_agent.runtime）で動かし、pr_agent.remote で呼ぶ。
"""

import argparse
import sys
from pathlib import Path

from ledger.cli import DEFAULT_TEMPLATE_REGION, load_template
from ledger.limits import load_limits
from ledger.usage import load_usage
from pr_agent.inputs import requirements_data
from pr_agent.review import review
from pr_agent.types import ReviewError, ReviewInput


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    review_input = ReviewInput(
        base=load_template(args.base),
        head=load_template(args.head),
        mode=args.mode,
        region=args.region,
        head_usage=load_usage(args.head_usage) if args.head_usage else {},
        pr_title=args.pr_title,
        pr_body=args.pr_body_file.read_text(encoding="utf-8") if args.pr_body_file else "",
        # 要件と閾値は main のチェックアウトから読む（spec §16.1、§16.9）
        requirements=requirements_data(args.requirements) if args.requirements else [],
        limits=load_limits(args.limits) if args.limits else {},
    )
    try:
        body = review(review_input)
    except ReviewError as error:
        print(error, file=sys.stderr)
        return 2
    args.out.write_text(body, encoding="utf-8")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr_agent")
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--pr-title", default="")
    parser.add_argument("--pr-body-file", type=Path)
    parser.add_argument("--mode", choices=["tool", "none"], default="tool")
    parser.add_argument("--region", default=DEFAULT_TEMPLATE_REGION)
    # head の使用量の前提（spec §14.1）。案も head の前提で見積もる
    parser.add_argument("--head-usage", type=Path)
    # EARS の要件（spec §16.1）
    parser.add_argument("--requirements", type=Path, required=True)
    # 要件の閾値（spec §16.9）
    parser.add_argument("--limits", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser


if __name__ == "__main__":
    sys.exit(main())
