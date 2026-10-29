# AI Tools Note

**Tool used:** Claude Code (Claude Opus) as a pair-programming agent, driving the AWS build,
the Python application, the CDK stack, and the evaluation suite.

**What it did well**
- Scaffolded the CDK stack and the ingestion/resolve/agent/eval modules quickly, and ran the
  real AWS deploy + data load, iterating on errors against live services.
- Wrote the deterministic resolver and the billing-rule engine against the real data pack and
  verified the HAE eval anchors (J0593 Takhzyro, J0596 Ruconest, J1290 Kalbitor, Orladeyo
  no-J-code, the Leucovorin J0640→J0643 version change) before wiring the agent.
- Implemented the Bedrock Converse tool-use loop so the research agent grounds each candidate
  in the NDC directory rather than relying on recall.

**What needed correction (and was corrected)**
- **Aurora engine version:** first deploy used `16.6`, which is not offered (non-limitless) in
  us-east-1; switched to `16.8` after querying `describe-db-engine-versions`.
- **Data API 45-second statement cap:** bulk-importing the 40 MB NDC file and the 5.8 MB claims
  file via the `aws_s3` extension exceeded the Data API's hard 45s limit (and
  `continueAfterTimeout` doesn't keep a SELECT-function alive). Switched those to batched
  inserts, each batch well under the cap.
- **Grounding trap:** the first validator checked whether a model-proposed code merely *existed*
  in HCPCS, which let J1744 (a real but retired code, absent from the Part B crosswalk for
  icatibant) slip through. Fixed to validate against the **crosswalk for that drug** — ungrounded
  recall is now rejected with a reason (eval scenario 3).

**Judgment the human kept:** platform choices (AWS, Aurora + Data API over a VPC-bound DB,
read-only reference tools instead of a Bedrock Knowledge Base for this scale), the decision to
build on real AWS because scoped IAM / PHI handling are graded, and the BR-6 billing rule.
