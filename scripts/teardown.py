"""Tear down all AWS resources for this case study.

    python scripts/teardown.py

Destroys the AmbitCodebook stack (Aurora, S3, Lambdas, Step Functions, VPC, admin secret)
and deletes the codebook_app secret, which was created at load time rather than by CDK.
The CDK bootstrap stack and its asset bucket are left in place; remove them manually if
you want the account fully clean.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boto3  # noqa: E402

REGION = "us-east-1"
ROOT = Path(__file__).resolve().parents[1]

print("Destroying AmbitCodebook stack (this removes Aurora, S3, Lambdas, Step Functions)...")
subprocess.run(["cdk", "destroy", "AmbitCodebook", "--force"], cwd=ROOT, check=False, shell=True)

print("Deleting the codebook_app secret (created by the loader, not CDK)...")
sm = boto3.client("secretsmanager", region_name=REGION)
try:
    sm.delete_secret(SecretId="ambit/codebook_app", ForceDeleteWithoutRecovery=True)
    print("  deleted ambit/codebook_app")
except Exception as e:  # noqa: BLE001
    print(f"  (secret delete skipped: {str(e)[:100]})")

print("Teardown complete.")
