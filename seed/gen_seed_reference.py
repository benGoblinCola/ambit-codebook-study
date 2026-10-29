"""
Generate Ambit HAE seed tables from the data pack.
- hae_product_admin.csv : NDC-derived product facts + analyst-owned self_administered flag.
- billing_rules_extended.csv : BR-1..BR-5 (unchanged) + BR-6 (new).

NOTE: this is the generator we would wire into the pipeline. In production the
candidate drug list (DISEASE_SUBSTANCES / DISEASE_BRANDS) comes from the grounded
research agent for whatever disease area is requested -- it is NOT hard-coded to HAE.
The self_administered column is analyst-owned reference data, layered on top of the
deterministic NDC/crosswalk facts; it is never inferred by the model or from claims.
"""
import csv, sys

DATA = sys.argv[1].rstrip('/') + '/'
OUT  = sys.argv[2].rstrip('/') + '/'
CROSSWALK_VERSION = '2026-10'   # pinned per BR-5

# --- disease candidate set (in prod: from the research agent) ---
DISEASE_SUBSTANCES = ['lanadelumab','icatibant','berotralstat','sebetralstat',
                      'ecallantide','conestat','garadacimab','donidalorsen','c1-esterase','c1 esterase']
DISEASE_BRANDS = {'berinert','haegarda','cinryze','ruconest','orladeyo','ekterly',
                  'takhzyro','kalbitor','andembry','dawnzera','firazyr','sajazir','icatibant'}

# --- analyst-owned self-administration flag (curated; the BR-3 "decide how" answer) ---
SELF_ADMIN_BY_SUBSTANCE = {
    'lanadelumab':'Y','icatibant':'Y','berotralstat':'Y','sebetralstat':'Y',
    'garadacimab':'Y','donidalorsen':'Y','ecallantide':'N','conestat':'N',
}
SELF_ADMIN_BY_BRAND = {'berinert':'Y','haegarda':'Y','cinryze':'Y','ruconest':'N',
                       'takhzyro':'Y','kalbitor':'N','orladeyo':'Y','ekterly':'Y',
                       'andembry':'Y','dawnzera':'Y','firazyr':'Y','sajazir':'Y','icatibant':'Y'}

def self_admin(sub, brand):
    s = sub.lower(); b = brand.lower()
    for k,v in SELF_ADMIN_BY_SUBSTANCE.items():
        if k in s: return v, 'analyst:substance'
    if b in SELF_ADMIN_BY_BRAND: return SELF_ADMIN_BY_BRAND[b], 'analyst:brand'
    return '', 'UNSET-flag-for-reviewer'

# --- crosswalk: labeler+product key (9 digits) -> J-code, as of pinned version ---
def ndc9(ndc):
    """labeler(5)+product(4) key from a dashed NDC, handling 5-4-2 / 5-3 / 4-4 forms."""
    p = ndc.split('-')
    if len(p) == 3:   # 11-digit package NDC (crosswalk): labeler-product-package
        return p[0].zfill(5) + p[1].zfill(4)
    if len(p) == 2:   # 10-digit product NDC (directory): labeler-product
        return p[0].zfill(5) + p[1].zfill(4)
    return ndc.replace('-', '').zfill(9)[:9]

prod_jcode = {}
with open(f'{DATA}crosswalk_{CROSSWALK_VERSION}.csv', encoding='latin-1') as f:
    r = csv.reader(f); hdr = False
    for row in r:
        if row and row[0] == '_2026_CODE': hdr = True; continue
        if hdr and row and row[0]:
            prod_jcode.setdefault(ndc9(row[3]), row[0])

# --- product file: filter to disease candidates, emit scaffold ---
rows = []
with open(f'{DATA}ndc/product.txt', encoding='latin-1') as f:
    r = csv.reader(f, delimiter='\t'); h = {x:i for i,x in enumerate(next(r))}
    seen = set()
    for row in r:
        sub   = row[h['SUBSTANCENAME']]
        brand = row[h['PROPRIETARYNAME']].strip()
        hit = any(t in sub.lower() for t in DISEASE_SUBSTANCES) or brand.lower() in DISEASE_BRANDS
        if not hit: continue
        pn = row[h['PRODUCTNDC']]
        if (pn, brand) in seen: continue
        seen.add((pn, brand))
        jcode = prod_jcode.get(ndc9(pn), '')
        sa, src = self_admin(sub, brand)
        rows.append({
            'product_ndc': pn, 'brand': brand,
            'nonproprietary': row[h['NONPROPRIETARYNAME']].strip(),
            'substance': sub.strip(),
            'dosage_form': row[h['DOSAGEFORMNAME']].strip(),
            'route': row[h['ROUTENAME']].strip(),
            'start_marketing_date': row[h['STARTMARKETINGDATE']].strip(),
            f'crosswalk_jcode_{CROSSWALK_VERSION}': jcode,
            'self_administered': sa, 'self_admin_source': src,
        })

rows.sort(key=lambda d: (d['route'] or 'ZZZ', d['brand']))
cols = ['product_ndc','brand','nonproprietary','substance','dosage_form','route',
        'start_marketing_date',f'crosswalk_jcode_{CROSSWALK_VERSION}','self_administered','self_admin_source']
with open(f'{OUT}hae_product_admin.csv','w',newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

# --- billing rules: original 5 + BR-6 ---
BR = [
 ['rule_id','rule_type','match_route','match_dosage_form','match_self_administered','then_channel','then_expects_hcpcs','then_action','note'],
 ['BR-1','channel','ORAL','TABLET|CAPSULE|SOLUTION|PELLET','','pharmacy','no','match by NDC','Oral products are dispensed by pharmacies and appear on pharmacy claims.'],
 ['BR-2','channel','INTRAVENOUS','','','medical','yes','count in the HCPCS billing unit','Infusions are given by a provider and billed on medical claims.'],
 ['BR-3','channel','SUBCUTANEOUS','','Y','both','either','track NDCs and the HCPCS code if one exists','Self-administration status is not in the reference files; decide how to determine it and document your choice.'],
 ['BR-4','units','','','','','','billing_units_per_package = package_strength / hcpcs_unit','Example: a 300 mg package on a 1 mg code is 300 billing units.'],
 ['BR-5','versioning','','','','','','use the crosswalk version in effect for the analysis period','Never the latest file by default.'],
 ['BR-6','channel','SUBCUTANEOUS','','N','medical','yes','count in the HCPCS billing unit','NEW. Subcutaneous drugs administered by a provider (not self-administered) bill on medical claims under a J-code. Complements BR-3 (SC self-administered). Witness: ecallantide/Kalbitor -> J1290.'],
]
with open(f'{OUT}billing_rules_extended.csv','w',newline='') as f:
    csv.writer(f).writerows(BR)

print(f'wrote {len(rows)} product rows -> {OUT}hae_product_admin.csv')
print(f'wrote 6 billing rules -> {OUT}billing_rules_extended.csv')
# quick console view
print('\nproduct_admin preview (brand | route | jcode | self_admin | source):')
for d in rows:
    print(f"  {d['brand'][:12]:<13}{(d['route'] or '(none)'):<13}{d['crosswalk_jcode_2026-10'] or '-':<7}{d['self_administered'] or '?':<3}{d['self_admin_source']}")
