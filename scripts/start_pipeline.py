"""Start the deployed Step Functions pipeline and wait for it to finish.

    python scripts/start_pipeline.py "hereditary angioedema"
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boto3  # noqa: E402

from ambit_codebook.config import get_config  # noqa: E402

cfg = get_config()
outputs = json.loads((Path(__file__).resolve().parents[1] / "cdk-outputs.json").read_text())
sm_arn = outputs["AmbitCodebook"]["StateMachineArn"]
disease = sys.argv[1] if len(sys.argv) > 1 else "hereditary angioedema"

sfn = boto3.client("stepfunctions", region_name=cfg.region)
ex = sfn.start_execution(
    stateMachineArn=sm_arn,
    input=json.dumps({"disease_area": disease, "version": "2026-10"}),
)
arn = ex["executionArn"]
print(f"started: {arn}")
while True:
    d = sfn.describe_execution(executionArn=arn)
    if d["status"] != "RUNNING":
        break
    time.sleep(5)
print(f"status: {d['status']}")
if d.get("output"):
    out = json.loads(d["output"])
    print(json.dumps({k: v for k, v in out.items() if k != "candidates"}, indent=2))
