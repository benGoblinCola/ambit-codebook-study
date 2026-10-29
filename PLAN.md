# Implementation Plan — AI-Assisted Medical Codebook Generation (HAE slice)

**Candidate exercise:** Applied AI & Software Engineer, Ambit Digital Transformation
**Platform:** AWS (own account), us-east-1
**Data pack:** `github.com/Ambit-RD/ambit-implementation-case-study` — hereditary angioedema (HAE)

> North star: the six architecture principles are the rubric. AI *proposes* candidate
> drugs; reference *data decides* every code; a *person approves*. A small slice done
> well, with honest gaps, over a complete-looking build that hides problems.

This plan reflects what was actually built and verified on AWS, not an aspiration. Where
something is deferred it is called out in §07.

---

## §01 Capability mapping

For each need: the AWS feature chosen, why, and the main alternative rejected.

| Need | Chosen (AWS) | Why | Rejected alternative |
|---|---|---|---|
| Reference file storage + refresh | **S3**, versioned prefixes (one per release) | cheap, durable, a release is naturally one prefix; land-and-version is an upload + a `reference_release` row | Lake Formation governed tables — more governance than a quarterly file drop needs |
| Reference tables + crosswalk joins | **Aurora Serverless v2 PostgreSQL 16.8** via the **RDS Data API** | NDC→J-code as-of-version joins and effective-date logic are plain SQL; Data API means Lambdas reach the DB over IAM-authed HTTPS, so **no VPC, no NAT** | Glue + Athena (clunky for pinned-version point lookups); provisioned Aurora (always-on cost); Data-API-less Aurora (forces Lambda-in-VPC) |
| Retrieval / grounding | **Read-only reference tools** (typed SQL search over `ndc_product`, `icd10cm`) called by the agent | grounding is a cited reference *record*; for ~a dozen HAE drugs this is more precise and far cheaper than a vector store | **Bedrock Knowledge Base + OpenSearch Serverless** — documented as the production grounding upgrade, deliberately left out (cost floor, overkill at this scale) |
| LLM + tool orchestration | **Amazon Bedrock** (Converse API, tool use) for the model; **Step Functions + Lambda** for the pipeline | native tool-use loop; typed step contracts with retry/catch and per-state tracing | LangChain / hand-rolled agent loop — adds custom code where the platform already solves it |
| Validation | Deterministic Python/SQL against the pinned reference tables | precision step; **never** the model | letting the model emit final codes |
| Review workflow | Step Functions `AwaitReview` (`.waitForTaskToken`) + a `set_review` call | draft → approved freezes a version; function over polish | a full web UI (out of scope) |
| Permissions | **Per-component IAM** + **Postgres roles** + a restricted-schema view | least privilege at both the AWS and the database layer | one broad application role |
| Secrets | **Secrets Manager** (admin secret for setup; `ambit/codebook_app` for runtime) | no secrets in source; the Data API resolves the credential server-side | secrets in env files / source |
| Tracing | Structured `TRACE` JSON spans (model/tool/validation/pipeline) | → **CloudWatch / X-Ray** queryable; measured, not eyeballed | print-and-read logs |
| Evaluation | Scenario runner that is a **release gate** (non-zero exit on required failure) | quality measured by an eval set, run against the real tools | manual spot-checks |
| Reproducible setup | **AWS CDK (Python)** + an idempotent loader script | reviewer recreates from the repo; IaC in the same language as the app | ClickOps; **Terraform** (CDK chosen to match the app language) |

---

## §02 Data model

All reference rows carry a **`reference_version`** and **effective/end dates**, so any
codebook is reproducible and crosswalk lookups resolve *as of the analysis period* (BR-5),
never "latest file wins". Tables (see `src/ambit_codebook/schema.sql`):

| Object | Purpose | Versioning |
|---|---|---|
| `reference_release` | registry: one row per (ref_set, release) with effective/end dates + S3 source URI | the version index |
| `crosswalk` | ASP NDC-HCPCS (Part B drugs), NDC11→J-code, package/bill units | `reference_version` = 2026-07 / 2026-10 |
| `ndc_product` | FDA NDC directory: substance, brand, route, dosage form, `product_ndc9` for joins | `reference_version` = 2026-10-19 |
| `hcpcs` | HCPCS Level II: code + descriptions + termination date | 2026-07 / 2026-10 |
| `icd10cm` | ICD-10-CM diagnosis codes (for the confusable-term case; diagnosis is a stretch) | fy2027 |
| `billing_rule` | analyst-owned rules **as data** (BR-1…BR-6) | — |
| `product_admin` | analyst-owned **self-administration** flag per product (answers BR-3) | — |
| `restricted.claims` | synthetic claims **with PHI-like fields**, isolated in its own schema | — |
| `claims_frequency` (view) | aggregate counts per (code_system, code, channel) — **no PHI columns** | — |
| `codebook`, `codebook_version` | output in the standard schema; draft → approved | one row per version |

NDC normalization (hyphenated 5-4-2 → 11 digits; labeler-product → 9 digits) is done both
in Python (`ingest/normalize.py`) and as SQL functions (`normalize_ndc11`,
`normalize_product_ndc9`) so claim NDCs, crosswalk NDCs, and product NDCs join cleanly.

**Standard codebook schema (as built):** `codebook_id, codebook_version, disease_area,
code, code_system, description, group_name, related_code, benefit_channel, billing_unit,
inclusion_rationale, evidence, validation_status, claims_frequency, reference_versions,
review_status, reviewed_by, reviewed_at`.

---

## §03 Pipeline flow (AI vs deterministic)

| Step | AI / deterministic | Notes |
|---|---|---|
| Land + version reference files | deterministic | upload to versioned S3 prefix; `reference_release` row; MANIFEST.sha256 verified |
| Validate the load | deterministic | row counts; a failed check stops publish |
| **Research candidates** | **AI (only AI step)** | Bedrock Converse + tool use: disease → candidate drugs, each **grounded** by a `find_products` call; model proposes drugs, **never** codes |
| Resolve + validate | deterministic | drug→NDCs→J-code via the crosswalk **as of the pinned version**; every code validated; ungrounded/retired codes rejected *with a reason* |
| Apply billing rules | deterministic | `billing_rule` data decides channel + billing unit; no-J-code products flagged pharmacy, **not forced** into a code |
| Claims frequency | deterministic | aggregate counts from `claims_frequency` only |
| Build draft codebook | deterministic | standard schema, `reference_versions` stamped, status = draft |
| Eval gate | deterministic | 11-scenario suite must pass before publish |
| Review + approve | **human** | freezes a version; a revision is a new version |

The local orchestrator `pipeline.py` runs these steps and **mirrors the Step Functions
state machine 1:1** (`infra/stepfunctions/codebook_pipeline.asl.json`).

---

## §04 Security and data handling

**This is the heart of the exercise and is demonstrated, not just described.**

- **PHI isolation.** Synthetic claims (names, DOB, member IDs, ZIP) load into a dedicated
  **`restricted` schema**. The only exposure is the **`claims_frequency` view** — aggregate
  counts per code and channel, with no PHI columns.
- **Least-privilege database role.** The runtime role **`codebook_app`** is granted
  `SELECT` on reference tables and `claims_frequency`, and `INSERT/UPDATE` on the codebook
  tables — and is **denied the `restricted` schema entirely**. Verified live: as
  `codebook_app`, `SELECT ... FROM restricted.claims` returns *"permission denied for
  schema restricted"*, while `SELECT ... FROM claims_frequency` succeeds. The admin secret
  is used only by the one-time loader.
- **Per-component IAM.** Each Lambda (reference tools, research agent, resolve/validate,
  build, review, publish) gets its own minimal role — e.g. the research agent may
  `bedrock:InvokeModel` and read only its scoped secret; the resolve role has no Bedrock
  access. IAM is expressed as code in the CDK stack.
- **Secrets.** In Secrets Manager; the Data API resolves credentials server-side, so secret
  values never enter application code or source control.
- **PHI never in a codebook, prompt, trace, or log.** The agent and resolver query only
  reference tables and the aggregate view; traces record codes/substances, never claim rows.

---

## §05 Evaluation plan

Eleven business scenarios (`evals/eval_suite.py`) run **against the real reference tools
and resolver** and are scored deterministically. The suite is a **release gate**: a
required failure exits non-zero and blocks publish. Current result: **11/11 pass**
(`evals/report.json`).

| # | Scenario | Passing means |
|---|---|---|
| 1 | Many NDCs → one J-code | Takhzyro NDCs all resolve to J0593 |
| 2 | Formulation with no J-code | Orladeyo (oral) → pharmacy, no J-code, not forced |
| 3 | Fabricated/retired code proposed | J1744 for icatibant **rejected with a reason** (not in the crosswalk) |
| 4 | Mapping changed between quarters | Leucovorin **J0640 (07) → J0643 (10)**, resolved per pinned version |
| 5 | Self-admin injectable, both channels | Takhzyro → `both` (BR-3) |
| 6 | Recently approved, no permanent J-code | garadacimab/Andembry → flagged, no J-code |
| 7 | Confusable disease term | D84.1 (HAE) kept distinct from T78.3 (angioneurotic edema) |
| 8 | BR-4 billing-unit math | Takhzyro 150 mg package on a 1 mg code → 150 units |
| 9 | Effective-date boundary | code pinned to the analysis-period crosswalk |
| 10 | BR-6 new rule | Kalbitor (SC, provider-administered) → medical / J1290, not "both" |
| 11 | Empty / no-candidate | nonsense input → rejected, no fabrication |

---

## §06 Operations

- **Quarterly crosswalk refresh.** Land the new quarter under a new S3 prefix → validate the
  load → it becomes a new `reference_version`. **Existing codebooks are unchanged** (pinned
  versions); new runs pick up the new version. Demonstrated by loading both 2026-07 and
  2026-10 and resolving against each.
- **Monitoring.** CloudWatch metrics/logs from the `TRACE` spans: load-validation failures,
  agent latency and token/cost, eval pass rate, rejected-code rate.
- **Cost drivers + controls.** Bedrock model calls (one short agent run per codebook; cache
  the candidate list — implemented as a fallback), Aurora Serverless v2 ACUs (scales to a
  low floor; a bulk load temporarily scales up), S3 (negligible). The deliberately omitted
  Bedrock KB / OpenSearch Serverless would have been the dominant always-on cost.

---

## §07 Scope and risks

**Built and verified on AWS (Part 02):** versioned reference load (crosswalk, HCPCS, NDC,
ICD, billing rules, self-admin seed) into Aurora + S3; the restricted-schema PHI model with
a proven least-privilege role; the live Bedrock research agent (grounded via reference
tools); deterministic resolve/validate/billing including **BR-6**; the draft codebook in the
standard schema + CSV output; the human review step; tracing; and the 11-scenario eval gate
(11/11).

**New billing rule — BR-6 (required "add ≥1"):** *route = SUBCUTANEOUS and
**not** self-administered → medical channel, J-code expected.* BR-3 only covers SC
*self-administered* (→ both); nothing covered SC *provider-administered*. Witness in the
data: **ecallantide / Kalbitor** — subcutaneous, provider-given, carries **J1290**. Covered
by eval scenario 10.

**Self-administration (BR-3's planted question):** resolved as **analyst-owned reference
data** (`product_admin`), governed like `billing_rules.csv` — not inferred from claims or the
model (probing showed claims carry no reliable self-admin signal).

**Deferred (honest gaps):**
- **Bedrock Knowledge Base** — grounding is done via the read-only reference tools instead;
  KB is the documented production upgrade.
- **Diagnosis/procedure codes** — stretch; ICD-10-CM is loaded and used for the confusable-
  term eval, but the drug slice is the focus.
- **In-machine human approval** — the deployed Step Functions pipeline (Research →
  ResolveAndValidate → BuildDraft → EvalGate → PublishVersion, each a per-component
  scoped-IAM Lambda) stops at an eval-gated draft; approval is a separate deliberate step
  (review dashboard / `approve.py`) rather than an in-machine `.waitForTaskToken` pause. The
  fuller ASL (`infra/stepfunctions/codebook_pipeline.asl.json`) models the task-token version.
- **Polished production UI, CI/CD** (described, not built), multi-team scale. A simple
  Streamlit review dashboard **is** built (R08); a production-grade UI is out of scope.

**Risks:** crosswalk effective-date edge cases; incomplete billing rules → unmatched products
(flagged for a reviewer, never guessed); the model confusing HAE with a similar condition
(guarded in the agent prompt + eval 7); own-account reproducibility (mitigated by CDK + the
loader script).
