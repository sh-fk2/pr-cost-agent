"""テンプレートやモデルから来た文字列を、外（PR コメントやモデル）に出す前の検査。

許可した形に合わないものは [redacted] にする。用途ごとに許す形を狭くしてある（URL や
Markdown の記号を通さないため）。
"""

import re

REDACTED = "[redacted]"
MAX_LENGTH = 64

# CloudFormation の論理 ID は英数字。EBS の行の key は「論理 ID + デバイス名」、
# Lambda の行の key は「論理 ID + .compute などの部分の名前」になる
LOGICAL_ID = re.compile(r"[A-Za-z0-9]{1,64}(?:/dev/[a-z0-9]{1,16}|\.(?:compute|requests|storage))?")
RESOURCE_TYPE = re.compile(r"AWS::[A-Za-z0-9]{1,32}::[A-Za-z0-9]{1,48}")
# 行の説明（例 "Web: EC2 m7g.large"、"Web/dev/xvda: EBS gp3 30GB"）
LABEL = re.compile(r"[A-Za-z0-9 :._/\-]{1,64}")


def _check(pattern: re.Pattern[str], value: str) -> str:
    return value if pattern.fullmatch(value) and "://" not in value else REDACTED


def safe_logical_id(value: str) -> str:
    """論理 ID（行の key）として出してよければそのまま、そうでなければ伏せ字。"""
    return _check(LOGICAL_ID, value)


def safe_resource_type(value: str) -> str:
    """リソースの型として出してよければそのまま、そうでなければ伏せ字。"""
    return _check(RESOURCE_TYPE, value)


def safe_label(value: str) -> str:
    """行の説明として出してよければそのまま、そうでなければ伏せ字。"""
    return _check(LABEL, value)


# プロパティ名と値（例 "InstanceType"、"m7g.large"、"gp3"）。空白や記号を通さない
VALUE = re.compile(r"[A-Za-z0-9._\-]{1,64}")


def safe_value(value: str) -> str:
    """プロパティ名や値として出してよければそのまま、そうでなければ伏せ字。"""
    return _check(VALUE, value)


# EventBridge と Glue のスケジュール式（例 "cron(0 11 * * ? *)"、"rate(1 day)"）。
# 英字は曜日・月の名前と L / W だけにして、文章を通さない（セキュリティレビュー M-1）
_CRON_FIELD = (
    r"(?:[0-9*?/,#\-]|L|W|MON|TUE|WED|THU|FRI|SAT|SUN"
    r"|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC){1,20}"
)
SCHEDULE = re.compile(
    rf"cron\((?:{_CRON_FIELD} ){{5}}{_CRON_FIELD}\)"
    r"|rate\([0-9]{1,4} (?:minute|minutes|hour|hours|day|days)\)"
)


def safe_schedule(value: str) -> str:
    """スケジュール式として出してよければそのまま、そうでなければ伏せ字。"""
    return _check(SCHEDULE, value)
