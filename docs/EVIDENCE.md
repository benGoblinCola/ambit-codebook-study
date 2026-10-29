# Evidence

Verification captured from live runs against AWS (stands in for the screenshot deliverable;
regenerate with the commands in the README).

## E02 — Evaluation suite (release gate): 11/11

From `evals/report.json`, `PYTHONPATH=src python evals/eval_suite.py`:

```
ID      PASS  REQ  SCENARIO
E02-1   PASS  yes  Drug with many NDCs -> one J-code        -> J0593 HCPCS rows=1, NDCs->J0593=2
E02-2   PASS  yes  Formulation with no J-code               -> HCPCS=0 (want 0); pharmacy NDC rows=6
E02-3   PASS  yes  Fabricated/retired code proposed         -> rejected: not grounded - exists in HCPCS but not in the ASP crosswalk for this drug
E02-4   PASS  yes  Mapping changed between quarters          -> 2026-07=[J0640,J0641]  2026-10=[J0641,J0643]
E02-5   PASS  yes  Self-administered injectable both channels-> channels=[both]
E02-6   PASS  yes  Recently approved, no permanent J-code    -> HCPCS=0; NDC rows flagged
E02-7   PASS  yes  Confusable disease term (D84.1 vs T78.3)  -> D84.1='Defects in the complement system'; T78.3 distinct
E02-8   PASS  -    BR-4 billing-unit math                    -> J0593 billing_unit='150 billing units/package'
E02-9   PASS  -    Effective-date boundary (version pinned)  -> 07=[J0593] 10=[J0593]
E02-10  PASS  -    BR-6 new rule (Kalbitor -> medical/J1290) -> J1290 channel=medical (not both)
E02-11  PASS  -    Empty / no-candidate disease              -> rows=1; rejected: no reference match
------------------------------------------------------------
11/11 passed; required failures: 0
```

## E03 — Tracing (model / tool / validation)

Representative `TRACE` spans emitted during a pipeline run (also queryable in CloudWatch/
X-Ray from the Lambdas). The agent step calls Bedrock, then grounds candidates via tools;
the resolve step validates each code:

```
TRACE {"kind": "model", "name": "bedrock.converse", "ms": 11855.3, "stop": "tool_use"}
TRACE {"kind": "tool", "name": "tool.products_by_substance", "ms": 84.8, "ok": true, "rows": 9}
TRACE {"kind": "tool", "name": "tool.crosswalk_by_drug", "ms": 52.2, "ok": true, "rows": 2}
TRACE {"kind": "validation", "name": "validate_code", "system": "HCPCS", "code": "J0593", "version": "2026-10", "status": "valid"}
TRACE {"kind": "tool", "name": "tool.claims_count", "ms": 61.1, "ok": true}
TRACE {"kind": "pipeline", "name": "resolve", "disease": "hereditary angioedema", "version": "2026-10", "candidates": 7, "rows": 31}
TRACE {"kind": "pipeline", "name": "build_draft", "codebook_id": "hereditary-angioedema", "version": 1, "rows": 31, "valid": 31, "rejected": 0}
```

A full run produced **113 trace spans** for a 7-candidate / 31-row codebook.

## §04 — Least privilege (PHI protection)

The runtime role `codebook_app` is denied the PHI-bearing `restricted` schema outright, yet
can read the aggregate `claims_frequency` view. Verified live:

```
--- least-privilege check (codebook_app) ---
app denied restricted.claims (GOOD): permission denied for schema restricted; SQLState: 42501
app reads claims_frequency OK: [{'code_system': 'HCPCS', 'code': 'J0593', 'channel': 'MEDICAL', 'claim_count': 130}]
```

So PHI (names, DOB, member IDs, ZIP) in `restricted.claims` is unreachable by the codebook
runtime; only aggregate counts per code/channel are exposed. The admin credential is used
only by the one-time loader.

## Orchestration — deployed Step Functions run

The state machine `CodebookPipeline` ran end-to-end through the per-component scoped-IAM
Lambdas (Research → ResolveAndValidate → BuildDraft → EvalGate → PublishVersion).
`python scripts/start_pipeline.py "hereditary angioedema"` final output:

```
status: SUCCEEDED
{
  "disease_area": "hereditary angioedema",
  "candidate_count": 7,
  "codebook_id": "hereditary-angioedema",
  "codebook_version": 2,
  "row_count": 31,
  "eval": {"passed": true, "passed_count": 11, "total": 11},
  "published_to": "s3://.../codebooks/hereditary-angioedema/v2.csv"
}
```

Each step runs under its own least-privilege role: only `ResearchCandidates` may call
Bedrock; only `PublishVersion` may write S3; all use the `codebook_app` DB identity. (An
earlier run surfaced — and we fixed — a real IAM bug: a hand-written secret policy missed
the Secrets Manager `-??????` ARN suffix, so the Lambda was denied the app secret.)

## R08 — Review dashboard

`PYTHONPATH=src streamlit run src/ambit_codebook/review_app.py` renders the draft for a
named reviewer: version selector, per-code channel / billing unit / claims frequency /
validation status, summary metrics, and Approve/Reject to freeze a version. Screenshot:
`docs/review_dashboard.jpg`.

## Sample output

`out/hereditary-angioedema_v1.csv` — a draft HAE codebook (31 rows, standard schema): J0593
Takhzyro (both, 150 units/pkg, BR-3), J0596 Ruconest / J0598 Cinryze (medical, BR-2), J1290
Kalbitor (medical, BR-6), Orladeyo + icatibant NDCs (no Part B J-code, correctly not forced),
each with evidence, validation status, aggregate claims frequency, and reference versions.
