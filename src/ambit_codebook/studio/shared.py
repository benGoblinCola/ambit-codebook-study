"""Shared helpers for the studio pages (DB access, Step Functions, small utilities)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
import streamlit as st

from ambit_codebook.config import get_config
from ambit_codebook.db import Db

REPO_ROOT = Path(__file__).resolve().parents[3]


@st.cache_resource
def get_db() -> Db:
    return Db(secret_arn=get_config().resolve_secret_arn)


def _outputs() -> dict:
    try:
        return json.loads((REPO_ROOT / "cdk-outputs.json").read_text()).get("AmbitCodebook", {})
    except Exception:  # noqa: BLE001
        return {}


def sfn_client():
    return boto3.client("stepfunctions", region_name=get_config().region)


@st.cache_data(ttl=300)
def state_machine_arn() -> str:
    arn = os.environ.get("STATE_MACHINE_ARN") or _outputs().get("StateMachineArn")
    if arn:
        return arn
    # Fallback: find by name prefix (outputs file is gitignored / may be absent).
    for sm in sfn_client().list_state_machines().get("stateMachines", []):
        if sm["name"].startswith("CodebookPipeline"):
            return sm["stateMachineArn"]
    return ""


def list_versions(db: Db) -> list[dict]:
    return db.execute(
        "SELECT codebook_id, codebook_version, disease_area, status, reviewed_by, "
        "reviewed_at, reference_versions FROM codebook_version "
        "ORDER BY codebook_id, codebook_version DESC"
    )


def get_rows(db: Db, cid: str, ver: int) -> list[dict]:
    return db.execute(
        "SELECT code, code_system, description, group_name, related_code, benefit_channel, "
        "billing_unit, validation_status, claims_frequency, evidence, audit_trail "
        "FROM codebook WHERE codebook_id = :i AND codebook_version = :v "
        "ORDER BY code_system, code",
        {"i": cid, "v": ver},
    )


def status_cat(s: str) -> str:
    if s == "valid":
        return "valid"
    return "rejected" if str(s).startswith("rejected") else "flagged"


def bucket_name() -> str:
    return get_config().bucket or _outputs().get("BucketName", "")
