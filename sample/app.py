"""PR のコストを見るエージェントのデモ用 CDK アプリ: GHCN-Daily の毎日の集計。

us-east-1 で毎日動いている Glue のジョブと同じ構成を、見積もり用に置いている。
デプロイはせず、テンプレートだけを作る。実行時間と回数は usage.json に書く。
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_assets as assets
from constructs import Construct

REGION = "us-east-1"
SOURCE_BUCKET = "noaa-ghcn-pds"
SOURCE_KEY = "csv/by_year/2026.csv"
GLUE_VERSION = "5.0"
WORKER_TYPE = "G.1X"
NUMBER_OF_WORKERS = 2
TIMEOUT_MINUTES = 30
# データは毎日 09 時台（UTC）に更新されるので、その後に動かす
SCHEDULE = "cron(0 11 * * ? *)"

HERE = Path(__file__).parent


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
        script = assets.Asset(self, "Script", path=str(HERE / "glue" / "aggregate_weather.py"))
        role = iam.Role(
            self,
            "JobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")
            ],
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{SOURCE_BUCKET}/{SOURCE_KEY}"],
            )
        )
        output.grant_read_write(role)
        script.grant_read(role)

        job = glue.CfnJob(
            self,
            "AggregateWeather",
            role=role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl", python_version="3", script_location=script.s3_object_url
            ),
            glue_version=GLUE_VERSION,
            worker_type=WORKER_TYPE,
            number_of_workers=NUMBER_OF_WORKERS,
            timeout=TIMEOUT_MINUTES,
            execution_property=glue.CfnJob.ExecutionPropertyProperty(max_concurrent_runs=1),
            default_arguments={
                "--SOURCE_PATH": f"s3://{SOURCE_BUCKET}/{SOURCE_KEY}",
                "--OUTPUT_PATH": output.s3_url_for_object("summary/"),
            },
        )
        glue.CfnTrigger(
            self,
            "DailyTrigger",
            type="SCHEDULED",
            schedule=SCHEDULE,
            start_on_creation=True,
            actions=[glue.CfnTrigger.ActionProperty(job_name=job.ref)],
        )


app = cdk.App()
WeatherDailyStack(app, "WeatherDaily", env=cdk.Environment(region=REGION))
app.synth()
