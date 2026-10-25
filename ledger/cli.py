"""コマンドの入口。

- `ledger size <options.json>`: 設計段階の試算（docs/spec.md §8）
- `ledger compare <base.json> <head.json>`: PR コメントの確定値の欄（docs/spec.md §7）
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ledger.compare import compare_templates, render_comment, render_usage_changes
from ledger.estimator import Estimate, EstimateError
from ledger.malformed import MALFORMED_INPUT_ERRORS
from ledger.plan_file import PlanError, parse_plan
from ledger.price_list import estimate
from ledger.report import render_markdown
from ledger.sizing import evaluate
from ledger.transform import UnsupportedRegionError, UsageRow
from ledger.usage import Usage, UsageError, load_usage

PRICING_REGION = "us-east-1"
DEFAULT_TEMPLATE_REGION = "ap-northeast-1"
# ① の試算（options.json）の usageType は us-east-1 のもの
DEFAULT_SIZE_REGION = "us-east-1"
# Price List API は呼び出しの速さの上限が低いので、再試行を付ける（spec §15.6）
PRICING_CONFIG = Config(retries={"mode": "standard", "max_attempts": 5})
EXIT_ESTIMATE_FAILED = 1
EXIT_INVALID_INPUT = 2
ESTIMATE_ERRORS = (EstimateError, ClientError, BotoCoreError)

# テンプレートは PR のコードが出力するので、大きさに上限を付ける（指摘7）
MAX_TEMPLATE_BYTES = 1_000_000


class TemplateTooLargeError(ValueError):
    """テンプレートが大きすぎる。"""


def load_template(path: Path) -> Any:
    """大きさを確かめてからテンプレート（JSON）を読む。"""
    size = path.stat().st_size
    if size > MAX_TEMPLATE_BYTES:
        raise TemplateTooLargeError(f"{path} is too large ({size} bytes)")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None, *, client: Any = None) -> int:
    """引数を読んで実行し、終了コードを返す。"""
    args = _parser().parse_args(argv)
    if args.command == "size":
        return _size(args, client)
    return _compare(args, client)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    size = commands.add_parser("size", help="設計段階の試算（docs/spec.md §8）")
    size.add_argument("options", type=Path, help="skill が書いた options.json")
    size.add_argument("--region", default=DEFAULT_SIZE_REGION, help="usageType のリージョン")
    compare = commands.add_parser("compare", help="PR コメントの確定値の欄（docs/spec.md §7）")
    compare.add_argument("base", type=Path, help="変更前の CloudFormation テンプレート（JSON）")
    compare.add_argument("head", type=Path, help="変更後の CloudFormation テンプレート（JSON）")
    compare.add_argument(
        "--region", default=DEFAULT_TEMPLATE_REGION, help="テンプレートのリージョン"
    )
    for side in ("base", "head"):
        compare.add_argument(
            f"--{side}-usage", type=Path, help=f"{side} の使用量のファイル（spec §14.1）"
        )
    return parser


def pricing_client() -> Any:
    """Price List API のクライアント（エンドポイントは us-east-1）。"""
    return boto3.client("pricing", region_name=PRICING_REGION, config=PRICING_CONFIG)


def _estimator(args: argparse.Namespace, client: Any) -> Callable[[list[UsageRow]], Estimate]:
    pricing = client or pricing_client()

    def estimate_rows(rows: list[UsageRow]) -> Estimate:
        return estimate(pricing, rows, args.region)

    return estimate_rows


def _size(args: argparse.Namespace, client: Any) -> int:
    try:
        plan = parse_plan(json.loads(args.options.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, PlanError) as error:
        print(f"入力を読めませんでした: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        evaluation = evaluate(plan, _estimator(args, client))
    except ESTIMATE_ERRORS as error:
        print(f"## 試算\n\n見積もりに失敗しました: {error}")
        return EXIT_ESTIMATE_FAILED
    print(render_markdown(plan, evaluation))
    return 0


def _compare(args: argparse.Namespace, client: Any) -> int:
    try:
        base = load_template(args.base)
        head = load_template(args.head)
    except (OSError, json.JSONDecodeError, TemplateTooLargeError) as error:
        print(f"Could not read the templates: {error!r}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        base_usage = _usage(args.base_usage)
        head_usage = _usage(args.head_usage)
    except (OSError, UsageError) as error:
        print(f"Could not read the usage file: {error!r}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        comparison = compare_templates(
            base,
            head,
            args.region,
            _estimator(args, client),
            base_usage=base_usage,
            head_usage=head_usage,
        )
    except UnsupportedRegionError as error:
        print(f"Unsupported region: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except (*ESTIMATE_ERRORS, *MALFORMED_INPUT_ERRORS) as error:
        # 黙って何も出さないと「差がない」と区別できないので、コメントに書く（spec §7.4）。
        # エラーの文面にはアカウント ID やロール名が入りうるので、コメントには出さず標準エラーへ。
        # 文面に PR 由来の改行が入るとワークフローのコマンドになりうるので repr で出す（指摘4）
        print("### Cost estimate\n\nThe cost estimate failed. See the job log for details.")
        print(f"cost estimate failed: {error!r}", file=sys.stderr)
        return EXIT_ESTIMATE_FAILED
    print(render_comment(comparison))
    changes = render_usage_changes(base_usage, head_usage)
    if changes:
        print(changes)
    return 0


def _usage(path: Path | None) -> Usage:
    """使用量のファイル。指定が無ければ空（テンプレートだけで量が決まるものだけを見積もる）。"""
    return load_usage(path) if path else {}


if __name__ == "__main__":
    sys.exit(main())
