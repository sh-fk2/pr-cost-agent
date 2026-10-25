# pr-cost-agent

A requirements reviewer agent for pull requests, with one tool that prices design alternatives. It runs on **Amazon Bedrock AgentCore Runtime** (Strands Agents, Amazon Nova 2 Lite) and is called from GitHub Actions on every pull request.

The reviewer started as the `requirements-reviewer` agent of [ears-trace](https://github.com/sh-fk2/ears-trace). It reviews each pull request that changes the CDK app in `sample/` against requirements written in EARS notation (`docs/requirements/weather-daily.ears.md`), and the workflow posts one comment:

1. **Cost estimate**: the monthly cost before and after the pull request, computed by code from the AWS Price List API. No model touches it.
2. **Requirements review (before: no tools)**: the reviewer as it was, for comparison.
3. **Requirements review (after: one new tool, estimate_cost)**: the reviewer with the tool, plus a table of the alternatives it tried. Cost and memory limits (`docs/requirements/weather-daily.limits.json`) are checked by code.

## The tool: estimate_cost

The agent passes up to three alternatives (for example another Lambda `MemorySize`). For the current design and each alternative, the tool:

- applies the changes to a copy of the CloudFormation template
- turns resources into usage lines, using the measured durations in `sample/usage.json`
- prices them with the AWS Price List API (`pricing:GetProducts`) and multiplies with `Decimal`
- computes the free memory from the measured peak
- compares cost and free memory with the limits file and returns the verdicts

## Layout

| Path | What it is |
|---|---|
| `ledger/` | template → usage lines, usage.json, Price List pricing, comparison, the tool |
| `pr_agent/` | the Strands agent and its review flow (`review.py`), the AgentCore entrypoint (`runtime.py`), the client for CI (`remote.py`), input checks, the number check, the table |
| `sample/` | the demo CDK app (daily weather aggregation) and its `usage.json` |
| `docs/requirements/` | EARS requirements and machine-readable limits (packaged into the runtime) |
| `infra/ci_access/` | the AgentCore Runtime, its execution role, the Bedrock guardrail, and the IAM OIDC role for GitHub Actions; `build_agentcore.sh` builds the arm64 package |
| `.github/workflows/` | `cost-review.yml` (synth without AWS access) and `cost-review-aws.yml` (the reusable review job, pinned to `main`) |

## Deploy and run

```bash
uv sync --group agent --group cdk
uv run pytest
infra/ci_access/build_agentcore.sh          # from a clean commit on origin/main
(cd infra/ci_access && CDK_DEFAULT_ACCOUNT=<account> npx aws-cdk deploy)
```

Repository variables for the workflow: `AWS_ROLE_ARN`, `AGENT_RUNTIME_ARN`, `AGENT_MODES` (`none tool`), and an environment `cost-review` with a required reviewer.

To try the agent without AgentCore, run the same review locally:

```bash
export BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0 GUARDRAIL_ID=<id> GUARDRAIL_VERSION=<version>
uv run python -m pr_agent --base base.template.json --head head.template.json \
  --head-usage sample/usage.json --region us-east-1 --mode tool \
  --requirements docs/requirements/weather-daily.ears.md \
  --limits docs/requirements/weather-daily.limits.json --out review.md
```

## Notes

- Prices are on-demand list prices. The free tier, Savings Plans and credits are not included.
- Code comments are in Japanese.
