# Architecture — AI-Assisted Medical Codebook Generator (HAE slice)

**Platform of record:** AWS. **Demo footprint:** runs end-to-end locally; every local component maps 1:1 to a named AWS service (table below). The one AI step calls Amazon Bedrock for real.

> Why this shape: the exercise is graded on six architecture principles, not on how much cloud is lit up. A one-day, torn-down-after demo is most honest and most reliable when the deterministic core runs locally against the real data pack, the one AI call is a real Bedrock call, and the cloud topology is expressed as deployable IaC + this mapping rather than as expensive always-on infra (Aurora, Bedrock KB / OpenSearch Serverless) stood up for a single walkthrough.

## Six principles → where each is enforced

| # | Principle | Enforced by |
|---|---|---|
| 1 | AI proposes, data decides | Research agent only *proposes* candidate drugs; NDC→J-code is a deterministic SQL/crosswalk lookup (`resolve/`), never generated. Billing channel comes from `billing_rules` data, never a prompt. |
| 2 | Grounded, not remembered | Each proposed candidate must carry evidence = a reference record; `validate/` rejects any code not present in the pinned reference tables. |
| 3 | Versioned reference data | Every reference row carries `reference_version` + `effective_date`/`end_date`; crosswalk resolves *as of the analysis period*, not latest (BR-5). |
| 4 | Human approval | Pipeline produces a `draft` codebook version; `AwaitReview` state blocks publish until a named reviewer approves; approved versions are frozen, revisions are new versions. |
| 5 | Least privilege | Claims (PHI-like) reachable only as aggregate counts via one restricted view; PHI never enters a codebook, prompt, trace, or log. Reference tools run under a read-only identity. |
| 6 | Observable & testable | Every model/tool/validation call is traced (`trace/`, PHI-safe); quality is measured by an eval set that gates publish, not by eye. |

## Component map — local ↔ AWS

| Pipeline concern | Local (this repo) | AWS service | Why this AWS service |
|---|---|---|---|
| Reference file storage + versioning | `data-pack/` + versioned loader | **S3** (versioned prefixes, one release per prefix) | cheap, durable, natural per-release versioning |
| Reference tables + crosswalk joins | **Postgres** (Docker Compose) | **Aurora PostgreSQL** | NDC→J-code as-of-version joins + effective-date logic are plain SQL |
| Retrieval / grounding | local reference tools over Postgres | **Bedrock Knowledge Bases** | native grounding + citations (Principle 2) |
| LLM + orchestration | local state-machine runner + **real Bedrock call** | **Step Functions + Lambda**; **Bedrock** (model) | typed step contracts, retry/catch, per-state trace |
| Typed read-only reference tools | `reference/tools.py` | **Lambda** under read-only IAM role | shared by agent + pipeline + evals; every call traced |
| Deterministic resolve + validate | `resolve/`, `validate/` | **Lambda / SQL** | precision step; never the model |
| Claims as aggregate-only | restricted SQL view | **Aurora view + IAM** | PHI never exposed row-level |
| Review workflow | `review/` (CLI/Streamlit) | **Lambda + minimal UI** | function over polish |
| Tracing | structured JSON (`trace/`) | **CloudWatch + X-Ray + Bedrock traces** | model/tool/validation calls |
| Evaluation / release gate | `evals/runner.py` | **script/job + optional Bedrock evaluations** | eval must pass before publish |
| Reproducible setup | Docker Compose + `make setup` | **IaC (CDK) / bundle** | reviewer recreates from repo |

## Orchestration — Step Functions

Orchestration is a real, deployed component. `cdk deploy` provisions a Step Functions state
machine (`CodebookPipeline`) whose tasks are per-component scoped-IAM Lambdas:

`ResearchCandidates → ResolveAndValidate → BuildDraft → EvalGate → (Choice: eval passed?) →
PublishVersion → DraftReady` / `EvalFailed`.

- Each Task is a Lambda in `src/ambit_codebook/handlers.py`; the business logic is the same
  shared modules the CLI uses, so the state machine and the local run are one program.
- `src/ambit_codebook/pipeline.py` is the local orchestrator that mirrors the same step
  sequence (Research → Resolve → BuildDraft), used for a fast local demo and in the evals.
- X-Ray tracing is enabled on the state machine; each step also emits `TRACE` spans.

Run it with `python scripts/start_pipeline.py "<disease>"`.

### Deployed subset vs. the production ASL
`infra/stepfunctions/codebook_pipeline.asl.json` is the fuller *production* design: it adds
`VerifyManifest → LoadReference → ValidateLoad` at the front and a `.waitForTaskToken`
`AwaitReview` state before publish. In this thin slice, two of those are deliberately out of
the deployed state machine and handled separately (honest gaps):
- **Reference load** is a one-time setup step (`python -m ambit_codebook.load`), not a
  per-generation state. The ASL models it for the quarterly-refresh path.
- **Human approval** is a separate deliberate action (`scripts/approve.py`, which freezes the
  version) rather than an in-machine task-token pause. The pipeline stops at an eval-gated
  `DraftReady`; approval is the human step (Principle 04).
