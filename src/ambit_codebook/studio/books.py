"""Books page: pick a generated codebook, filter it, inspect audit trails, export, review."""
from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter

import streamlit as st

from ambit_codebook.review import set_review

from .shared import bucket_name, get_db, get_rows, list_versions, status_cat

_CSV_COLS = ["code", "code_system", "description", "group_name", "related_code",
             "benefit_channel", "billing_unit", "validation_status", "claims_frequency",
             "evidence", "audit_trail"]


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_CSV_COLS)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c) for c in _CSV_COLS})
    return buf.getvalue().encode("utf-8")


def _live_trace(db, row: dict) -> None:
    """Re-run the reference lookups for one code and show the actual rows at each hop."""
    code, system = row["code"], row["code_system"]
    substance = (row.get("group_name") or "").strip()
    trail = row.get("audit_trail")
    if isinstance(trail, str):
        try:
            trail = json.loads(trail)
        except json.JSONDecodeError:
            trail = []
    if trail:  # the agent.proposed node holds "Brand (SUBSTANCE) — ..."
        m = re.search(r"\(([^)]+)\)", trail[0].get("detail") or "")
        if m:
            substance = m.group(1).strip()
    refs = row.get("reference_versions")
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except json.JSONDecodeError:
            refs = {}
    version = (refs or {}).get("crosswalk", "2026-10")
    sub, brand = f"%{substance.lower()}%", f"%{(row.get('group_name') or '').lower()}%"

    st.markdown(f"**Live trace for `{code}`** — substance `{substance}`, crosswalk `{version}`")
    def grid(cap, sql, p):
        st.caption(cap)
        st.dataframe(db.execute(sql, p), use_container_width=True, hide_index=True)

    grid("① ndc_product — substance → NDCs, route, form",
         "SELECT product_ndc,proprietary_name,route,dosage_form FROM ndc_product "
         "WHERE lower(substance_name) LIKE :q ORDER BY product_ndc LIMIT 8", {"q": sub})
    grid("② product_admin — analyst self-admin flag",
         "SELECT product_ndc9,brand,route,self_administered FROM product_admin "
         "WHERE lower(substance) LIKE :q LIMIT 8", {"q": sub})
    grid(f"③ crosswalk ({version}) — NDC → J-code",
         "SELECT hcpcs_code,ndc11,drug_name,billunitspkg FROM crosswalk WHERE reference_version=:v "
         "AND (lower(drug_name) LIKE :s OR lower(drug_name) LIKE :b) LIMIT 8",
         {"v": version, "s": sub, "b": brand})
    if system == "HCPCS":
        grid(f"④ hcpcs ({version}) — validate the code",
             "SELECT code,short_description,termination_date FROM hcpcs "
             "WHERE reference_version=:v AND code=:c", {"v": version, "c": code})
    grid("⑤ claims_frequency — aggregate (no PHI)",
         "SELECT code_system,code,channel,claim_count FROM claims_frequency WHERE code=:c", {"c": code})


def render() -> None:
    st.title("Generated Books")
    db = get_db()
    versions = list_versions(db)
    if not versions:
        st.info("No books yet — generate one from the **Intake** page.")
        return

    # --- pick a book ---
    diseases = sorted({v["disease_area"] for v in versions})
    fd = st.sidebar.multiselect("Disease", diseases, default=diseases)
    fstat = st.sidebar.multiselect("Status", ["draft", "approved", "rejected"],
                                   default=["draft", "approved", "rejected"])
    picks = [v for v in versions if v["disease_area"] in fd and v["status"] in fstat]
    if not picks:
        st.info("No books match the filters.")
        return
    labels = {f"{v['disease_area']} — v{v['codebook_version']} [{v['status']}]": v for v in picks}
    choice = st.selectbox("Book", list(labels))
    v = labels[choice]
    cid, ver = v["codebook_id"], v["codebook_version"]

    meta = f"**status:** {v['status']} · **reference versions:** {v['reference_versions']}"
    if v.get("reviewed_by"):
        meta += f" · reviewed by {v['reviewed_by']} at {v['reviewed_at']}"
    st.markdown(meta)

    rows = get_rows(db, cid, ver)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", len(rows))
    c2.metric("Valid", sum(1 for r in rows if r["validation_status"] == "valid"))
    c3.metric("Rejected", sum(1 for r in rows if status_cat(r["validation_status"]) == "rejected"))
    c4.metric("Flagged", sum(1 for r in rows if status_cat(r["validation_status"]) == "flagged"))
    st.write("**Channels:**", dict(Counter(r["benefit_channel"] for r in rows if r["benefit_channel"])))

    # --- export ---
    ec1, ec2 = st.columns([1, 3])
    ec1.download_button("⬇️ Export CSV", _csv_bytes(rows),
                        file_name=f"{cid}_v{ver}.csv", mime="text/csv",
                        use_container_width=True)
    if bucket_name():
        ec2.caption(f"Published: `s3://{bucket_name()}/codebooks/{cid}/v{ver}.csv`")

    # --- in-book filters ---
    st.sidebar.markdown("### Filter codes")
    f_system = st.sidebar.multiselect("Code system", ["HCPCS", "NDC", "ICD10CM", "CPT"],
                                      default=["HCPCS", "NDC"])
    f_channel = st.sidebar.multiselect("Channel", ["medical", "pharmacy", "both"],
                                       default=["medical", "pharmacy", "both"])
    f_status2 = st.sidebar.multiselect("Validation", ["valid", "rejected", "flagged"],
                                       default=["valid", "rejected", "flagged"])
    search = st.sidebar.text_input("Search code / description / drug").strip().lower()
    filtered = [
        r for r in rows
        if r["code_system"] in f_system
        and (r["benefit_channel"] in f_channel or (r["benefit_channel"] is None and "both" in f_channel))
        and status_cat(r["validation_status"]) in f_status2
        and (not search or search in " ".join(
            str(r.get(k) or "") for k in ("code", "description", "group_name")).lower())
    ]
    st.divider()
    st.caption(f"Showing {len(filtered)} of {len(rows)} codes")
    st.dataframe([{k: val for k, val in r.items() if k != "audit_trail"} for r in filtered],
                 use_container_width=True, hide_index=True)

    # --- audit trail (node-path tracker) ---
    st.subheader("Audit trail")
    st.caption("The ordered path a code travelled: agent → reference lookups → validation → "
               "billing rule → claims → decision.")
    codes = [r["code"] for r in filtered if r.get("code")]
    if codes:
        pick = st.selectbox("Trace a code", codes)
        row = next((r for r in filtered if r.get("code") == pick), None)
        trail = row.get("audit_trail") if row else None
        if isinstance(trail, str):
            try:
                trail = json.loads(trail)
            except json.JSONDecodeError:
                trail = []
        # Laid out as a table (not a stacked list) so the whole path reads at a glance.
        st.table([{"step": i + 1, "node": s.get("node"), "detail": s.get("detail")}
                  for i, s in enumerate(trail or [])])
        if st.toggle("Show the live table trace (actual rows at each hop)") and row:
            _live_trace(db, row)
    else:
        st.caption("No codes match the current filters.")

    # --- review ---
    st.divider()
    st.subheader("Review")
    reviewer = st.text_input("Reviewer name", "Dr. Reviewer")
    a, r = st.columns(2)
    if a.button("✅ Approve & freeze version", disabled=(v["status"] == "approved"),
                use_container_width=True):
        set_review(cid, ver, "approved", reviewer)
        st.success(f"Approved {cid} v{ver} — frozen.")
        st.rerun()
    if r.button("❌ Reject", use_container_width=True):
        set_review(cid, ver, "rejected", reviewer)
        st.warning(f"Rejected {cid} v{ver}.")
        st.rerun()
