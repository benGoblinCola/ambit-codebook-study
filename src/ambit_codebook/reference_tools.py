"""Typed, read-only reference tools (reference architecture step 8).

One small surface shared by the research agent, the deterministic pipeline, and the eval
suite. Every call runs under the least-privilege `codebook_app` role by default, so these
tools can read reference tables and the aggregate claims_frequency view but never the
PHI-bearing restricted.claims. Each call is traced (model/tool/validation) for E03.
"""
from __future__ import annotations

from .config import get_config
from .db import Db
from .trace import trace


class ReferenceTools:
    def __init__(self, db: Db | None = None):
        # Default to the least-privilege app identity, not the admin secret.
        self.db = db or Db(secret_arn=get_config().resolve_secret_arn)

    # --- drug / product discovery ------------------------------------------
    @trace("tool.products_by_substance")
    def products_by_substance(self, substance: str) -> list[dict]:
        return self.db.execute(
            "SELECT product_ndc, product_ndc9, proprietary_name, nonproprietary_name, "
            "dosage_form, route, substance_name, labeler_name "
            "FROM ndc_product WHERE lower(substance_name) LIKE :q ORDER BY product_ndc",
            {"q": f"%{substance.lower()}%"},
        )

    @trace("tool.products_by_brand")
    def products_by_brand(self, brand: str) -> list[dict]:
        return self.db.execute(
            "SELECT product_ndc, product_ndc9, proprietary_name, nonproprietary_name, "
            "dosage_form, route, substance_name, labeler_name "
            "FROM ndc_product WHERE lower(proprietary_name) LIKE :q ORDER BY product_ndc",
            {"q": f"%{brand.lower()}%"},
        )

    # --- deterministic crosswalk (NDC -> J-code) as of a pinned version -----
    @trace("tool.crosswalk_by_drug")
    def crosswalk_by_drug(self, name: str, version: str) -> list[dict]:
        return self.db.execute(
            "SELECT hcpcs_code, ndc11, ndc_raw, drug_name, labeler_name, hcpcs_dosage, "
            "pkg_size, billunits, billunitspkg, effective_date, end_date "
            "FROM crosswalk WHERE reference_version = :v AND lower(drug_name) LIKE :q",
            {"v": version, "q": f"%{name.lower()}%"},
        )

    @trace("tool.crosswalk_by_ndc")
    def crosswalk_by_ndc(self, ndc11: str, version: str) -> list[dict]:
        return self.db.execute(
            "SELECT hcpcs_code, ndc11, drug_name, billunitspkg, effective_date, end_date "
            "FROM crosswalk WHERE reference_version = :v AND ndc11 = :n",
            {"v": version, "n": ndc11},
        )

    # --- code validation lookups -------------------------------------------
    @trace("tool.hcpcs")
    def hcpcs(self, code: str, version: str) -> dict | None:
        rows = self.db.execute(
            "SELECT code, short_description, long_description, termination_date "
            "FROM hcpcs WHERE reference_version = :v AND code = :c",
            {"v": version, "c": code},
        )
        return rows[0] if rows else None

    @trace("tool.icd_lookup")
    def icd_lookup(self, code: str | None = None, term: str | None = None,
                   version: str = "fy2027") -> list[dict]:
        if code:
            return self.db.execute(
                "SELECT code, description FROM icd10cm WHERE reference_version=:v AND code=:c",
                {"v": version, "c": code},
            )
        return self.db.execute(
            "SELECT code, description FROM icd10cm WHERE reference_version=:v "
            "AND lower(description) LIKE :q ORDER BY code LIMIT 25",
            {"v": version, "q": f"%{(term or '').lower()}%"},
        )

    # --- analyst-owned self-administration flag ----------------------------
    @trace("tool.self_admin_for")
    def self_admin_for(self, substance: str) -> bool | None:
        rows = self.db.execute(
            "SELECT DISTINCT self_administered FROM product_admin "
            "WHERE lower(substance) LIKE :q AND self_administered IS NOT NULL",
            {"q": f"%{substance.lower()}%"},
        )
        vals = {r["self_administered"] for r in rows}
        if len(vals) == 1:
            return vals.pop()
        return None  # unknown or conflicting -> resolver flags for reviewer

    # --- aggregate claims frequency (no PHI) -------------------------------
    @trace("tool.claims_count")
    def claims_count(self, code_system: str, code: str) -> dict:
        rows = self.db.execute(
            "SELECT channel, claim_count FROM claims_frequency "
            "WHERE code_system = :s AND code = :c",
            {"s": code_system, "c": code},
        )
        return {r["channel"]: int(r["claim_count"]) for r in rows}

    @trace("tool.claims_count_by_ndc9")
    def claims_count_by_ndc9(self, ndc9: str) -> dict:
        """Sum claims for all package NDCs under a labeler-product (pharmacy products
        appear in claims at 11-digit; their first 9 digits are the product NDC)."""
        rows = self.db.execute(
            "SELECT channel, sum(claim_count) AS claim_count FROM claims_frequency "
            "WHERE code_system = 'NDC' AND left(code, 9) = :n GROUP BY channel",
            {"n": ndc9},
        )
        return {r["channel"]: int(r["claim_count"]) for r in rows}

    @trace("tool.billing_rules")
    def billing_rules(self) -> list[dict]:
        return self.db.execute("SELECT * FROM billing_rule ORDER BY sort_order")
