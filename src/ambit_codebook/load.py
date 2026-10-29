"""One-time reference load: land + version the data pack into Aurora + S3 (E05).

Run after the foundation stack is deployed:

    python -m ambit_codebook.load

What it does, in order:
  1. Verify MANIFEST.sha256 for every file it will load (trust the data before loading).
  2. Land raw files in S3 under versioned prefixes; record a reference_release row each.
  3. Bulk-import the big FDA NDC product file and the PHI claims file straight from S3
     (claims land directly in restricted.claims -- PHI never passes through this process).
  4. Data-API-load the parsed reference tables (crosswalk, HCPCS, ICD, billing, self-admin).
  5. Create the least-privilege `codebook_app` Postgres role: it may read reference tables
     and the aggregate claims_frequency view, but is denied restricted.claims (PHI).
"""
from __future__ import annotations

import hashlib
import json
import secrets as pysecrets
import time
from pathlib import Path

import boto3

from .config import get_config
from .db import Db
from .ingest import parsers as P
from .ingest.normalize import QUARTER_RANGES

CFG = get_config()
DATA = P.DATA_ROOT
SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"

# Files that are bulk-loaded from S3 rather than parsed in-process.
NDC_PRODUCT = DATA / "reference" / "ndc" / "2026-10-19" / "ndctext" / "product.txt"
CLAIMS = DATA / "synthetic" / "claims_2026.csv"

# (ref_set, version, local_path, s3_key, effective, end)
RAW_FILES = [
    ("crosswalk", "2026-07", DATA / "reference/crosswalk/2026-07/asp_ndc_hcpcs_crosswalk_2026-07.csv", "reference/crosswalk/2026-07/crosswalk.csv"),
    ("crosswalk", "2026-10", DATA / "reference/crosswalk/2026-10/asp_ndc_hcpcs_crosswalk_2026-10.csv", "reference/crosswalk/2026-10/crosswalk.csv"),
    ("hcpcs", "2026-07", P._hcpcs_file("2026-07"), "reference/hcpcs/2026-07/hcpcs.txt"),
    ("hcpcs", "2026-10", P._hcpcs_file("2026-10"), "reference/hcpcs/2026-10/hcpcs.txt"),
    ("icd10cm", "fy2027", DATA / "reference/icd10cm/fy2027/icd10cm_codes_2027.txt", "reference/icd10cm/fy2027/codes.txt"),
    ("ndc", "2026-10-19", NDC_PRODUCT, "reference/ndc/2026-10-19/product.txt"),
    ("claims", "2026", CLAIMS, "restricted/claims/claims_2026.csv"),
]


# --------------------------------------------------------------------------
def verify_manifest() -> None:
    manifest = DATA / "MANIFEST.sha256"
    if not manifest.exists():
        print("  ! MANIFEST.sha256 not found; skipping checksum verification")
        return
    want: dict[str, str] = {}
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h, _, rel = line.partition(" ")
        want[rel.strip().lstrip("*").lstrip("./")] = h.strip()
    checked = ok = 0
    for rel, h in want.items():
        fp = DATA / rel
        if not fp.exists():
            continue
        checked += 1
        actual = hashlib.sha256(fp.read_bytes()).hexdigest()
        if actual == h:
            ok += 1
        else:
            print(f"  ! CHECKSUM MISMATCH: {rel}")
    print(f"  manifest: {ok}/{checked} files verified")


def create_schema(db: Db) -> None:
    db.execute_script(SCHEMA_SQL.read_text())
    print("  schema applied")


def upload_raw(db: Db) -> None:
    s3 = boto3.client("s3", region_name=CFG.region)
    for ref_set, version, path, key in RAW_FILES:
        if not Path(path).exists():
            print(f"  ! missing {path}; skipping upload")
            continue
        s3.upload_file(str(path), CFG.bucket, key)
        eff, end = QUARTER_RANGES.get(version, (None, None))
        db.execute(
            """INSERT INTO reference_release (ref_set, version_label, effective_date, end_date, source_uri)
               VALUES (:r, :v, CAST(:e AS DATE), CAST(:x AS DATE), :u)
               ON CONFLICT (ref_set, version_label) DO UPDATE SET source_uri = EXCLUDED.source_uri""",
            {"r": ref_set, "v": version, "e": eff, "x": end, "u": f"s3://{CFG.bucket}/{key}"},
        )
    print(f"  landed {len(RAW_FILES)} raw files in s3://{CFG.bucket}")


def _batch(db: Db, table: str, cols: list[str], rows: list[dict], date_cols=()) -> None:
    placeholders = ", ".join(
        (f"CAST(:{c} AS DATE)" if c in date_cols else f":{c}") for c in cols
    )
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    db.execute(f"TRUNCATE {table}")
    for i in range(0, len(rows), 400):
        db.batch(sql, rows[i:i + 400])
    print(f"  loaded {table}: {len(rows)} rows")


def load_parsed(db: Db) -> None:
    xw = P.parse_crosswalk("2026-07") + P.parse_crosswalk("2026-10")
    _batch(db, "crosswalk",
           ["reference_version", "hcpcs_code", "short_description", "labeler_name", "ndc_raw",
            "ndc11", "drug_name", "hcpcs_dosage", "pkg_size", "pkg_qty", "billunits",
            "billunitspkg", "effective_date", "end_date"],
           xw, date_cols={"effective_date", "end_date"})

    hc = P.parse_hcpcs("2026-07") + P.parse_hcpcs("2026-10")
    _batch(db, "hcpcs",
           ["reference_version", "code", "short_description", "long_description", "add_date",
            "action_effective_date", "termination_date", "action_code", "coverage_code"],
           hc, date_cols={"add_date", "action_effective_date", "termination_date"})

    icd = P.parse_icd10()
    _batch(db, "icd10cm", ["reference_version", "code", "description", "effective_date"],
           icd, date_cols={"effective_date"})

    _batch(db, "billing_rule",
           ["rule_id", "rule_type", "match_route", "match_dosage_form", "match_self_administered",
            "then_channel", "then_expects_hcpcs", "then_action", "note", "sort_order"],
           P.parse_billing_rules())

    _batch(db, "product_admin",
           ["product_ndc9", "brand", "nonproprietary", "substance", "dosage_form", "route",
            "start_marketing_date", "crosswalk_jcode", "self_administered", "source"],
           P.parse_product_admin())


def load_ndc_product(db: Db) -> None:
    # Parsed + Data-API batched (each batch is well under the Data API's 45s cap).
    rows = P.parse_ndc_product("2026-10-19")
    _batch(db, "ndc_product",
           ["reference_version", "product_id", "product_ndc", "product_ndc9", "product_type",
            "proprietary_name", "nonproprietary_name", "dosage_form", "route", "start_marketing_date",
            "marketing_category", "application_number", "labeler_name", "substance_name",
            "active_strength", "active_unit", "pharm_classes", "dea_schedule"],
           rows)


def _poll_count(db: Db, sql: str, target: int | None = None, tries: int = 60, delay: int = 3) -> int:
    """Poll a COUNT(*) until it hits target or stops changing (for continueAfterTimeout loads)."""
    last = -1
    for _ in range(tries):
        n = db.execute(sql)[0]["c"]
        if (target and n >= target) or (n == last and n > 0):
            return n
        last = n
        time.sleep(delay)
    return last


def load_claims(db: Db) -> None:
    # Loaded by the trusted ADMIN identity into the restricted schema. Batched via the
    # Data API (each batch < the 45s cap). The runtime codebook path (codebook_app role)
    # can never read restricted.claims -- only the aggregate claims_frequency view.
    rows = P.parse_claims()
    _batch(db, "restricted.claims", P.CLAIMS_COLS, rows)
    print("  loaded restricted.claims (PHI confined to the restricted schema)")


def create_app_role(db: Db) -> str:
    """Create/refresh the least-privilege app role and store its secret. Returns ARN."""
    pw = pysecrets.token_urlsafe(24)
    db.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='codebook_app') "
        f"THEN CREATE ROLE codebook_app LOGIN PASSWORD '{pw}'; "
        f"ELSE ALTER ROLE codebook_app WITH LOGIN PASSWORD '{pw}'; END IF; END $$;"
    )
    for stmt in [
        "GRANT CONNECT ON DATABASE codebook TO codebook_app",
        "GRANT USAGE ON SCHEMA public TO codebook_app",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO codebook_app",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO codebook_app",
        "GRANT INSERT, UPDATE ON codebook TO codebook_app",
        "GRANT INSERT, UPDATE ON codebook_version TO codebook_app",
        # Belt and suspenders: the app role is never granted the restricted schema.
        "REVOKE ALL ON SCHEMA restricted FROM codebook_app",
        "REVOKE ALL ON ALL TABLES IN SCHEMA restricted FROM codebook_app",
    ]:
        db.execute(stmt)

    sm = boto3.client("secretsmanager", region_name=CFG.region)
    secret_value = json.dumps({"username": "codebook_app", "password": pw})
    name = "ambit/codebook_app"
    try:
        arn = sm.create_secret(Name=name, SecretString=secret_value)["ARN"]
    except sm.exceptions.ResourceExistsException:
        arn = sm.put_secret_value(SecretId=name, SecretString=secret_value)["ARN"]
    print("  created least-privilege role codebook_app (denied restricted.claims)")
    return arn


def _record_app_secret(arn: str) -> None:
    out_path = Path(__file__).resolve().parents[2] / "cdk-outputs.json"
    data = {}
    if out_path.exists():
        data = json.loads(out_path.read_text())
    data.setdefault("AmbitCodebook", {})["AppSecretArn"] = arn
    out_path.write_text(json.dumps(data, indent=2))


def main() -> None:
    print(f"Loading into cluster {CFG.cluster_arn.split(':')[-1]} ({CFG.region})")
    print("[1/5] verify manifest");       verify_manifest()
    db = Db(secret_arn=CFG.secret_arn)  # admin identity for setup
    print("[2/5] schema");                create_schema(db)
    print("[3/5] land raw -> s3");        upload_raw(db)
    print("[4/5] load reference tables"); load_parsed(db); load_ndc_product(db); load_claims(db)
    print("[5/5] least-privilege role");  arn = create_app_role(db); _record_app_secret(arn)
    print("Done.")


if __name__ == "__main__":
    main()
