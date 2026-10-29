"""Parsers for the data pack's custom-format files.

The two giant FDA NDC files (product.txt, package.txt) and the synthetic claims CSV are
bulk-loaded straight from S3 by the loader, so they are not parsed here. These parsers
cover the files that need real work: the latin-1 crosswalk with junk header rows, the
fixed-width HCPCS layout, the undotted ICD codes, and the analyst-owned seed CSVs.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .normalize import (
    QUARTER_RANGES,
    icd_dotted,
    normalize_ndc11,
    normalize_product_ndc9,
    parse_yyyymmdd,
    to_number,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data-pack" / "data-pack"
SEED_ROOT = REPO_ROOT / "seed"


# --- Crosswalk (ASP NDC-HCPCS) ---------------------------------------------
def parse_crosswalk(version: str) -> list[dict]:
    path = DATA_ROOT / "reference" / "crosswalk" / version / f"asp_ndc_hcpcs_crosswalk_{version}.csv"
    eff, end = QUARTER_RANGES[version]
    rows: list[dict] = []
    with open(path, encoding="latin-1", newline="") as f:
        reader = csv.reader(f)
        header_seen = False
        for rec in reader:
            if not header_seen:
                if rec and rec[0].strip() == "_2026_CODE":
                    header_seen = True
                continue
            if not rec or not rec[0].strip():
                continue
            c = (rec + [""] * 10)[:10]
            rows.append(
                dict(
                    reference_version=version,
                    hcpcs_code=c[0].strip(),
                    short_description=c[1].strip(),
                    labeler_name=c[2].strip(),
                    ndc_raw=c[3].strip(),
                    ndc11=normalize_ndc11(c[3]),
                    drug_name=c[4].strip(),
                    hcpcs_dosage=c[5].strip(),
                    pkg_size=to_number(c[6]),
                    pkg_qty=to_number(c[7]),
                    billunits=to_number(c[8]),
                    billunitspkg=to_number(c[9]),
                    effective_date=eff,
                    end_date=end,
                )
            )
    return rows


# --- HCPCS Level II (fixed width) ------------------------------------------
def _hcpcs_file(version: str) -> Path:
    d = DATA_ROOT / "reference" / "hcpcs" / version
    files = sorted(d.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"no HCPCS .txt in {d}")
    return files[0]


def parse_hcpcs(version: str) -> list[dict]:
    """Fixed-width per HCPC2026_recordlayout.txt (1-indexed positions):
    code 1-5, record-ident 11, long desc 12-91, short desc 92-119, coverage 230,
    add-date 269-276, action-eff 277-284, termination 285-292, action 293.
    Record-ident '3' is the first (detail) line of a code; '4' lines are description
    continuations, which we skip.
    """
    eff, end = QUARTER_RANGES[version]
    rows: list[dict] = []
    with open(_hcpcs_file(version), encoding="latin-1") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if len(line) < 320:
                line = line.ljust(320)
            if line[10:11] != "3":
                continue
            code = line[0:5].strip()
            if not code:
                continue
            rows.append(
                dict(
                    reference_version=version,
                    code=code,
                    long_description=line[11:91].strip(),
                    short_description=line[91:119].strip(),
                    coverage_code=line[229:230].strip(),
                    add_date=parse_yyyymmdd(line[268:276]),
                    action_effective_date=parse_yyyymmdd(line[276:284]),
                    termination_date=parse_yyyymmdd(line[284:292]),
                    action_code=line[292:293].strip(),
                    effective_date=eff,
                    end_date=end,
                )
            )
    return rows


# --- ICD-10-CM codes (undotted "A000  desc") -------------------------------
def parse_icd10(version: str = "fy2027") -> list[dict]:
    path = DATA_ROOT / "reference" / "icd10cm" / version / "icd10cm_codes_2027.txt"
    rows: list[dict] = []
    with open(path, encoding="latin-1") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            code, _, desc = line.partition(" ")
            rows.append(
                dict(
                    reference_version=version,
                    code=icd_dotted(code.strip()),
                    description=desc.strip(),
                    effective_date="2026-10-01",
                )
            )
    return rows


# --- Synthetic CPT ----------------------------------------------------------
def parse_cpt() -> list[dict]:
    path = DATA_ROOT / "synthetic" / "cpt_synthetic.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [
            dict(reference_version="synthetic", code=r["cpt_code"].strip(),
                 description=r.get("description", "").strip(),
                 category=r.get("category", "").strip())
            for r in csv.DictReader(f)
        ]


# --- FDA NDC product directory (tab-delimited, latin-1) ---------------------
def parse_ndc_product(version: str = "2026-10-19") -> list[dict]:
    """Parse product.txt into rows keyed for join by product_ndc9.

    QUOTE_NONE: the FDA files use tab delimiters and never quote fields, and some
    names contain a bare double-quote, so quote processing must be off.
    """
    path = DATA_ROOT / "reference" / "ndc" / version / "ndctext" / "product.txt"
    if not path.exists():  # extract from the shipped ndctext.zip on first use
        import zipfile
        zp = path.parent.parent / "ndctext.zip"
        if zp.exists():
            zipfile.ZipFile(zp).extractall(path.parent)
    rows: list[dict] = []
    with open(path, encoding="latin-1", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader)
        idx = {name.strip(): i for i, name in enumerate(header)}

        def g(rec: list[str], name: str) -> str:
            i = idx.get(name)
            return rec[i].strip() if i is not None and i < len(rec) else ""

        for rec in reader:
            if not rec:
                continue
            pndc = g(rec, "PRODUCTNDC")
            rows.append(
                dict(
                    reference_version=version,
                    product_id=g(rec, "PRODUCTID"),
                    product_ndc=pndc,
                    product_ndc9=normalize_product_ndc9(pndc),
                    product_type=g(rec, "PRODUCTTYPENAME"),
                    proprietary_name=g(rec, "PROPRIETARYNAME"),
                    nonproprietary_name=g(rec, "NONPROPRIETARYNAME"),
                    dosage_form=g(rec, "DOSAGEFORMNAME"),
                    route=g(rec, "ROUTENAME"),
                    start_marketing_date=g(rec, "STARTMARKETINGDATE"),
                    marketing_category=g(rec, "MARKETINGCATEGORYNAME"),
                    application_number=g(rec, "APPLICATIONNUMBER"),
                    labeler_name=g(rec, "LABELERNAME"),
                    substance_name=g(rec, "SUBSTANCENAME"),
                    active_strength=g(rec, "ACTIVE_NUMERATOR_STRENGTH"),
                    active_unit=g(rec, "ACTIVE_INGRED_UNIT"),
                    pharm_classes=g(rec, "PHARM_CLASSES"),
                    dea_schedule=g(rec, "DEASCHEDULE"),
                )
            )
    return rows


# --- Synthetic claims (PHI-like; loaded only by the trusted admin loader) ---
CLAIMS_COLS = [
    "claim_id", "claim_line", "claim_type", "member_id", "member_first_name",
    "member_last_name", "date_of_birth", "sex", "member_zip", "service_date",
    "place_of_service", "dx1", "dx2", "dx3", "hcpcs_code", "cpt_code", "ndc_11",
    "units", "quantity", "days_supply", "rendering_npi",
]


def parse_claims() -> list[dict]:
    path = DATA_ROOT / "synthetic" / "claims_2026.csv"
    rows: list[dict] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({c: (r.get(c) or None) for c in CLAIMS_COLS})
    return rows


# --- Analyst-owned seeds ----------------------------------------------------
def parse_billing_rules() -> list[dict]:
    path = SEED_ROOT / "billing_rules_extended.csv"
    rows: list[dict] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            rows.append(
                dict(
                    rule_id=r["rule_id"].strip(),
                    rule_type=r["rule_type"].strip(),
                    match_route=r["match_route"].strip() or None,
                    match_dosage_form=r["match_dosage_form"].strip() or None,
                    match_self_administered=r["match_self_administered"].strip() or None,
                    then_channel=r["then_channel"].strip() or None,
                    then_expects_hcpcs=r["then_expects_hcpcs"].strip() or None,
                    then_action=r["then_action"].strip() or None,
                    note=r["note"].strip() or None,
                    sort_order=i,
                )
            )
    return rows


def parse_product_admin() -> list[dict]:
    path = SEED_ROOT / "hae_product_admin.csv"
    rows: list[dict] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            sa = r["self_administered"].strip().upper()
            rows.append(
                dict(
                    product_ndc9=normalize_product_ndc9(r["product_ndc"]),
                    brand=r["brand"].strip(),
                    nonproprietary=r.get("nonproprietary", "").strip(),
                    substance=r["substance"].strip(),
                    dosage_form=r.get("dosage_form", "").strip(),
                    route=r.get("route", "").strip(),
                    start_marketing_date=r.get("start_marketing_date", "").strip(),
                    crosswalk_jcode=r.get("crosswalk_jcode_2026-10", "").strip() or None,
                    self_administered=(True if sa == "Y" else False if sa == "N" else None),
                    source=r.get("self_admin_source", "").strip(),
                )
            )
    return rows
