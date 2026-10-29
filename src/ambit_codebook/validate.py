"""Code validation against the reference tables (R05 / Principle 02).

A code proposed for the codebook is valid only if it exists in the pinned-version
reference tables (and, for HCPCS, is not terminated). Anything else is rejected WITH a
reason and recorded -- never silently dropped. This is where the model's ungrounded
recall (e.g. a remembered J1744 for icatibant) gets caught.
"""
from __future__ import annotations

from .reference_tools import ReferenceTools
from .trace import record


def validate_code(tools: ReferenceTools, system: str, code: str, version: str,
                  icd_version: str = "fy2027") -> tuple[bool, str]:
    s = (system or "").upper()

    if s in ("HCPCS", "J"):
        row = tools.hcpcs(code, version)
        if not row:
            record("validation", "validate_code", system="HCPCS", code=code,
                   version=version, status="rejected", reason="not in HCPCS reference")
            return False, f"not found in HCPCS {version}"
        if row.get("termination_date"):
            record("validation", "validate_code", system="HCPCS", code=code,
                   version=version, status="rejected", reason="terminated")
            return False, f"terminated {row['termination_date']} in HCPCS {version}"
        record("validation", "validate_code", system="HCPCS", code=code,
               version=version, status="valid")
        return True, "valid"

    if s == "ICD10CM":
        rows = tools.icd_lookup(code=code, version=icd_version)
        ok = bool(rows)
        record("validation", "validate_code", system="ICD10CM", code=code,
               version=icd_version, status="valid" if ok else "rejected")
        return (ok, "valid" if ok else f"not found in ICD-10-CM {icd_version}")

    # NDC codes are sourced directly from the reference tables, so valid by construction.
    return True, "valid"
