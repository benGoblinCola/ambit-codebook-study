"""Intake page: what the system does + a chat portal (with disease autocomplete).

Submitting a disease starts a real Step Functions execution; watch it on the Pipeline page.
"""
from __future__ import annotations

import json

import streamlit as st

from .shared import sfn_client, state_machine_arn

# Autocomplete suggestions. Free text is still accepted (the agent handles any disease);
# swap this for the loaded ICD-10-CM descriptions to make it exhaustive.
COMMON_DISEASES = sorted([
    "hereditary angioedema", "rheumatoid arthritis", "psoriatic arthritis", "ankylosing spondylitis",
    "type 2 diabetes mellitus", "type 1 diabetes mellitus", "asthma",
    "chronic obstructive pulmonary disease", "Crohn's disease", "ulcerative colitis", "psoriasis",
    "atopic dermatitis", "multiple sclerosis", "systemic lupus erythematosus", "migraine",
    "epilepsy", "Parkinson's disease", "Alzheimer's disease", "breast cancer", "lung cancer",
    "colorectal cancer", "prostate cancer", "multiple myeloma", "chronic lymphocytic leukemia",
    "non-Hodgkin lymphoma", "melanoma", "hemophilia A", "hemophilia B", "sickle cell disease",
    "cystic fibrosis", "pulmonary arterial hypertension", "chronic kidney disease", "heart failure",
    "atrial fibrillation", "hypertension", "hyperlipidemia", "osteoporosis", "gout",
    "hypothyroidism", "HIV", "hepatitis C", "hepatitis B", "growth hormone deficiency",
    "age-related macular degeneration", "glaucoma", "schizophrenia", "major depressive disorder",
    "bipolar disorder", "Gaucher disease", "Fabry disease", "spinal muscular atrophy",
    "amyotrophic lateral sclerosis",
])


def _disease_picker() -> str | None:
    try:  # Streamlit >=1.47 supports typing values not in the list
        return st.selectbox(
            "Disease area", options=COMMON_DISEASES, index=None,
            placeholder="Type to search a disease, or enter your own…",
            accept_new_options=True, label_visibility="collapsed",
        )
    except TypeError:  # older Streamlit: picker for suggestions + free-text fallback
        pick = st.selectbox("Disease area", options=["— choose —", *COMMON_DISEASES],
                            label_visibility="collapsed")
        other = st.text_input("…or enter another disease")
        return other.strip() or (None if pick == "— choose —" else pick)


def render() -> None:
    st.title("Medical Codebook — Intake")
    st.markdown(
        """
Every healthcare-analytics project starts by building a **codebook**: the diagnosis, drug,
procedure, and billing codes relevant to a disease. This tool drafts one for you.

**AI proposes · reference data decides · a person approves.** A Bedrock research agent
proposes candidate drugs for the disease (grounded in the FDA/CMS reference data); then
deterministic logic resolves each drug → NDCs → J-code through the Medicare ASP crosswalk
*as of a pinned version*, validates every code, applies the analyst-owned billing rules,
flags products with no J-code, adds aggregate claims frequency, and records an **audit
trail** for each code. The result is a reviewer-approvable draft.

Pick or type a disease area below to build a codebook. It runs as a **Step Functions**
pipeline — open **Pipeline (live)** to watch it, then **Books** to review and export it.
        """
    )
    st.divider()
    st.subheader("Discussion")

    for m in st.session_state.get("chat", []):
        st.chat_message(m["role"]).markdown(m["text"])

    col1, col2 = st.columns([5, 1])
    with col1:
        disease = _disease_picker()
    with col2:
        go = st.button("Build", type="primary", use_container_width=True)

    if not (go and disease):
        return

    st.session_state.setdefault("chat", []).append({"role": "user", "text": disease})
    arn = state_machine_arn()
    if not arn:
        st.session_state.chat.append({"role": "assistant",
                                      "text": "⚠️ Step Functions state machine not found — deploy the stack first."})
        st.rerun()

    ex = sfn_client().start_execution(
        stateMachineArn=arn,
        input=json.dumps({"disease_area": disease, "version": "2026-10"}),
    )
    st.session_state.exec_arn = ex["executionArn"]
    st.session_state.exec_disease = disease
    st.session_state.chat.append({
        "role": "assistant",
        "text": (f"Started a pipeline run for **{disease}**. "
                 f"Open **Pipeline (live)** to watch it build, then **Books** to review the result."),
    })
    st.rerun()
