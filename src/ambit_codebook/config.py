"""Runtime configuration, resolved from CDK stack outputs or the environment.

Resolution order for each value:
  1. explicit environment variable (how the Lambdas receive it), then
  2. cdk-outputs.json written by `cdk deploy --outputs-file` (how local tools get it).

No secrets live here: we pass around the *ARN* of the Secrets Manager secret, and the
Data API resolves the credential server-side. The secret value never enters the process.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_STACK = "AmbitCodebook"
_OUTPUTS_FILE = Path(__file__).resolve().parents[2] / "cdk-outputs.json"


def _from_outputs() -> dict:
    try:
        data = json.loads(_OUTPUTS_FILE.read_text())
        return data.get(_STACK, {})
    except (OSError, json.JSONDecodeError):
        return {}


@dataclass(frozen=True)
class Config:
    region: str
    cluster_arn: str
    secret_arn: str          # admin secret (loader / setup only)
    database: str
    bucket: str
    app_secret_arn: str      # least-privilege app role (resolve/validate/agent)
    bedrock_model_id: str

    @property
    def resolve_secret_arn(self) -> str:
        """Prefer the least-privilege app role; fall back to admin if not yet created."""
        return self.app_secret_arn or self.secret_arn


@lru_cache(maxsize=1)
def get_config() -> Config:
    out = _from_outputs()

    def pick(env_key: str, out_key: str, default: str = "") -> str:
        return os.environ.get(env_key) or out.get(out_key, default)

    region = pick("AWS_REGION", "RegionOut", "us-east-1")
    app_secret_arn = pick("APP_SECRET_ARN", "AppSecretArn")
    if not app_secret_arn:
        # The loader creates ambit/codebook_app after deploy, and `cdk deploy` rewrites
        # the outputs file without it; resolve the ARN by name as a fallback.
        try:
            import boto3
            app_secret_arn = boto3.client("secretsmanager", region_name=region).describe_secret(
                SecretId="ambit/codebook_app")["ARN"]
        except Exception:  # noqa: BLE001
            app_secret_arn = ""
    return Config(
        region=region,
        cluster_arn=pick("CLUSTER_ARN", "ClusterArn"),
        secret_arn=pick("SECRET_ARN", "SecretArn"),
        database=pick("DB_NAME", "DbName", "codebook"),
        bucket=pick("REF_BUCKET", "BucketName"),
        app_secret_arn=app_secret_arn,
        bedrock_model_id=os.environ.get(
            "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
    )
