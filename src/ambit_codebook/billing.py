"""Billing-rule engine: analyst-owned rules as DATA decide claims channel.

Pure and dependency-free so it is directly unit-testable (E01). The rules themselves
come from the billing_rule table (loaded from billing_rules.csv); nothing here is a
prompt or hard-coded policy. First matching channel rule wins; no match => flag for a
reviewer, never a guess (per the brief).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BillingDecision:
    channel: str | None       # pharmacy | medical | both | None(=> flag for reviewer)
    expects_hcpcs: str | None  # yes | no | either
    rule_id: str | None
    note: str


def _route_match(match_route: str | None, route: str | None) -> bool:
    if not match_route:
        return True
    return match_route.strip().upper() in (route or "").upper()


def _form_match(match_form: str | None, dosage_form: str | None) -> bool:
    if not match_form:
        return True
    opts = [o.strip().upper() for o in match_form.split("|") if o.strip()]
    f = (dosage_form or "").upper()
    return any(o in f for o in opts)


def _selfadmin_match(match_sa: str | None, self_administered: bool | None) -> bool:
    if not match_sa:
        return True
    if self_administered is None:
        return False
    return (match_sa.strip().upper() == "Y") == bool(self_administered)


def apply_billing_rules(
    rules: list[dict],
    route: str | None,
    dosage_form: str | None,
    self_administered: bool | None,
) -> BillingDecision:
    """Return the channel decision from the first matching channel rule."""
    for r in sorted(rules, key=lambda x: (x.get("sort_order") or 0)):
        if r.get("rule_type") != "channel":
            continue
        if (
            _route_match(r.get("match_route"), route)
            and _form_match(r.get("match_dosage_form"), dosage_form)
            and _selfadmin_match(r.get("match_self_administered"), self_administered)
        ):
            return BillingDecision(
                channel=r.get("then_channel"),
                expects_hcpcs=r.get("then_expects_hcpcs"),
                rule_id=r.get("rule_id"),
                note=r.get("note") or "",
            )
    return BillingDecision(None, None, None, "no billing rule matched - flag for reviewer")


def billing_units_per_package(billunitspkg) -> float | None:
    """BR-4: units a package contributes on the J-code, taken from the crosswalk's
    pre-computed BILLUNITSPKG (package_strength / hcpcs_unit)."""
    try:
        return float(billunitspkg) if billunitspkg is not None else None
    except (TypeError, ValueError):
        return None
