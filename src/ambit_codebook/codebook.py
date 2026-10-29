"""Assemble resolved rows into a versioned draft codebook (R07) and write the CSV.

Writes under the least-privilege app role (granted INSERT on the codebook tables only).
A codebook starts as a draft version; review freezes it (R08 / Principle 04).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .config import get_config
from .db import Db
from .trace import record

SCHEMA_COLS = [
    "codebook_id", "codebook_version", "disease_area", "code", "code_system", "description",
    "group_name", "related_code", "benefit_channel", "billing_unit", "inclusion_rationale",
    "evidence", "validation_status", "claims_frequency", "reference_versions", "audit_trail",
    "review_status",
]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "codebook"


def build_codebook(disease_area: str, rows: list[dict], version: str,
                   db: Db | None = None) -> tuple[str, int]:
    db = db or Db(secret_arn=get_config().resolve_secret_arn)
    codebook_id = _slug(disease_area)
    nxt = db.execute(
        "SELECT coalesce(max(codebook_version), 0) + 1 AS v FROM codebook_version WHERE codebook_id = :i",
        {"i": codebook_id},
    )[0]["v"]
    ref_versions = {"crosswalk": version, "hcpcs": version, "ndc": "2026-10-19", "icd10cm": "fy2027"}

    db.execute(
        "INSERT INTO codebook_version (codebook_id, codebook_version, disease_area, reference_versions, status) "
        "VALUES (:i, :v, :d, CAST(:r AS JSONB), 'draft')",
        {"i": codebook_id, "v": nxt, "d": disease_area, "r": json.dumps(ref_versions)},
    )

    insert = (
        "INSERT INTO codebook (codebook_id, codebook_version, disease_area, code, code_system, "
        "description, group_name, related_code, benefit_channel, billing_unit, inclusion_rationale, "
        "evidence, validation_status, claims_frequency, reference_versions, audit_trail, review_status) VALUES "
        "(:codebook_id, :codebook_version, :disease_area, :code, :code_system, :description, "
        ":group_name, :related_code, :benefit_channel, :billing_unit, :inclusion_rationale, "
        ":evidence, :validation_status, :claims_frequency, CAST(:reference_versions AS JSONB), "
        "CAST(:audit_trail AS JSONB), :review_status)"
    )
    params = [{
        "codebook_id": codebook_id, "codebook_version": nxt,
        "disease_area": r.get("disease_area"), "code": r.get("code"),
        "code_system": r.get("code_system"), "description": r.get("description"),
        "group_name": r.get("group_name"), "related_code": r.get("related_code"),
        "benefit_channel": r.get("benefit_channel"), "billing_unit": r.get("billing_unit"),
        "inclusion_rationale": r.get("inclusion_rationale"), "evidence": r.get("evidence"),
        "validation_status": r.get("validation_status"),
        "claims_frequency": r.get("claims_frequency"),
        "reference_versions": json.dumps(r.get("reference_versions") or ref_versions),
        "audit_trail": json.dumps(r.get("audit_trail") or []),
        "review_status": "pending",
    } for r in rows]
    db.batch(insert, params)

    valid = sum(1 for r in rows if r.get("validation_status") == "valid")
    rejected = sum(1 for r in rows if str(r.get("validation_status", "")).startswith("rejected"))
    record("pipeline", "build_draft", codebook_id=codebook_id, version=nxt,
           rows=len(rows), valid=valid, rejected=rejected)
    return codebook_id, nxt


def write_csv(codebook_id: str, version: int, out_path: str | Path,
              db: Db | None = None) -> Path:
    db = db or Db(secret_arn=get_config().resolve_secret_arn)
    cols = SCHEMA_COLS + ["reviewed_by", "reviewed_at"]
    data = db.execute(
        f"SELECT {', '.join(cols)} FROM codebook WHERE codebook_id = :i AND codebook_version = :v "
        "ORDER BY code_system, code",
        {"i": codebook_id, "v": version},
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in data:
            w.writerow({c: row.get(c) for c in cols})
    return out
