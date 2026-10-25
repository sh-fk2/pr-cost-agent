"""要件レビューのエージェントの AgentCore Runtime、Guardrail、GitHub Actions から使うロール。"""

import os
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Duration, Stack, Tags
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

REGION = "us-east-1"
PROJECT_TAG = ("Project", "pr-cost-agent")
WORKLOAD_TAG = ("Workload", "pr-cost-agent")

GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
# GitHub の sub は所有者とリポジトリの数値 ID を含む形式（use_immutable_subject）。
# AWS を使うジョブは Environment "cost-review" で動くので、sub は environment の形になる。
# 所有者の ID を固定し、リポジトリの ID はワイルドカードにする
GITHUB_OWNER = "sh-fk2@199446538"
ENVIRONMENT = "cost-review"
GITHUB_SUBJECTS = [
    f"repo:{GITHUB_OWNER}/pr-cost-agent@*:environment:{ENVIRONMENT}",
]
# ロールを引き受けられるのは、main にある再利用ワークフローから来たときだけ。
# PR でワークフローを書き換えても、このファイルの main 以外からはロールに入れない
REVIEW_WORKFLOWS = [
    f"sh-fk2/{repo}/.github/workflows/cost-review-aws.yml@refs/heads/main"
    for repo in ("pr-cost-agent",)
]
# Amazon Nova 2 Lite。推論プロファイル経由でだけ呼べる
INFERENCE_PROFILE_ID = "us.amazon.nova-2-lite-v1:0"
FOUNDATION_MODEL_ID = "amazon.nova-2-lite-v1:0"
SESSION_DURATION = Duration.hours(1)
IDLE_SESSION_SECONDS = 60
RUNTIME_NAME = "pr_cost_agent_reviewer"
BUILD_DIR = Path(__file__).resolve().parents[2] / ".build" / "agentcore"


def _build_dir() -> Path:
    if not BUILD_DIR.exists():
        raise FileNotFoundError("先に infra/ci_access/build_agentcore.sh で zip の中身を作る")
    return BUILD_DIR


def _build_commit() -> str:
    """build スクリプトが zip に書いた、要件のファイルのコミット。"""
    return (_build_dir() / "BUILD_COMMIT").read_text(encoding="utf-8").strip()


def _execution_role(scope: Construct, code: s3_assets.Asset) -> iam.Role:
    """ランタイムの実行ロール。ログ・メトリクス・この zip の読み取りだけ。"""
    stack = Stack.of(scope)
    runtime_arn = f"arn:aws:bedrock-agentcore:{REGION}:{stack.account}:runtime/{RUNTIME_NAME}*"
    role = iam.Role(
        scope,
        "ReviewRuntimeRole",
        assumed_by=iam.ServicePrincipal(
            "bedrock-agentcore.amazonaws.com",
            conditions={
                "StringEquals": {"aws:SourceAccount": stack.account},
                "ArnLike": {"aws:SourceArn": runtime_arn},
            },
        ),
        description="pr-cost-agent review runtime",
    )
    log_group = f"arn:aws:logs:{REGION}:{stack.account}:log-group:/aws/bedrock-agentcore/runtimes/*"
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"], resources=[log_group]
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"{log_group}:log-stream:*"],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[f"arn:aws:s3:::{code.s3_bucket_name}/{code.s3_object_key}"],
        )
    )
    return role


def _agent_statements(profile_arn: str, guardrail_arn: str) -> list[iam.PolicyStatement]:
    """エージェントが使う権限: Nova 2 Lite（推論プロファイル経由）、Guardrail、Price List API。"""
    invoke = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    return [
        iam.PolicyStatement(actions=invoke, resources=[profile_arn]),
        # 推論プロファイルは複数のリージョンへ振り分けるので、基盤モデルはリージョンを限らない。
        # ただしこのプロファイルを通した呼び出しだけに絞る
        iam.PolicyStatement(
            actions=invoke,
            resources=[f"arn:aws:bedrock:*::foundation-model/{FOUNDATION_MODEL_ID}"],
            conditions={"StringEquals": {"bedrock:InferenceProfileArn": profile_arn}},
        ),
        iam.PolicyStatement(actions=["bedrock:ApplyGuardrail"], resources=[guardrail_arn]),
        # 見積もりは Price List API の単価で出す（spec §15）。GetProducts はリソースの型を持たない
        iam.PolicyStatement(actions=["pricing:GetProducts"], resources=["*"]),
    ]


class CiAccessStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        guardrail = bedrock.CfnGuardrail(
            self,
            "PromptAttackGuardrail",
            name="pr-cost-agent-prompt-attack",
            description="PR の文章やテンプレートに仕込まれた指示を、エージェントに渡す前に止める",
            blocked_input_messaging="入力に、エージェントへの指示とみられる文章がありました。",
            blocked_outputs_messaging="出力を止めました。",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    # prompt attack は入力にだけ効く。出力側は NONE にする必要がある
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
                    )
                ]
            ),
        )
        version = bedrock.CfnGuardrailVersion(
            self, "PromptAttackGuardrailVersion", guardrail_identifier=guardrail.attr_guardrail_id
        )

        provider_arn = f"arn:aws:iam::{self.account}:oidc-provider/{GITHUB_OIDC_HOST}"
        role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.WebIdentityPrincipal(
                provider_arn,
                conditions={
                    "StringEquals": {
                        f"{GITHUB_OIDC_HOST}:aud": "sts.amazonaws.com",
                        f"{GITHUB_OIDC_HOST}:job_workflow_ref": REVIEW_WORKFLOWS,
                    },
                    "StringLike": {f"{GITHUB_OIDC_HOST}:sub": GITHUB_SUBJECTS},
                },
            ),
            description="pr-cost-agent (cost-review environment, main workflow only)",
            max_session_duration=SESSION_DURATION,
        )

        profile_arn = (
            f"arn:aws:bedrock:{REGION}:{self.account}:inference-profile/{INFERENCE_PROFILE_ID}"
        )
        # エージェント（Strands のループとツール）は AgentCore Runtime で動かす（spec §17）
        # zip は自前の資産にして、ランタイムが読めるのをこの1オブジェクトだけにする。
        # from_code_asset だと、CDK のブートストラップのバケット全体を読める権限が付く（指摘 M-2）
        code = s3_assets.Asset(self, "RuntimeCode", path=str(_build_dir()))
        execution_role = _execution_role(self, code)
        # L2 の Runtime は、渡したロールにも既定の権限（ブートストラップのバケット全体の読み取り、
        # X-Ray など）を足すので、L1 で定義して権限を自分で決める（指摘 M-2）
        runtime = agentcore.CfnRuntime(
            self,
            "ReviewerRuntime",
            agent_runtime_name=RUNTIME_NAME,
            description="EARS requirements reviewer with the estimate_cost tool",
            role_arn=execution_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                code_configuration=agentcore.CfnRuntime.CodeConfigurationProperty(
                    code=agentcore.CfnRuntime.CodeProperty(
                        s3=agentcore.CfnRuntime.S3LocationProperty(
                            bucket=code.s3_bucket_name, prefix=code.s3_object_key
                        )
                    ),
                    entry_point=["main.py"],
                    runtime="PYTHON_3_12",
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            # PR の文面をセッションに長く残さない（指摘 L-4）
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=IDLE_SESSION_SECONDS
            ),
            environment_variables={
                "BEDROCK_MODEL_ID": INFERENCE_PROFILE_ID,
                "GUARDRAIL_ID": guardrail.attr_guardrail_id,
                "GUARDRAIL_VERSION": version.attr_version,
                # zip に同梱した要件の版（指摘 M-3）
                "REQUIREMENTS_COMMIT": _build_commit(),
            },
        )
        # ロールの権限ができてからランタイムを作る（作成時に zip を読むため）
        runtime.node.add_dependency(execution_role)
        for statement in _agent_statements(profile_arn, guardrail.attr_guardrail_arn):
            execution_role.add_to_policy(statement)

        # GitHub Actions のロールが持つのは、ランタイムの呼び出しと、確定値（ledger compare）の
        # ための Price List API だけ。Bedrock の権限はランタイム側にだけ置く（spec §17.4）
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[runtime.attr_agent_runtime_arn, f"{runtime.attr_agent_runtime_arn}/*"],
            )
        )
        role.add_to_policy(iam.PolicyStatement(actions=["pricing:GetProducts"], resources=["*"]))

        cdk.CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn)
        cdk.CfnOutput(self, "RoleArn", value=role.role_arn)
        cdk.CfnOutput(self, "GuardrailId", value=guardrail.attr_guardrail_id)
        cdk.CfnOutput(self, "GuardrailVersion", value=version.attr_version)
        cdk.CfnOutput(self, "InferenceProfileId", value=INFERENCE_PROFILE_ID)


app = cdk.App()
stack = CiAccessStack(
    app,
    "LedgerPrCostAgentAccess",
    env=cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region=REGION),
)
for key, value in (PROJECT_TAG, WORKLOAD_TAG):
    Tags.of(stack).add(key, value)
app.synth()
