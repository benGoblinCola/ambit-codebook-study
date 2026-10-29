"""Streamlit review dashboard (R08 / Principle 04) -- the human approval step.

Browse codebook versions, see every code with its channel / billing unit / claims
frequency / validation status and evidence, review the summary + eval gate, and
Approve (freeze) or Reject a draft. Reads and writes Aurora through the same modules the
pipeline uses, under the least-privilege codebook_app role.

    PYTHONPATH=src streamlit run src/ambit_codebook/review_app.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `src` importable

import streamlit as st  # noqa: E402

from ambit_codebook.config import get_config  # noqa: E402
from ambit_codebook.db import Db  # noqa: E402
from ambit_codebook.review import set_review  # noqa: E402


@st.cache_resource
def _db() -> Db:
    return Db(secret_arn=get_config().resolve_secret_arn)


def _versions(db: Db) -> list[dict]:
    return db.execute(
        "SELECT codebook_id, codebook_version, disease_area, status, reviewed_by, "
        "reviewed_at, reference_versions FROM codebook_version "
        "ORDER BY codebook_id, codebook_version DESC"
    )


def _rows(db: Db, cid: str, ver: int) -> list[dict]:
    return db.execute(
        "SELECT code, code_system, description, group_name, related_code, benefit_channel, "
        "billing_unit, validation_status, claims_frequency, evidence "
        "FROM codebook WHERE codebook_id = :i AND codebook_version = :v "
        "ORDER BY code_system, code",
        {"i": cid, "v": ver},
    )


st.set_page_config(page_title="Codebook Review", layout="wide")
st.title("Medical Codebook — Review & Approve")
st.caption("AI proposes · reference data decides · a person approves")

db = _db()
versions = _versions(db)
if not versions:
    st.info("No codebooks yet. Run the pipeline: `python -m ambit_codebook.pipeline \"hereditary angioedema\"`")
    st.stop()

labels = {f"{v['codebook_id']}  v{v['codebook_version']}  [{v['status']}]": v for v in versions}
choice = st.sidebar.selectbox("Codebook version", list(labels))
v = labels[choice]
cid, ver = v["codebook_id"], v["codebook_version"]

st.subheader(f"{v['disease_area']} — version {ver}")
meta = f"**status:** {v['status']} · **reference versions:** {v['reference_versions']}"
if v.get("reviewed_by"):
    meta += f" · reviewed by {v['reviewed_by']} at {v['reviewed_at']}"
st.markdown(meta)

rows = _rows(db, cid, ver)
valid = sum(1 for r in rows if r["validation_status"] == "valid")
rejected = sum(1 for r in rows if str(r["validation_status"]).startswith("rejected"))
flagged = sum(1 for r in rows if str(r["validation_status"]).startswith("flagged"))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Rows", len(rows))
c2.metric("Valid", valid)
c3.metric("Rejected", rejected)
c4.metric("Flagged", flagged)
st.write("**Channels:**", dict(Counter(r["benefit_channel"] for r in rows if r["benefit_channel"])))

st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Review")
reviewer = st.text_input("Reviewer name", "Dr. Reviewer")
col_a, col_r = st.columns(2)
if col_a.button("✅ Approve & freeze version", disabled=(v["status"] == "approved")):
    set_review(cid, ver, "approved", reviewer)
    st.success(f"Approved {cid} v{ver} — version frozen.")
    st.rerun()
if col_r.button("❌ Reject"):
    set_review(cid, ver, "rejected", reviewer)
    st.warning(f"Rejected {cid} v{ver}.")
    st.rerun()
