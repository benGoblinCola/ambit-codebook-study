# Ambit Case Study — AI-Assisted Medical Codebook Generator (HAE)

Given a disease area, an LLM proposes candidate drugs grounded in reference data; then
deterministic logic resolves drugs → NDCs → J-codes via the CMS ASP crosswalk (as of a
pinned version), validates every code, applies analyst-owned billing rules, tags the claims
channel, flags products with no J-code, and produces a reviewer-approvable codebook.

**AI proposes, reference data decides, a person approves.** Disease slice: hereditary
angioedema (HAE).

## Architecture

Runs on AWS: **S3** (versioned reference files) → **Aurora Serverless v2 PostgreSQL +
RDS Data API** (reference tables, no VPC/NAT) → **Bedrock** research agent + deterministic
resolve/validate → **Step Functions** orchestration → **CloudWatch/X-Ray** tracing, all
provisioned by **AWS CDK (Python)**. Security centers on per-component scoped IAM, a
`restricted` schema for PHI, and a least-privilege DB role that can read aggregates but not
PHI. Full detail: [`PLAN.md`](PLAN.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Prerequisites

- AWS credentials for your own account (the setup uses S3, Aurora Serverless v2, Secrets
  Manager, Bedrock, IAM).
- **Amazon Bedrock model access** enabled in your region for
  `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (override with `BEDROCK_MODEL_ID`).
- Node.js + the AWS CDK CLI (`npm install -g aws-cdk`).
- Python 3.12+ with `boto3`, `aws-cdk-lib`, `constructs` (`pip install -r requirements-cdk.txt boto3`).

## Setup and run

0. Clone the public data pack into `data-pack/` (CMS/FDA reference files + synthetic
   claims; the loader auto-extracts the NDC zip on first use):

```bash
git clone https://github.com/Ambit-RD/ambit-implementation-case-study.git data-pack
```

1. Provision the stack (VPC, Aurora Serverless v2 + Data API, versioned S3, secret):

```bash
cdk deploy AmbitCodebook --require-approval never --outputs-file cdk-outputs.json
```

2. Load reference data (verifies MANIFEST, lands raw files in versioned S3 prefixes, loads
   the reference tables, loads PHI claims into the `restricted` schema, and creates the
   least-privilege `codebook_app` role):

```bash
PYTHONPATH=src python -m ambit_codebook.load
```

3. Generate a draft codebook end-to-end (Bedrock agent → resolve/validate → Aurora + CSV).
   Writes `out/<codebook_id>_v<n>.csv`:

```bash
PYTHONPATH=src python -m ambit_codebook.pipeline "hereditary angioedema"
```

4. Run the evaluation suite (release gate; exits non-zero on a required failure):

```bash
PYTHONPATH=src python evals/eval_suite.py
```

5. Run the unit tests for the deterministic logic:

```bash
PYTHONPATH=src python -m pytest -q
```

> The reference data pack lives in `data-pack/` (public CMS/FDA files; claims and CPT are
> synthetic — treated as if real). `src/` is the application; `infra/` the CDK stack and the
> Step Functions ASL; `seed/` the analyst-owned billing rules and self-admin table.

### Run via the deployed Step Functions pipeline

`cdk deploy` provisions a state machine — **ResearchCandidates → ResolveAndValidate →
BuildDraft → EvalGate → PublishVersion** — each step its own scoped-IAM Lambda (only
`ResearchCandidates` may call Bedrock; only `PublishVersion` may write S3). Start an
execution and wait for it:

```bash
python scripts/start_pipeline.py "hereditary angioedema"
```

A named reviewer then approves the eval-gated draft, freezing the version (R08 / human
approval), either from the CLI:

```bash
python scripts/approve.py hereditary-angioedema 1 "Dr. Reviewer" approved
```

…or from the **review dashboard** — browse versions, inspect every code with its channel /
billing unit / claims frequency / validation status, and Approve or Reject:

```bash
PYTHONPATH=src streamlit run src/ambit_codebook/review_app.py
```

## Deliverables in this repo

- [`PLAN.md`](PLAN.md) — Part 01 implementation plan (all seven sections).
- `src/` + `infra/` — the working slice and its infrastructure as code.
- [`evals/eval_suite.py`](evals/eval_suite.py) + `evals/report.json` — 11 scenarios, release gate.
- `out/hereditary-angioedema_v1.csv` — a sample draft HAE codebook.
- [`docs/EVIDENCE.md`](docs/EVIDENCE.md) — trace, review, and least-privilege evidence.
- [`docs/AI_TOOLS_NOTE.md`](docs/AI_TOOLS_NOTE.md) — AI tools used.

## Teardown

One command (destroys the stack and deletes the loader-created secret):

```bash
python scripts/teardown.py
```

`cdk destroy` removes the Aurora cluster, S3 bucket (auto-emptied), VPC, Lambdas, Step
Functions, and the generated admin secret; the `codebook_app` secret is created by the
loader at runtime, so `teardown.py` deletes it separately. The CDK bootstrap stack and its
asset bucket are left in place.
