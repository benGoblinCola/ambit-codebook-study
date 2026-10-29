"""Eval scenarios + runner (E02), importable so both the CLI and the EvalGate Lambda
share one source of truth. run_suite() returns a report dict; the CLI prints + gates.
"""
from __future__ import annotations

from .reference_tools import ReferenceTools
from .resolve import resolve

V = "2026-10"
DISEASE = "hereditary angioedema"


def _rows(tools, cand, version=V):
    return resolve(tools, DISEASE, [cand], version)


def s01(tools):
    rows = _rows(tools, {"drug": "Takhzyro", "substance": "LANADELUMAB"})
    j = [r for r in rows if r["code_system"] == "HCPCS" and r["code"] == "J0593"]
    ndc = [r for r in rows if r["code_system"] == "NDC" and r["related_code"] == "J0593"]
    return (len(j) == 1 and len(ndc) >= 2), f"J0593 HCPCS={len(j)}, NDCs->J0593={len(ndc)}"


def s02(tools):
    rows = _rows(tools, {"drug": "Orladeyo", "substance": "BEROTRALSTAT"})
    hc = [r for r in rows if r["code_system"] == "HCPCS"]
    ndc = [r for r in rows if r["code_system"] == "NDC"]
    return (not hc and ndc and all(r["benefit_channel"] == "pharmacy" for r in ndc)), \
        f"HCPCS={len(hc)} (want 0); pharmacy NDC={len(ndc)}"


def s03(tools):
    rows = _rows(tools, {"drug": "Firazyr", "substance": "ICATIBANT", "proposed_codes": ["J1744"]})
    rej = [r for r in rows if r["code"] == "J1744" and r["validation_status"].startswith("rejected")]
    return len(rej) == 1, (rej[0]["validation_status"] if rej else "J1744 NOT rejected")


def s04(tools):
    def jset(ver):
        return {r["code"] for r in _rows(tools, {"drug": "Leucovorin", "substance": "LEUCOVORIN"}, ver)
                if r["code_system"] == "HCPCS"}
    j07, j10 = jset("2026-07"), jset("2026-10")
    return ("J0640" in j07 and "J0640" not in j10 and "J0643" in j10), \
        f"2026-07={sorted(j07)} 2026-10={sorted(j10)}"


def s05(tools):
    rows = _rows(tools, {"drug": "Takhzyro", "substance": "LANADELUMAB"})
    chans = {r["benefit_channel"] for r in rows}
    return chans == {"both"}, f"channels={sorted(chans)}"


def s06(tools):
    rows = _rows(tools, {"drug": "Andembry", "substance": "GARADACIMAB"})
    hc = [r for r in rows if r["code_system"] == "HCPCS"]
    ndc = [r for r in rows if r["code_system"] == "NDC"]
    return (not hc and len(ndc) >= 1), f"HCPCS={len(hc)} (want 0); NDC={len(ndc)}"


def s07(tools):
    hae = tools.icd_lookup(code="D84.1")
    conf = tools.icd_lookup(code="T78.3XXA")
    hae_ok = hae and "complement" in hae[0]["description"].lower()
    distinct = bool(conf) and (not hae or conf[0]["description"] != hae[0]["description"])
    return (hae_ok and distinct), f"D84.1={hae[0]['description'] if hae else None!r}; distinct={distinct}"


def s08(tools):
    rows = _rows(tools, {"drug": "Takhzyro", "substance": "LANADELUMAB"})
    j = [r for r in rows if r["code"] == "J0593" and r["code_system"] == "HCPCS"]
    unit = j[0]["billing_unit"] if j else ""
    return (bool(j) and "150" in str(unit)), f"J0593 billing_unit={unit!r}"


def s09(tools):
    def jset(ver):
        return {r["code"] for r in _rows(tools, {"drug": "Takhzyro", "substance": "LANADELUMAB"}, ver)
                if r["code_system"] == "HCPCS"}
    return ("J0593" in jset("2026-07") and "J0593" in jset("2026-10")), "pinned both quarters"


def s10(tools):
    rows = _rows(tools, {"drug": "Kalbitor", "substance": "ECALLANTIDE"})
    j = [r for r in rows if r["code"] == "J1290" and r["code_system"] == "HCPCS"]
    return (bool(j) and j[0]["benefit_channel"] == "medical"), \
        f"J1290 channel={j[0]['benefit_channel'] if j else None}"


def s11(tools):
    rows = _rows(tools, {"drug": "Notadrug", "substance": "XYZZYNOTREAL"})
    return (len(rows) == 1 and rows[0]["validation_status"].startswith("rejected")), \
        f"rows={len(rows)}; status={rows[0]['validation_status'] if rows else None}"


SCENARIOS = [
    ("E02-1", "Drug with many NDCs -> one J-code", s01, True),
    ("E02-2", "Formulation with no J-code", s02, True),
    ("E02-3", "Fabricated/retired code proposed by model", s03, True),
    ("E02-4", "Mapping changed between crosswalk quarters", s04, True),
    ("E02-5", "Self-administered injectable on both channels", s05, True),
    ("E02-6", "Recently approved drug, no permanent J-code", s06, True),
    ("E02-7", "Confusable disease term (D84.1 vs T78.3)", s07, True),
    ("E02-8", "BR-4 billing-unit math (Takhzyro 150)", s08, False),
    ("E02-9", "Effective-date boundary (version pinned)", s09, False),
    ("E02-10", "BR-6 new rule (Kalbitor -> medical/J1290)", s10, False),
    ("E02-11", "Empty / no-candidate disease", s11, False),
]


def run_suite(tools: ReferenceTools | None = None) -> dict:
    tools = tools or ReferenceTools()
    results, n_pass, n_req_fail = [], 0, 0
    for sid, name, fn, required in SCENARIOS:
        try:
            passed, detail = fn(tools)
        except Exception as e:  # noqa: BLE001
            passed, detail = False, f"ERROR {type(e).__name__}: {e}"
        n_pass += bool(passed)
        n_req_fail += (required and not passed)
        results.append({"id": sid, "name": name, "required": required,
                        "passed": bool(passed), "detail": detail})
    return {"version": V, "total": len(SCENARIOS), "passed": n_pass,
            "required_failures": n_req_fail, "results": results}
