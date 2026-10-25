"""CloudFormation テンプレートを、見積もりの使用量の行へ変換する。"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, TypeGuard

from ledger.usage import Factor, Usage

HOURS_PER_MONTH = 730
REGION_PREFIXES = {"ap-northeast-1": "APN1", "us-east-1": "USE1"}
SECONDS_PER_HOUR = Decimal(3600)
# 課金の直接ない型は、対象外の一覧にも出さずに飛ばす（spec §14.2）
IGNORED_TYPES = frozenset(
    {
        "AWS::CDK::Metadata",
        "AWS::IAM::Role",
        "AWS::IAM::Policy",
        "AWS::Lambda::Permission",
        "AWS::Events::Rule",
        "AWS::Glue::Trigger",
        "AWS::Lambda::LayerVersion",
        "AWS::S3::BucketPolicy",
    }
)


@dataclass(frozen=True)
class UsageRow:
    """見積もりの1行。量に Price List API の単価を掛けて月額にする（spec §15）。"""

    key: str
    service_code: str
    usage_type: str
    operation: str
    amount: float
    label: str


FindingKind = Literal["excluded", "note"]


class UnsupportedRegionError(ValueError):
    """usageType の接頭辞を持っていないリージョンが指定された。"""


@dataclass(frozen=True)
class Finding:
    """見積もりに入れなかったもの（excluded）や、行は出したが知らせること（note）。"""

    logical_id: str
    resource_type: str
    kind: FindingKind
    reason: str


@dataclass(frozen=True)
class TransformResult:
    """変換の結果。"""

    rows: list[UsageRow]
    findings: list[Finding]


def transform(template: dict[str, Any], region: str, usage: Usage | None = None) -> TransformResult:
    """テンプレートから使用量の行と、対象外・注記の一覧を作る。

    usage はテンプレートだけでは決まらない実行時間や回数（spec §14.1）。
    """
    if region not in REGION_PREFIXES:
        raise UnsupportedRegionError(f"対応していないリージョンです: {region}")
    prefix = REGION_PREFIXES[region]
    handlers = _REGION_HANDLERS[region]
    usage = usage or {}
    items: list[UsageRow | Finding] = []
    for logical_id, resource in template.get("Resources", {}).items():
        resource_type = resource["Type"]
        if resource_type in IGNORED_TYPES:
            continue
        handler = handlers.get(resource_type)
        if handler is None:
            items.append(Finding(logical_id, resource_type, "excluded", "最小版の対象外"))
            continue
        items.extend(handler(logical_id, resource["Properties"], prefix, usage.get(logical_id)))
        if "Condition" in resource:
            reason = f"条件付きのリソース（{resource['Condition']}）。作られるものとして見積もった"
            items.append(Finding(logical_id, resource_type, "note", reason))
    return TransformResult(
        rows=[i for i in items if isinstance(i, UsageRow)],
        findings=[i for i in items if isinstance(i, Finding)],
    )


def _is_int(value: Any) -> TypeGuard[int]:
    """CloudFormation の値が整数か（組み込み関数の dict や文字列ではないか）。bool は除く。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _intrinsic(logical_id: str, resource_type: str, name: str) -> Finding:
    return Finding(logical_id, resource_type, "excluded", f"{name} が値ではない（組み込み関数）")


def _instance_rows(
    logical_id: str, properties: dict[str, Any], prefix: str
) -> list[UsageRow | Finding]:
    if properties.get("Tenancy", "default") != "default":
        return [
            Finding(
                logical_id, "AWS::EC2::Instance", "excluded", "共有テナンシー以外は最小版の対象外"
            )
        ]
    if "InstanceType" not in properties:
        return [
            Finding(
                logical_id,
                "AWS::EC2::Instance",
                "excluded",
                "InstanceType がない（LaunchTemplate 経由など）",
            )
        ]
    instance_type = properties["InstanceType"]
    if not isinstance(instance_type, str):
        return [_intrinsic(logical_id, "AWS::EC2::Instance", "InstanceType")]
    instance = UsageRow(
        key=logical_id,
        service_code="AmazonEC2",
        usage_type=f"{prefix}-BoxUsage:{instance_type}",
        operation="RunInstances",
        amount=HOURS_PER_MONTH,
        label=f"{logical_id}: EC2 {instance_type}",
    )
    mappings = properties.get("BlockDeviceMappings")
    if not mappings:
        root = Finding(
            logical_id,
            "AWS::EC2::Instance",
            "excluded",
            "ルートボリュームは AMI で決まるため見積もれない（BlockDeviceMappings がない）",
        )
        return [instance, root]
    volumes = [item for m in mappings for item in _block_device_items(logical_id, m, prefix)]
    return [instance, *volumes]


def _block_device_items(
    logical_id: str, mapping: dict[str, Any], prefix: str
) -> list[UsageRow | Finding]:
    key = f"{logical_id}{mapping['DeviceName']}"
    ebs = mapping["Ebs"]
    if "VolumeType" not in ebs:
        return [
            Finding(
                key,
                "AWS::EC2::Instance",
                "excluded",
                "VolumeType が省略されている（既定値は AMI 依存）",
            )
        ]
    row = _ebs_row(key, ebs["VolumeType"], ebs["VolumeSize"], prefix)
    return [row, *_performance_note(key, "AWS::EC2::Instance", ebs)]


def _volume_rows(
    logical_id: str, properties: dict[str, Any], prefix: str
) -> list[UsageRow | Finding]:
    volume_type = properties["VolumeType"]
    size = properties["Size"]
    if not isinstance(volume_type, str):
        return [_intrinsic(logical_id, "AWS::EC2::Volume", "VolumeType")]
    if not isinstance(size, int | float):
        return [_intrinsic(logical_id, "AWS::EC2::Volume", "Size")]
    row = _ebs_row(logical_id, volume_type, size, prefix)
    return [row, *_performance_note(logical_id, "AWS::EC2::Volume", properties)]


def _performance_note(key: str, resource_type: str, ebs: dict[str, Any]) -> list[Finding]:
    if "Iops" not in ebs and "Throughput" not in ebs:
        return []
    return [Finding(key, resource_type, "note", "IOPS・スループットの追加課金は見積もりに含まない")]


def _nat_gateway_rows(
    logical_id: str, properties: dict[str, Any], prefix: str
) -> list[UsageRow | Finding]:
    return [
        UsageRow(
            key=logical_id,
            service_code="AmazonEC2",
            usage_type=f"{prefix}-NatGateway-Hours",
            operation="NatGateway",
            amount=HOURS_PER_MONTH,
            label=f"{logical_id}: NAT Gateway",
        )
    ]


def _vpc_endpoint_rows(
    logical_id: str, properties: dict[str, Any], prefix: str
) -> list[UsageRow | Finding]:
    if properties.get("VpcEndpointType", "Gateway") != "Interface":
        return [
            Finding(
                logical_id,
                "AWS::EC2::VPCEndpoint",
                "excluded",
                "Gateway 型（VpcEndpointType の既定値）は時間の課金がない",
            )
        ]
    az_count = len(properties["SubnetIds"])
    return [
        UsageRow(
            key=logical_id,
            service_code="AmazonVPC",
            usage_type=f"{prefix}-VpcEndpoint-Hours",
            operation="VpcEndpoint",
            amount=HOURS_PER_MONTH * az_count,
            label=f"{logical_id}: Interface VPC エンドポイント × {az_count} AZ",
        )
    ]


def _ebs_row(key: str, volume_type: str, size: float, prefix: str) -> UsageRow:
    return UsageRow(
        key=key,
        service_code="AmazonEC2",
        usage_type=f"{prefix}-EBS:VolumeUsage.{volume_type}",
        operation="",
        amount=size,
        label=f"{key}: EBS {volume_type} {size}GB",
    )


GLUE_JOB = "AWS::Glue::Job"
# Glue の WorkerType ごとの DPU（spec §14.2.1）
GLUE_DPU_PER_WORKER = {"G.1X": Decimal(1), "G.2X": Decimal(2)}
GLUE_OLD_VERSIONS = frozenset({"0.9", "1.0"})
# Glue の G.1X / G.2X で指定できる台数
GLUE_MIN_WORKERS = 2
GLUE_MAX_WORKERS = 299
# Glue 2.0 以降の課金は1回あたり最低1分
GLUE_MIN_SECONDS = Decimal(60)


def _glue_job_rows(
    logical_id: str, properties: dict[str, Any], prefix: str, usage: dict[str, Factor] | None
) -> list[UsageRow | Finding]:
    worker_type = properties.get("WorkerType")
    workers = properties.get("NumberOfWorkers")
    command = properties.get("Command")
    if not isinstance(command, dict) or command.get("Name") != "glueetl":
        return [Finding(logical_id, GLUE_JOB, "excluded", "glueetl 以外のジョブは対象外")]
    # Flex は単価が違い、0.9 / 1.0 は最低課金が 10 分なので、最小版では扱わない
    if properties.get("ExecutionClass", "STANDARD") != "STANDARD":
        return [Finding(logical_id, GLUE_JOB, "excluded", "Flex の実行クラスは対象外")]
    # GlueVersion が無いと古い既定の版になりうるので、明示されたものだけを扱う
    if properties.get("GlueVersion") in GLUE_OLD_VERSIONS | {None}:
        return [Finding(logical_id, GLUE_JOB, "excluded", "GlueVersion がない、または 0.9 / 1.0")]
    if worker_type not in GLUE_DPU_PER_WORKER:
        return [Finding(logical_id, GLUE_JOB, "excluded", "WorkerType が G.1X / G.2X ではない")]
    # テンプレートは PR の作者が書けるので、負の数や巨大な値で費用を偽れないよう範囲を見る
    if not _is_int(workers) or not GLUE_MIN_WORKERS <= workers <= GLUE_MAX_WORKERS:
        reason = f"NumberOfWorkers が {GLUE_MIN_WORKERS}〜{GLUE_MAX_WORKERS} の整数ではない"
        return [Finding(logical_id, GLUE_JOB, "excluded", reason)]
    missing = _missing(usage, ("runs_per_month", "seconds_per_run"))
    if missing:
        return [missing.finding(logical_id, GLUE_JOB)]
    runs = _usage_value(usage, "runs_per_month")
    seconds = max(_usage_value(usage, "seconds_per_run"), GLUE_MIN_SECONDS)
    dpu_hours = runs * seconds / SECONDS_PER_HOUR * workers * GLUE_DPU_PER_WORKER[worker_type]
    return [
        UsageRow(
            key=logical_id,
            service_code="AWSGlue",
            usage_type=f"{prefix}-ETL-DPU-Hour",
            operation="Jobrun",
            amount=float(dpu_hours),
            label=f"{logical_id}: Glue {worker_type} x {workers}",
        )
    ]


LAMBDA_FUNCTION = "AWS::Lambda::Function"
LAMBDA_DEFAULT_MEMORY_MB = 128
# Lambda で設定できるメモリと /tmp の範囲（MB）
LAMBDA_MIN_MEMORY_MB = 128
LAMBDA_MAX_MB = 10_240
# /tmp は 512MB まで無料。超えた分だけ GB 秒で課金される
LAMBDA_FREE_STORAGE_MB = 512
MB_PER_GB = Decimal(1024)
MS_PER_SECOND = Decimal(1000)


def _lambda_rows(
    logical_id: str, properties: dict[str, Any], prefix: str, usage: dict[str, Factor] | None
) -> list[UsageRow | Finding]:
    memory = properties.get("MemorySize", LAMBDA_DEFAULT_MEMORY_MB)
    ephemeral = properties.get("EphemeralStorage", {})
    storage = ephemeral.get("Size", LAMBDA_FREE_STORAGE_MB) if isinstance(ephemeral, dict) else None
    # Architectures が無ければ x86_64。usageType が違うので最小版では arm64 だけを扱う
    if properties.get("Architectures") != ["arm64"]:
        return [Finding(logical_id, LAMBDA_FUNCTION, "excluded", "arm64 以外は対象外")]
    if not _is_int(memory):
        return [_intrinsic(logical_id, LAMBDA_FUNCTION, "MemorySize")]
    if not _is_int(storage):
        return [_intrinsic(logical_id, LAMBDA_FUNCTION, "EphemeralStorage.Size")]
    if not LAMBDA_MIN_MEMORY_MB <= memory <= LAMBDA_MAX_MB:
        return [Finding(logical_id, LAMBDA_FUNCTION, "excluded", "MemorySize が範囲外")]
    if not LAMBDA_FREE_STORAGE_MB <= storage <= LAMBDA_MAX_MB:
        return [Finding(logical_id, LAMBDA_FUNCTION, "excluded", "EphemeralStorage.Size が範囲外")]
    missing = _missing(usage, ("invocations_per_month", "duration_ms_by_memory"))
    if missing:
        return [missing.finding(logical_id, LAMBDA_FUNCTION)]
    invocations = _usage_value(usage, "invocations_per_month")
    durations = (usage or {})["duration_ms_by_memory"].values or {}
    if memory not in durations:
        reason = f"MemorySize {memory} の所要時間の実測が usage.json にない"
        return [Finding(logical_id, LAMBDA_FUNCTION, "excluded", reason)]
    seconds = invocations * durations[memory] / MS_PER_SECOND
    rows = [
        _lambda_row(
            logical_id,
            "compute",
            prefix,
            "Lambda-GB-Second-ARM",
            seconds * memory / MB_PER_GB,
            memory,
        ),
        _lambda_row(logical_id, "requests", prefix, "Request-ARM", invocations, memory),
    ]
    if storage > LAMBDA_FREE_STORAGE_MB:
        extra_gb = (storage - LAMBDA_FREE_STORAGE_MB) / MB_PER_GB
        rows.append(
            _lambda_row(
                logical_id,
                "storage",
                prefix,
                "Lambda-Storage-GB-Second-ARM",
                seconds * extra_gb,
                memory,
            )
        )
    return list(rows)


def _lambda_row(
    logical_id: str, part: str, prefix: str, usage_type: str, amount: Decimal, memory: int
) -> UsageRow:
    # us-east-1 の Lambda の usageType には接頭辞が付かない（ほかのリージョンは "APN1-" など）
    region_part = "" if prefix == "USE1" else f"{prefix}-"
    return UsageRow(
        key=f"{logical_id}.{part}",
        service_code="AWSLambda",
        usage_type=f"{region_part}{usage_type}",
        operation="",
        amount=float(amount),
        label=f"{logical_id}: Lambda arm64 {memory}MB {part}",
    )


STATE_MACHINE = "AWS::StepFunctions::StateMachine"


def _state_machine_rows(
    logical_id: str, properties: dict[str, Any], prefix: str, usage: dict[str, Factor] | None
) -> list[UsageRow | Finding]:
    # Express は実行時間とメモリで課金されるので、最小版では Standard だけを扱う
    if properties.get("StateMachineType", "STANDARD") != "STANDARD":
        return [Finding(logical_id, STATE_MACHINE, "excluded", "Express は対象外")]
    missing = _missing(usage, ("executions_per_month", "transitions_per_execution"))
    if missing:
        return [missing.finding(logical_id, STATE_MACHINE)]
    transitions = _usage_value(usage, "executions_per_month") * _usage_value(
        usage, "transitions_per_execution"
    )
    return [
        UsageRow(
            key=logical_id,
            service_code="AmazonStates",
            usage_type=f"{prefix}-StateTransition",
            operation="",
            amount=float(transitions),
            label=f"{logical_id}: Step Functions Standard",
        )
    ]


@dataclass(frozen=True)
class _Missing:
    """usage.json に足りない項目。"""

    names: list[str]

    def __bool__(self) -> bool:
        return bool(self.names)

    def finding(self, logical_id: str, resource_type: str) -> Finding:
        reason = f"usage.json に {', '.join(self.names)} がない"
        return Finding(logical_id, resource_type, "excluded", reason)


def _missing(usage: dict[str, Factor] | None, names: tuple[str, ...]) -> _Missing:
    return _Missing([n for n in names if (usage or {}).get(n) is None])


def _usage_value(usage: dict[str, Factor] | None, name: str) -> Decimal:
    """値の1項目。呼ぶ前に _missing で有無を確かめる。メモリごとの実測なら 0 を返さず例外。"""
    value = (usage or {})[name].value
    if value is None:
        raise ValueError(f"{name} has values by memory, not a single value")
    return value


_Handler = Callable[[str, dict[str, Any], str, dict[str, Factor] | None], list[UsageRow | Finding]]
_TemplateOnlyHandler = Callable[[str, dict[str, Any], str], list[UsageRow | Finding]]


def _template_only(handler: _TemplateOnlyHandler) -> _Handler:
    """使用量のファイルを使わない（テンプレートだけで量が決まる）変換。"""
    return lambda logical_id, properties, prefix, _usage: handler(logical_id, properties, prefix)


_EC2_HANDLERS: dict[str, _Handler] = {
    "AWS::EC2::Instance": _template_only(_instance_rows),
    "AWS::EC2::Volume": _template_only(_volume_rows),
    "AWS::EC2::NatGateway": _template_only(_nat_gateway_rows),
    "AWS::EC2::VPCEndpoint": _template_only(_vpc_endpoint_rows),
}
_SERVERLESS_HANDLERS: dict[str, _Handler] = {
    "AWS::Glue::Job": _glue_job_rows,
    LAMBDA_FUNCTION: _lambda_rows,
    STATE_MACHINE: _state_machine_rows,
}
# us-east-1 で扱うのはサーバーレスだけ（EC2 系の usageType は確かめていない。spec §14.2）
_REGION_HANDLERS: dict[str, dict[str, _Handler]] = {
    "ap-northeast-1": _EC2_HANDLERS,
    "us-east-1": _SERVERLESS_HANDLERS,
}
