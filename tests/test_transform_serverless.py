from decimal import Decimal

import pytest

from ledger.safe import safe_logical_id
from ledger.transform import transform
from ledger.usage import Factor, Usage

REGION = "us-east-1"


def _value(value: str, basis: str = "measured") -> Factor:
    return Factor(value=Decimal(value), values=None, basis=basis)  # type: ignore[arg-type]


def _glue_job(worker_type: str = "G.1X", workers: int = 2) -> dict[str, object]:
    return {
        "Type": "AWS::Glue::Job",
        "Properties": {
            "Command": {"Name": "glueetl", "PythonVersion": "3"},
            "GlueVersion": "5.0",
            "WorkerType": worker_type,
            "NumberOfWorkers": workers,
        },
    }


GLUE_USAGE: Usage = {
    "AggregateWeather": {
        "runs_per_month": _value("30", "requirement"),
        "seconds_per_run": _value("126"),
    }
}


def test_transform_glue_job_returns_dpu_hours_from_usage() -> None:
    # Arrange
    template = {"Resources": {"AggregateWeather": _glue_job()}}

    # Act
    result = transform(template, REGION, GLUE_USAGE)

    # Assert: 30 回 × 126 秒 ÷ 3600 × 2 台 × 1 DPU = 2.1 DPU 時間
    assert result.findings == []
    [row] = result.rows
    assert (row.key, row.service_code, row.usage_type, row.operation) == (
        "AggregateWeather",
        "AWSGlue",
        "USE1-ETL-DPU-Hour",
        "Jobrun",
    )
    assert row.amount == 2.1
    assert row.label == "AggregateWeather: Glue G.1X x 2"


def test_transform_glue_job_shorter_than_a_minute_is_billed_as_a_minute() -> None:
    template = {"Resources": {"AggregateWeather": _glue_job(workers=2)}}
    usage: Usage = {
        "AggregateWeather": {"runs_per_month": _value("30"), "seconds_per_run": _value("20")}
    }

    [row] = transform(template, REGION, usage).rows

    # 30 回 × 60 秒 ÷ 3600 × 2 台 = 1 DPU 時間
    assert row.amount == 1.0


@pytest.mark.parametrize(
    "job",
    [
        _glue_job(worker_type="G.4X"),
        {
            **_glue_job(),
            "Properties": {**_glue_job()["Properties"], "Command": {"Name": "pythonshell"}},
        },  # type: ignore[dict-item]
    ],
)
def test_transform_glue_job_unsupported_worker_or_command_is_excluded(
    job: dict[str, object],
) -> None:
    template = {"Resources": {"AggregateWeather": job}}

    result = transform(template, REGION, GLUE_USAGE)

    assert result.rows == []
    [finding] = result.findings
    assert (finding.logical_id, finding.kind) == ("AggregateWeather", "excluded")


@pytest.mark.parametrize(
    "usage",
    [None, {}, {"AggregateWeather": {"runs_per_month": _value("30")}}],
)
def test_transform_glue_job_without_usage_is_excluded(usage: Usage | None) -> None:
    template = {"Resources": {"AggregateWeather": _glue_job()}}

    result = transform(template, REGION, usage)

    assert result.rows == []
    [finding] = result.findings
    assert (finding.logical_id, finding.kind) == ("AggregateWeather", "excluded")
    assert "seconds_per_run" in finding.reason


def _values(values: dict[int, str]) -> Factor:
    return Factor(value=None, values={k: Decimal(v) for k, v in values.items()}, basis="measured")


def _lambda(
    memory: int = 1536, storage: int | None = 2048, arch: list[str] | None = None
) -> dict[str, object]:
    properties: dict[str, object] = {
        "Architectures": arch if arch is not None else ["arm64"],
        "MemorySize": memory,
        "Runtime": "python3.12",
    }
    if storage is not None:
        properties["EphemeralStorage"] = {"Size": storage}
    return {"Type": "AWS::Lambda::Function", "Properties": properties}


LAMBDA_USAGE: Usage = {
    "Aggregate": {
        "invocations_per_month": _value("30", "requirement"),
        "duration_ms_by_memory": _values({1024: "33600", 1536: "25600"}),
    }
}


def test_transform_lambda_returns_compute_requests_and_storage_rows() -> None:
    template = {"Resources": {"Aggregate": _lambda(memory=1536, storage=2048)}}

    result = transform(template, REGION, LAMBDA_USAGE)

    assert result.findings == []
    rows = {row.key: row for row in result.rows}
    assert list(rows) == ["Aggregate.compute", "Aggregate.requests", "Aggregate.storage"]
    # 30 回 × 25.6 秒 × 1.5 GB = 1152 GB 秒
    compute = rows["Aggregate.compute"]
    assert (compute.service_code, compute.usage_type, compute.operation) == (
        "AWSLambda",
        "Lambda-GB-Second-ARM",
        "",
    )
    assert compute.amount == 1152.0
    assert compute.label == "Aggregate: Lambda arm64 1536MB compute"
    assert (rows["Aggregate.requests"].usage_type, rows["Aggregate.requests"].amount) == (
        "Request-ARM",
        30.0,
    )
    # 30 回 × 25.6 秒 × (2048 − 512) / 1024 GB = 1152 GB 秒
    storage = rows["Aggregate.storage"]
    assert (storage.usage_type, storage.amount) == ("Lambda-Storage-GB-Second-ARM", 1152.0)


@pytest.mark.parametrize("storage", [None, 512])
def test_transform_lambda_with_free_storage_has_no_storage_row(storage: int | None) -> None:
    template = {"Resources": {"Aggregate": _lambda(memory=1024, storage=storage)}}

    result = transform(template, REGION, LAMBDA_USAGE)

    assert [row.key for row in result.rows] == ["Aggregate.compute", "Aggregate.requests"]


@pytest.mark.parametrize(
    ("function", "reason"),
    [
        (_lambda(arch=[]), "arm64"),
        (_lambda(arch=["x86_64"]), "arm64"),
        (_lambda(memory=2048), "2048"),
    ],
)
def test_transform_lambda_not_arm64_or_without_measurement_is_excluded(
    function: dict[str, object], reason: str
) -> None:
    template = {"Resources": {"Aggregate": function}}

    result = transform(template, REGION, LAMBDA_USAGE)

    assert result.rows == []
    [finding] = result.findings
    assert (finding.logical_id, finding.kind) == ("Aggregate", "excluded")
    assert reason in finding.reason


def test_transform_lambda_without_usage_is_excluded() -> None:
    template = {"Resources": {"Aggregate": _lambda()}}

    result = transform(template, REGION, None)

    assert result.rows == []
    assert "invocations_per_month" in result.findings[0].reason


SFN_USAGE: Usage = {
    "WeatherDaily": {
        "executions_per_month": _value("30", "requirement"),
        "transitions_per_execution": _value("1"),
    }
}


@pytest.mark.parametrize("properties", [{}, {"StateMachineType": "STANDARD"}])
def test_transform_standard_state_machine_returns_state_transitions(
    properties: dict[str, object],
) -> None:
    template = {
        "Resources": {
            "WeatherDaily": {"Type": "AWS::StepFunctions::StateMachine", "Properties": properties}
        }
    }

    result = transform(template, REGION, SFN_USAGE)

    assert result.findings == []
    [row] = result.rows
    assert (row.key, row.service_code, row.usage_type, row.operation, row.amount) == (
        "WeatherDaily",
        "AmazonStates",
        "USE1-StateTransition",
        "",
        30.0,
    )
    assert row.label == "WeatherDaily: Step Functions Standard"


def test_transform_express_state_machine_is_excluded() -> None:
    template = {
        "Resources": {
            "WeatherDaily": {
                "Type": "AWS::StepFunctions::StateMachine",
                "Properties": {"StateMachineType": "EXPRESS"},
            }
        }
    }

    result = transform(template, REGION, SFN_USAGE)

    assert result.rows == []
    assert result.findings[0].kind == "excluded"


def test_transform_types_without_direct_charge_are_skipped_silently() -> None:
    free = [
        "AWS::IAM::Role",
        "AWS::IAM::Policy",
        "AWS::Lambda::Permission",
        "AWS::Events::Rule",
        "AWS::Glue::Trigger",
        "AWS::Lambda::LayerVersion",
        "AWS::S3::BucketPolicy",
    ]
    template = {"Resources": {f"R{i}": {"Type": t, "Properties": {}} for i, t in enumerate(free)}}

    result = transform(template, REGION)

    assert (result.rows, result.findings) == ([], [])


def test_transform_ec2_in_us_east_1_is_excluded() -> None:
    template = {
        "Resources": {
            "Web": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "m7g.large"}}
        }
    }

    result = transform(template, REGION)

    assert result.rows == []
    assert (result.findings[0].logical_id, result.findings[0].kind) == ("Web", "excluded")


def test_safe_logical_id_accepts_lambda_row_keys() -> None:
    assert [safe_logical_id(k) for k in ("Aggregate.compute", "Aggregate.storage", "A.other")] == [
        "Aggregate.compute",
        "Aggregate.storage",
        "[redacted]",
    ]


@pytest.mark.parametrize(
    "properties",
    [
        {"Architectures": ["arm64"], "MemorySize": {"Ref": "Mem"}},
        {
            "Architectures": ["arm64"],
            "MemorySize": 1536,
            "EphemeralStorage": {"Size": {"Ref": "S"}},
        },
        {"Architectures": ["arm64"], "MemorySize": "1536"},
    ],
)
def test_transform_lambda_with_non_integer_sizes_is_excluded(properties: dict[str, object]) -> None:
    template = {
        "Resources": {"Aggregate": {"Type": "AWS::Lambda::Function", "Properties": properties}}
    }

    result = transform(template, REGION, LAMBDA_USAGE)

    assert result.rows == []
    assert result.findings[0].kind == "excluded"


@pytest.mark.parametrize(
    "extra",
    [
        {"ExecutionClass": "FLEX"},
        {"GlueVersion": "1.0"},
        {"GlueVersion": "0.9"},
        {"GlueVersion": None},
    ],
)
def test_transform_glue_flex_or_old_version_is_excluded(extra: dict[str, object]) -> None:
    job = _glue_job()
    properties = {**job["Properties"], **extra}  # type: ignore[dict-item]
    job["Properties"] = {k: v for k, v in properties.items() if v is not None}
    template = {"Resources": {"AggregateWeather": job}}

    result = transform(template, REGION, GLUE_USAGE)

    assert result.rows == []
    assert result.findings[0].kind == "excluded"


@pytest.mark.parametrize("workers", [-5, 1, 300, True, 10**20])
def test_transform_glue_workers_out_of_range_is_excluded(workers: object) -> None:
    template = {"Resources": {"AggregateWeather": _glue_job(workers=workers)}}  # type: ignore[arg-type]

    result = transform(template, REGION, GLUE_USAGE)

    assert result.rows == []
    assert result.findings[0].kind == "excluded"


@pytest.mark.parametrize(
    ("memory", "storage"), [(64, 2048), (10241, 2048), (1536, 256), (1536, 20000), (1536.5, 2048)]
)
def test_transform_lambda_sizes_out_of_range_are_excluded(memory: object, storage: object) -> None:
    function = _lambda(memory=1536, storage=2048)
    function["Properties"] = {  # type: ignore[index]
        **function["Properties"],  # type: ignore[dict-item]
        "MemorySize": memory,
        "EphemeralStorage": {"Size": storage},
    }
    template = {"Resources": {"Aggregate": function}}

    result = transform(template, REGION, LAMBDA_USAGE)

    assert result.rows == []
    assert result.findings[0].kind == "excluded"
