"""AWS Lambda handlers -- the Step Functions task bodies.

Each handler takes the pipeline STATE (a dict) and returns the state updated with its
output, so the state machine threads one object through the pipeline. The business logic
lives in the shared modules; these are thin adapters. Every Lambda runs under its own
least-privilege role (see the CDK stack): all use the codebook_app DB identity; only
`research` may call Bedrock; only `publish` may write S3.
"""
from __future__ import annotations

import json
import os
import tempfile

import boto3

from . import evals as evalmod
from .agent.research import research_candidates
from .codebook import build_codebook, write_csv
from .reference_tools import ReferenceTools
from .resolve import resolve


def _state(event) -> dict:
    """Step Functions may hand us the state as a dict or a JSON string; normalize."""
    return json.loads(event) if isinstance(event, str) else (event or {})


def research(event, _ctx=None):
    event = _state(event)
    tools = ReferenceTools()
    candidates = research_candidates(event["disease_area"], tools)
    return {**event, "candidates": candidates, "candidate_count": len(candidates)}


def resolve_validate(event, _ctx=None):
    event = _state(event)
    tools = ReferenceTools()
    rows = resolve(tools, event["disease_area"], event.get("candidates", []),
                   event.get("version", "2026-10"))
    return {**event, "rows": rows}


def build_draft(event, _ctx=None):
    event = _state(event)
    version = event.get("version", "2026-10")
    cid, ver = build_codebook(event["disease_area"], event.get("rows", []), version)
    state = {k: v for k, v in event.items() if k != "rows"}  # drop rows to keep state small
    return {**state, "codebook_id": cid, "codebook_version": ver,
            "row_count": len(event.get("rows", []))}


def eval_gate(event, _ctx=None):
    event = _state(event)
    report = evalmod.run_suite(ReferenceTools())
    return {**event, "eval": {"passed": report["required_failures"] == 0,
                              "passed_count": report["passed"], "total": report["total"]}}


def publish(event, _ctx=None):
    event = _state(event)
    cid, ver = event["codebook_id"], event["codebook_version"]
    local = write_csv(cid, ver, os.path.join(tempfile.gettempdir(), f"{cid}_v{ver}.csv"))
    bucket = os.environ["REF_BUCKET"]
    key = f"codebooks/{cid}/v{ver}.csv"
    boto3.client("s3").upload_file(str(local), bucket, key)
    return {**event, "published_to": f"s3://{bucket}/{key}"}
