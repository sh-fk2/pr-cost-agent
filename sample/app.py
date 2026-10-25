"""PR のコストを見るエージェントのデモ用 CDK アプリ: GHCN-Daily の毎日の集計。

us-east-1 で毎日動いている Step Functions と Lambda と同じ構成を、見積もり用に置いている。
デプロイはせず、テンプレートだけを作る。実行時間と回数は usage.json に書く。
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy, Size, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

REGION = "us-east-1"
SOURCE_BUCKET = "noaa-ghcn-pds"
SOURCE_KEY = "csv/by_year/2026.csv"
ELEMENTS = ["TMAX", "TMIN", "PRCP", "SNWD"]
# 1024 / 1536 / 2048 / 3008 MB で実測（2026-10-24）。1024 は最大メモリ 1012MB で余裕がないため 1536
MEMORY_MB = 1536
# 元の CSV（約 1GB）と DuckDB の一時ファイルを /tmp に置く
EPHEMERAL_STORAGE = Size.gibibytes(2)
TIMEOUT = Duration.minutes(15)
STATE_MACHINE_TIMEOUT = Duration.minutes(30)
SCHEDULE = events.Schedule.cron(minute="0", hour="11")

HERE = Path(__file__).parent
# DuckDB のレイヤー（aarch64 の wheel から作る）。見積もりには効かないので、
# 無ければレイヤーなしでテンプレートを作る（CI の synth で大きな wheel を取りに行かないため）
LAYER_DIR = HERE / ".build" / "layer"


class WeatherDailyStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        output = s3.Bucket(
            self,
            "Output",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
        )
        layers = []
        if LAYER_DIR.exists():
            layers.append(
                lambda_.LayerVersion(
                    self,
                    "DuckDb",
                    code=lambda_.Code.from_asset(str(LAYER_DIR)),
                    compatible_architectures=[lambda_.Architecture.ARM_64],
                    compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
                )
            )
        aggregate = lambda_.Function(
            self,
            "Aggregate",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="aggregate.handler",
            code=lambda_.Code.from_asset(str(HERE / "handlers")),
            layers=layers,
            memory_size=MEMORY_MB,
            ephemeral_storage_size=EPHEMERAL_STORAGE,
            timeout=TIMEOUT,
            environment={
                "SOURCE_BUCKET": SOURCE_BUCKET,
                "SOURCE_KEY": SOURCE_KEY,
                "OUTPUT_BUCKET": output.bucket_name,
            },
            log_group=logs.LogGroup(self, "AggregateLogs", retention=logs.RetentionDays.ONE_WEEK),
        )
        # usage.json と突き合わせるため、論理 ID を短く固定する
        aggregate.node.default_child.override_logical_id("Aggregate")  # type: ignore[union-attr]
        aggregate.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{SOURCE_BUCKET}/{SOURCE_KEY}"],
            )
        )
        output.grant_put(aggregate)

        # 元の CSV を1回だけ読み、4つの要素をまとめて集計する
        aggregate_all = tasks.LambdaInvoke(
            self,
            "AggregateAll",
            lambda_function=aggregate,
            payload=sfn.TaskInput.from_object({"elements": ELEMENTS}),
            payload_response_only=True,
        )
        machine = sfn.StateMachine(
            self,
            "WeatherDaily",
            definition_body=sfn.DefinitionBody.from_chainable(aggregate_all),
            timeout=STATE_MACHINE_TIMEOUT,
        )
        machine.node.default_child.override_logical_id("WeatherDaily")  # type: ignore[union-attr]
        events.Rule(
            self, "DailySchedule", schedule=SCHEDULE, targets=[targets.SfnStateMachine(machine)]
        )


app = cdk.App()
WeatherDailyStack(app, "WeatherDaily", env=cdk.Environment(region=REGION))
app.synth()
