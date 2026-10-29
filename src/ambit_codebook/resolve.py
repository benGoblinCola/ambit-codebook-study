"""Deterministic resolution: drug -> NDCs -> J-code, as of a pinned crosswalk version.

This is the "data decides" half. The research agent only proposes candidate drugs; here
NDC->J-code comes solely from the crosswalk (never generated), billing channel comes from
the analyst-owned rules, every code is validated, and products with no Part B J-code are
identified as pharmacy/no-J-code rather than forced into a billing code.

Every output row carries an `audit_trail`: the ordered path it travelled from the agent's
proposal through the reference lookups, validation, and billing rule to the final decision,
so each code in the codebook is auditable (a node-path tracker for the derivation).
"""
from __future__ import annotations

from .billing import apply_billing_rules, billing_units_per_package
from .reference_tools import ReferenceTools
from .trace import record
from .validate import validate_code

NDC_VERSION = "2026-10-19"


def _row(disease, code, system, *, description, group, related, channel, unit,
         rationale, evidence, status, claims, refs, trail) -> dict:
    return {
        "disease_area": disease, "code": code, "code_system": system,
        "description": description, "group_name": group, "related_code": related,
        "benefit_channel": channel, "billing_unit": unit,
        "inclusion_rationale": rationale, "evidence": evidence,
        "validation_status": status, "claims_frequency": claims,
        "reference_versions": refs, "audit_trail": trail,
    }


def resolve(tools: ReferenceTools, disease_area: str, candidates: list[dict],
            version: str) -> list[dict]:
    rules = [dict(r) for r in tools.billing_rules()]
    rows: list[dict] = []
    for cand in candidates:
        rows += _resolve_candidate(tools, rules, disease_area, cand, version)
    record("pipeline", "resolve", disease=disease_area, version=version,
           candidates=len(candidates), rows=len(rows))
    return rows


def _resolve_candidate(tools, rules, disease, cand, version) -> list[dict]:
    name = (cand.get("drug") or cand.get("name") or "").strip()
    substance = (cand.get("substance") or name).strip()
    rationale = cand.get("rationale") or ""
    refs = {"crosswalk": version, "hcpcs": version, "ndc": NDC_VERSION}
    rows: list[dict] = []

    products = tools.products_by_substance(substance) or (tools.products_by_brand(name) if name else [])
    route = next((p["route"] for p in products if p.get("route")), None)
    dosage_form = next((p["dosage_form"] for p in products if p.get("dosage_form")), None)
    self_admin = tools.self_admin_for(substance)

    xwalk = tools.crosswalk_by_drug(substance, version)
    if not xwalk and name:
        xwalk = tools.crosswalk_by_drug(name, version)
    jrows: dict[str, dict] = {}
    for r in xwalk:
        jrows.setdefault(r["hcpcs_code"], r)
    has_j = bool(jrows)
    bd = apply_billing_rules(rules, route, dosage_form, self_admin)

    # The shared derivation path for this candidate (the first nodes every code inherits).
    base_trail = [
        {"node": "agent.proposed", "detail": f"{name or substance} ({substance}) — {rationale}"},
        {"node": "tool.products_by_substance",
         "detail": (f"{len(products)} NDC-directory product(s); route={route}, form={dosage_form}"
                    if products else "no NDC-directory match")},
        {"node": "analyst.self_admin", "detail": f"self_administered={self_admin} (analyst-owned)"},
        {"node": "tool.crosswalk",
         "detail": (f"ASP crosswalk {version}: J-code(s) {sorted(jrows)}"
                    if has_j else f"ASP crosswalk {version}: no Part B entry")},
        {"node": "rule.billing", "detail": f"{bd.rule_id or 'none'} → channel={bd.channel}; {bd.note}"},
    ]

    # (a) Grounding check (Principle 02) for any model-proposed code.
    for pc in (cand.get("proposed_codes") or []):
        if pc in jrows:
            continue
        in_hcpcs = tools.hcpcs(pc, version) is not None
        why = ("exists in HCPCS but is not in the ASP crosswalk for this drug"
               if in_hcpcs else "not found in the reference tables")
        record("validation", "grounding_check", code=pc, drug=substance,
               version=version, status="rejected", reason=why)
        rows.append(_row(
            disease, pc, "HCPCS", description=None, group=name or substance, related=None,
            channel=None, unit=None, rationale=f"model-proposed code; {rationale}".strip(),
            evidence="model recall only (not grounded in the ASP crosswalk)",
            status=f"rejected: not grounded - {why}", claims=None, refs=refs,
            trail=base_trail + [
                {"node": "grounding_check", "detail": f"{pc}: {why}"},
                {"node": "decision", "detail": f"reject {pc} (ungrounded)"}]))

    # (b) Deterministic J-code rows from the crosswalk.
    for jc, xr in sorted(jrows.items()):
        ok, reason = validate_code(tools, "HCPCS", jc, version)
        hc = tools.hcpcs(jc, version)
        desc = (hc or {}).get("short_description")
        unit = billing_units_per_package(xr.get("billunitspkg"))
        claims = tools.claims_count("HCPCS", jc)
        rep_ndc = xr.get("ndc11")
        chan = bd.channel or ("medical" if has_j else None)
        rows.append(_row(
            disease, jc, "HCPCS", description=desc, group=name or substance, related=rep_ndc,
            channel=chan,
            unit=(f"{unit:g} billing units/package" if unit else (xr.get("hcpcs_dosage") or None)),
            rationale=f"{rationale} | billing {bd.rule_id or 'none'}: {bd.note}".strip(" |"),
            evidence=f"crosswalk {version}: NDC {rep_ndc} -> {jc}; HCPCS {version} {jc} '{desc}'",
            status=("valid" if ok else f"rejected: {reason}"),
            claims=(sum(claims.values()) if claims else 0), refs=refs,
            trail=base_trail + [
                {"node": "validate", "detail": f"{jc} {'valid' if ok else reason} in HCPCS {version}"},
                {"node": "billing.units", "detail": f"BR-4: {unit:g} units/package" if unit else "BR-4: n/a"},
                {"node": "claims", "detail": f"{jc}: {claims or {}}"},
                {"node": "decision", "detail": f"include {jc} (HCPCS), channel={chan}"}]))

    # (c) NDC rows: from the crosswalk (Part B) or the NDC directory (pharmacy/no J).
    seen: set[str] = set()
    if has_j:
        for r in xwalk:
            ndc11 = r.get("ndc11")
            if not ndc11 or ndc11 in seen:
                continue
            seen.add(ndc11)
            claims = tools.claims_count("NDC", ndc11)
            rows.append(_row(
                disease, ndc11, "NDC", description=r.get("drug_name") or substance,
                group=name or substance, related=r.get("hcpcs_code"),
                channel=bd.channel, unit=None,
                rationale=f"{rationale} | billing {bd.rule_id or 'none'}".strip(" |"),
                evidence=f"crosswalk {version} NDC {ndc11}",
                status="valid", claims=(sum(claims.values()) if claims else 0), refs=refs,
                trail=base_trail + [
                    {"node": "claims", "detail": f"NDC {ndc11}: {claims or {}}"},
                    {"node": "decision", "detail": f"include NDC {ndc11} -> {r.get('hcpcs_code')}, channel={bd.channel}"}]))
            if len(seen) >= 8:
                break
    else:
        for p in products:
            pn9 = p.get("product_ndc9")
            if not pn9 or pn9 in seen:
                continue
            seen.add(pn9)
            claims = tools.claims_count_by_ndc9(pn9)
            rows.append(_row(
                disease, p.get("product_ndc"), "NDC",
                description=p.get("proprietary_name") or substance,
                group=name or substance, related=None, channel=bd.channel, unit=None,
                rationale=(f"{rationale} | no Part B J-code (correct) | billing {bd.rule_id or 'none'}").strip(" |"),
                evidence=f"NDC directory {NDC_VERSION}: {p.get('product_ndc')} {p.get('route')}/{p.get('dosage_form')}",
                status="valid", claims=(sum(claims.values()) if claims else 0), refs=refs,
                trail=base_trail + [
                    {"node": "claims", "detail": f"product {pn9}: {claims or {}}"},
                    {"node": "decision", "detail": f"include NDC {p.get('product_ndc')} (pharmacy/no J-code), channel={bd.channel}"}]))
            if len(seen) >= 8:
                break

    # (d) No billing rule matched -> flag non-rejected rows for the reviewer (never guess).
    if bd.channel is None:
        for row in rows:
            if not row["validation_status"].startswith("rejected"):
                row["validation_status"] = "flagged: no billing rule matched (reviewer)"
                row["audit_trail"] = row["audit_trail"] + [
                    {"node": "flag", "detail": "no billing rule matched — flagged for reviewer"}]

    # (e) Candidate found nothing in the reference data at all.
    if not rows:
        rows.append(_row(
            disease, None, None, description=None, group=name or substance, related=None,
            channel=None, unit=None, rationale=rationale,
            evidence="no match in NDC directory or crosswalk",
            status="rejected: no reference match for candidate", claims=None, refs=refs,
            trail=base_trail + [{"node": "decision", "detail": "reject — no reference match"}]))
    return rows
