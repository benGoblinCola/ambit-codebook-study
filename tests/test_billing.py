"""Unit tests for the billing-rule engine (E01) -- pure, no AWS."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ambit_codebook.billing import apply_billing_rules, billing_units_per_package  # noqa: E402

# Mirrors seed/billing_rules_extended.csv (BR-1..BR-6).
RULES = [
    {"rule_id": "BR-1", "rule_type": "channel", "match_route": "ORAL",
     "match_dosage_form": "TABLET|CAPSULE|SOLUTION|PELLET", "match_self_administered": None,
     "then_channel": "pharmacy", "then_expects_hcpcs": "no", "sort_order": 0},
    {"rule_id": "BR-2", "rule_type": "channel", "match_route": "INTRAVENOUS",
     "match_dosage_form": None, "match_self_administered": None,
     "then_channel": "medical", "then_expects_hcpcs": "yes", "sort_order": 1},
    {"rule_id": "BR-3", "rule_type": "channel", "match_route": "SUBCUTANEOUS",
     "match_dosage_form": None, "match_self_administered": "Y",
     "then_channel": "both", "then_expects_hcpcs": "either", "sort_order": 2},
    {"rule_id": "BR-4", "rule_type": "units", "sort_order": 3},
    {"rule_id": "BR-5", "rule_type": "versioning", "sort_order": 4},
    {"rule_id": "BR-6", "rule_type": "channel", "match_route": "SUBCUTANEOUS",
     "match_dosage_form": None, "match_self_administered": "N",
     "then_channel": "medical", "then_expects_hcpcs": "yes", "sort_order": 5},
]


def test_br1_oral_pharmacy():
    d = apply_billing_rules(RULES, "ORAL", "CAPSULE", None)
    assert d.rule_id == "BR-1" and d.channel == "pharmacy" and d.expects_hcpcs == "no"


def test_br2_iv_medical():
    d = apply_billing_rules(RULES, "INTRAVENOUS", "POWDER", None)
    assert d.rule_id == "BR-2" and d.channel == "medical"


def test_br3_sc_selfadmin_both():
    d = apply_billing_rules(RULES, "SUBCUTANEOUS", "SOLUTION", True)
    assert d.rule_id == "BR-3" and d.channel == "both"


def test_br6_sc_provider_medical():
    # The NEW rule: SC + NOT self-administered -> medical (Kalbitor case).
    d = apply_billing_rules(RULES, "SUBCUTANEOUS", "SOLUTION", False)
    assert d.rule_id == "BR-6" and d.channel == "medical"


def test_br3_and_br6_are_disjoint_on_selfadmin():
    assert apply_billing_rules(RULES, "SUBCUTANEOUS", None, True).rule_id == "BR-3"
    assert apply_billing_rules(RULES, "SUBCUTANEOUS", None, False).rule_id == "BR-6"


def test_no_match_flags_for_reviewer():
    d = apply_billing_rules(RULES, "INTRATHECAL", "SOLUTION", None)
    assert d.channel is None and "flag" in d.note.lower()


def test_multivalued_route_matches():
    d = apply_billing_rules(RULES, "ORAL; INTRAVENOUS", "TABLET", None)
    assert d.rule_id == "BR-1"


def test_br4_units_from_crosswalk():
    assert billing_units_per_package("150") == 150.0
    assert billing_units_per_package(None) is None
