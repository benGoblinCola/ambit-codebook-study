"""Thin wrapper over the Aurora RDS Data API (boto3 ``rds-data``).

Why the Data API: the Lambdas never open a socket to the database, so they stay out of
the VPC and reach Aurora over an IAM-authorized HTTPS endpoint. Each caller presents the
ARN of a Secrets Manager secret; the credential is resolved server-side and never enters
the process. Which secret a component may read is what its IAM policy scopes -- that is
the enforcement point for least privilege.
"""
from __future__ import annotations

import decimal
from typing import Any, Iterable, Sequence

import boto3

from .config import Config, get_config


def _to_param(name: str, value: Any) -> dict:
    """Convert a Python value to a Data API typed parameter."""
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"name": name, "value": {"longValue": value}}
    if isinstance(value, float):
        return {"name": name, "value": {"doubleValue": value}}
    if isinstance(value, decimal.Decimal):
        return {"name": name, "value": {"stringValue": str(value)}, "typeHint": "DECIMAL"}
    return {"name": name, "value": {"stringValue": str(value)}}


def _from_field(field: dict) -> Any:
    if "isNull" in field and field["isNull"]:
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if key in field:
            return field[key]
    return None


class Db:
    """Executes SQL against the cluster named in the resolved config.

    ``secret_arn`` selects the identity: pass the admin secret for setup/load, or the
    least-privilege app secret for the resolve/validate/agent path.
    """

    def __init__(self, secret_arn: str | None = None, cfg: Config | None = None):
        self.cfg = cfg or get_config()
        self.secret_arn = secret_arn or self.cfg.resolve_secret_arn
        self._client = boto3.client("rds-data", region_name=self.cfg.region)

    def execute(self, sql: str, params: dict | None = None,
                transaction_id: str | None = None,
                continue_after_timeout: bool = False) -> list[dict]:
        kwargs: dict = dict(
            resourceArn=self.cfg.cluster_arn,
            secretArn=self.secret_arn,
            database=self.cfg.database,
            sql=sql,
            includeResultMetadata=True,
        )
        if params:
            kwargs["parameters"] = [_to_param(k, v) for k, v in params.items()]
        if transaction_id:
            kwargs["transactionId"] = transaction_id
        if continue_after_timeout:
            # The Data API caps a statement call at 45s; this lets a long DML/COPY keep
            # running server-side after the call returns, so the caller can poll for it.
            kwargs["continueAfterTimeout"] = True
        resp = self._client.execute_statement(**kwargs)
        cols = [c["name"] for c in resp.get("columnMetadata", [])]
        return [
            {cols[i]: _from_field(f) for i, f in enumerate(row)}
            for row in resp.get("records", [])
        ]

    def batch(self, sql: str, param_sets: Iterable[dict],
              transaction_id: str | None = None) -> None:
        sets = [[_to_param(k, v) for k, v in row.items()] for row in param_sets]
        if not sets:
            return
        # Data API caps a batch at 1000 parameter sets.
        for i in range(0, len(sets), 1000):
            kwargs: dict = dict(
                resourceArn=self.cfg.cluster_arn,
                secretArn=self.secret_arn,
                database=self.cfg.database,
                sql=sql,
                parameterSets=sets[i:i + 1000],
            )
            if transaction_id:
                kwargs["transactionId"] = transaction_id
            self._client.batch_execute_statement(**kwargs)

    def execute_script(self, script: str) -> None:
        """Run a multi-statement SQL script (splits on ';'). For DDL only."""
        for stmt in _split_statements(script):
            self.execute(stmt)

    def begin(self) -> str:
        return self._client.begin_transaction(
            resourceArn=self.cfg.cluster_arn,
            secretArn=self.secret_arn,
            database=self.cfg.database,
        )["transactionId"]

    def commit(self, transaction_id: str) -> None:
        self._client.commit_transaction(
            resourceArn=self.cfg.cluster_arn,
            secretArn=self.secret_arn,
            transactionId=transaction_id,
        )

    def rollback(self, transaction_id: str) -> None:
        self._client.rollback_transaction(
            resourceArn=self.cfg.cluster_arn,
            secretArn=self.secret_arn,
            transactionId=transaction_id,
        )


def _split_statements(script: str) -> Sequence[str]:
    out, buf, in_dollar = [], [], False
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        if "$$" in line:
            in_dollar = not in_dollar
        buf.append(line)
        if stripped.endswith(";") and not in_dollar:
            out.append("\n".join(buf).rstrip().rstrip(";"))
            buf = []
    if buf:
        out.append("\n".join(buf).rstrip().rstrip(";"))
    return [s for s in out if s.strip()]
