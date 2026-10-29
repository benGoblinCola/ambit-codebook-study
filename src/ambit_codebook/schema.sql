-- Reference + output schema for the Ambit codebook generator (HAE slice).
-- Principle 03 (versioned reference data): every reference row carries a
-- reference_version plus effective/end dates, so any codebook is reproducible.
-- Principle 05 (least privilege): PHI-bearing claims live in the `restricted`
-- schema; everything else sees only the aggregate `claims_frequency` view.

-- Bulk-load the large FDA NDC file straight from S3 (fast, and keeps big files out
-- of application memory). The S3-read role is associated with the cluster by CDK.
CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE;

-- NDC normalization in SQL, used when transforming S3-imported rows.
CREATE OR REPLACE FUNCTION normalize_ndc11(code TEXT) RETURNS TEXT AS $$
DECLARE parts TEXT[];
BEGIN
    IF code IS NULL OR position('-' in code) = 0 THEN RETURN NULL; END IF;
    parts := string_to_array(code, '-');
    IF array_length(parts, 1) <> 3 THEN RETURN NULL; END IF;
    RETURN lpad(parts[1], 5, '0') || lpad(parts[2], 4, '0') || lpad(parts[3], 2, '0');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION normalize_product_ndc9(code TEXT) RETURNS TEXT AS $$
DECLARE parts TEXT[];
BEGIN
    IF code IS NULL OR position('-' in code) = 0 THEN RETURN NULL; END IF;
    parts := string_to_array(code, '-');
    IF array_length(parts, 1) < 2 THEN RETURN NULL; END IF;
    RETURN lpad(parts[1], 5, '0') || lpad(parts[2], 4, '0');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------------------------------
-- Version registry: one row per (reference set, release).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reference_release (
    ref_set        TEXT NOT NULL,            -- crosswalk | hcpcs | ndc | icd10cm
    version_label  TEXT NOT NULL,            -- e.g. 2026-07, 2026-10, 2026-10-19, fy2027
    effective_date DATE,
    end_date       DATE,
    source_uri     TEXT,                      -- s3://... of the landed raw file
    row_count      INTEGER,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ref_set, version_label)
);

-- ---------------------------------------------------------------------------
-- ASP NDC-HCPCS crosswalk (Medicare Part B drugs only), per quarter.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crosswalk (
    reference_version TEXT NOT NULL,          -- 2026-07 | 2026-10
    hcpcs_code        TEXT NOT NULL,          -- the J-code (_2026_CODE)
    short_description TEXT,
    labeler_name      TEXT,
    ndc_raw           TEXT,                    -- as published (hyphenated 5-4-2 etc.)
    ndc11             TEXT,                    -- normalized 11-digit
    drug_name         TEXT,
    hcpcs_dosage      TEXT,
    pkg_size          NUMERIC,
    pkg_qty           NUMERIC,
    billunits         NUMERIC,
    billunitspkg      NUMERIC,
    effective_date    DATE,
    end_date          DATE
);
CREATE INDEX IF NOT EXISTS ix_xwalk_ndc ON crosswalk (ndc11, reference_version);
CREATE INDEX IF NOT EXISTS ix_xwalk_hcpcs ON crosswalk (hcpcs_code, reference_version);

-- ---------------------------------------------------------------------------
-- FDA NDC Directory: product + package.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ndc_product (
    reference_version    TEXT NOT NULL,
    product_id           TEXT,
    product_ndc          TEXT NOT NULL,
    product_ndc9         TEXT,               -- normalized labeler-product, for joins
    product_type         TEXT,
    proprietary_name     TEXT,
    nonproprietary_name  TEXT,
    dosage_form          TEXT,
    route                TEXT,
    start_marketing_date TEXT,
    marketing_category   TEXT,
    application_number   TEXT,
    labeler_name         TEXT,
    substance_name       TEXT,
    active_strength      TEXT,
    active_unit          TEXT,
    pharm_classes        TEXT,
    dea_schedule         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ndcprod_sub ON ndc_product (lower(substance_name));
CREATE INDEX IF NOT EXISTS ix_ndcprod_prop ON ndc_product (lower(proprietary_name));
CREATE INDEX IF NOT EXISTS ix_ndcprod_ndc9 ON ndc_product (product_ndc9);

-- (NDC package file is not loaded for the drug slice: the crosswalk is already
--  NDC-level for Part B, and ndc_product gives route/form for non-Part-B products.)

-- ---------------------------------------------------------------------------
-- HCPCS Level II (J-codes etc.), per quarter, with effective/termination.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hcpcs (
    reference_version TEXT NOT NULL,
    code              TEXT NOT NULL,
    short_description TEXT,
    long_description  TEXT,
    add_date          DATE,
    action_effective_date DATE,
    termination_date  DATE,
    action_code       TEXT,
    coverage_code     TEXT
);
CREATE INDEX IF NOT EXISTS ix_hcpcs_code ON hcpcs (code, reference_version);

-- ---------------------------------------------------------------------------
-- ICD-10-CM diagnosis codes (stretch goal; loaded for D84.1 / T78.3 eval case).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS icd10cm (
    reference_version TEXT NOT NULL,
    code              TEXT NOT NULL,
    description       TEXT,
    effective_date    DATE
);
CREATE INDEX IF NOT EXISTS ix_icd_code ON icd10cm (code, reference_version);

-- ---------------------------------------------------------------------------
-- Billing rules: analyst-owned DATA (Principle 01). Loaded from CSV, not prompts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_rule (
    rule_id                 TEXT PRIMARY KEY,
    rule_type               TEXT,             -- channel | units | versioning
    match_route             TEXT,
    match_dosage_form       TEXT,
    match_self_administered TEXT,
    then_channel            TEXT,             -- pharmacy | medical | both
    then_expects_hcpcs      TEXT,             -- yes | no | either
    then_action             TEXT,
    note                    TEXT,
    sort_order              INTEGER
);

-- ---------------------------------------------------------------------------
-- Analyst-owned self-administration reference (answers BR-3's planted question).
-- Self-admin status is NOT in the reference files and is NEVER inferred from the
-- model or from claims; it is curated data, governed like billing_rule.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_admin (
    product_ndc9         TEXT,               -- labeler-product (9-digit), from the seed
    brand                TEXT,
    nonproprietary       TEXT,
    substance            TEXT,
    dosage_form          TEXT,
    route                TEXT,
    start_marketing_date TEXT,
    crosswalk_jcode      TEXT,
    self_administered    BOOLEAN,
    source               TEXT
);
CREATE INDEX IF NOT EXISTS ix_padmin_sub ON product_admin (lower(substance));
CREATE INDEX IF NOT EXISTS ix_padmin_ndc ON product_admin (product_ndc9);

-- ---------------------------------------------------------------------------
-- PHI-bearing claims: RESTRICTED. The app role is never granted access here.
-- (Synthetic data, handled as if real.)
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS restricted;
-- Columns mirror claims_2026.csv exactly so the file imports straight from S3 into
-- here (PHI never passes through application code).
CREATE TABLE IF NOT EXISTS restricted.claims (
    claim_id          TEXT,
    claim_line        TEXT,
    claim_type        TEXT,                   -- PHARMACY | MEDICAL (the channel)
    member_id         TEXT,                   -- PHI
    member_first_name TEXT,                   -- PHI
    member_last_name  TEXT,                   -- PHI
    date_of_birth     TEXT,                   -- PHI
    sex               TEXT,
    member_zip        TEXT,                   -- PHI
    service_date      TEXT,
    place_of_service  TEXT,
    dx1 TEXT, dx2 TEXT, dx3 TEXT,
    hcpcs_code        TEXT,
    cpt_code          TEXT,
    ndc_11            TEXT,
    units             TEXT,
    quantity          TEXT,
    days_supply       TEXT,
    rendering_npi     TEXT
);

-- Aggregate-only exposure of claims: counts per code + channel, no PHI columns.
CREATE OR REPLACE VIEW claims_frequency AS
    SELECT 'HCPCS' AS code_system, hcpcs_code AS code, claim_type AS channel, count(*) AS claim_count
      FROM restricted.claims WHERE hcpcs_code IS NOT NULL AND hcpcs_code <> '' GROUP BY hcpcs_code, claim_type
    UNION ALL
    SELECT 'NDC', ndc_11, claim_type, count(*) FROM restricted.claims
      WHERE ndc_11 IS NOT NULL AND ndc_11 <> '' GROUP BY ndc_11, claim_type
    UNION ALL
    SELECT 'CPT', cpt_code, claim_type, count(*) FROM restricted.claims
      WHERE cpt_code IS NOT NULL AND cpt_code <> '' GROUP BY cpt_code, claim_type
    UNION ALL
    SELECT 'ICD10CM', dx, claim_type, count(*) FROM (
        SELECT dx1 AS dx, claim_type FROM restricted.claims WHERE dx1 IS NOT NULL AND dx1 <> ''
        UNION ALL SELECT dx2, claim_type FROM restricted.claims WHERE dx2 IS NOT NULL AND dx2 <> ''
        UNION ALL SELECT dx3, claim_type FROM restricted.claims WHERE dx3 IS NOT NULL AND dx3 <> ''
    ) d GROUP BY dx, claim_type;

-- ---------------------------------------------------------------------------
-- Codebook output (standard schema from the brief; extended as needed).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codebook_version (
    codebook_id        TEXT NOT NULL,
    codebook_version   INTEGER NOT NULL,
    disease_area       TEXT,
    reference_versions JSONB,
    status             TEXT DEFAULT 'draft',  -- draft | approved | rejected
    created_at         TIMESTAMPTZ DEFAULT now(),
    reviewed_by        TEXT,
    reviewed_at        TIMESTAMPTZ,
    PRIMARY KEY (codebook_id, codebook_version)
);

CREATE TABLE IF NOT EXISTS codebook (
    codebook_id         TEXT NOT NULL,
    codebook_version    INTEGER NOT NULL,
    disease_area        TEXT,
    code                TEXT,
    code_system         TEXT,                 -- ICD10CM | NDC | HCPCS | CPT
    description         TEXT,
    group_name          TEXT,                 -- drug name / therapeutic class
    related_code        TEXT,                 -- e.g. the J-code for an NDC
    benefit_channel     TEXT,                 -- medical | pharmacy | both
    billing_unit        TEXT,
    inclusion_rationale TEXT,
    evidence            TEXT,                 -- reference records supporting it
    validation_status   TEXT,                 -- valid | rejected: <reason>
    claims_frequency    INTEGER,
    reference_versions  JSONB,
    audit_trail         JSONB,               -- ordered derivation path for this code
    review_status       TEXT,
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    row_id              BIGINT GENERATED BY DEFAULT AS IDENTITY,
    PRIMARY KEY (codebook_id, codebook_version, row_id)
);
