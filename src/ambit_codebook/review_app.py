"""Codebook Studio — a three-page Streamlit app over the live AWS pipeline.

  - Intake: explanatory text + a disease picker (autocomplete) that starts a real
    Step Functions execution.
  - Pipeline (live): watches that execution's states and input/output in real time.
  - Books: browse generated codebooks, filter, inspect per-code audit trails, export, review.

Runs under the least-privilege codebook_app role.

    PYTHONPATH=src streamlit run src/ambit_codebook/review_app.py
"""
import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `src` importable

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Codebook Studio", layout="wide")

from ambit_codebook.studio.books import render as books_page  # noqa: E402
from ambit_codebook.studio.intake import render as intake_page  # noqa: E402
from ambit_codebook.studio.pipeline_page import render as pipeline_page  # noqa: E402

def _auth_ok() -> bool:
    """Username+password gate for hosted deployments. STUDIO_USERNAME / STUDIO_PASSWORD are
    injected from Secrets Manager by App Runner; when the password is unset (local dev)
    there is no gate."""
    expected_pw = os.environ.get("STUDIO_PASSWORD")
    if not expected_pw:
        return True
    expected_user = os.environ.get("STUDIO_USERNAME", "")
    if st.session_state.get("authed"):
        return True
    st.title("Codebook Studio")
    st.caption("Sign in to continue.")
    with st.form("login"):
        user = st.text_input("Username")
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            ok_user = hmac.compare_digest(user, expected_user) if expected_user else True
            if ok_user and hmac.compare_digest(pw, expected_pw):
                st.session_state["authed"] = True
                st.rerun()
            st.error("Incorrect username or password.")
    return False


if not _auth_ok():
    st.stop()

st.navigation([
    st.Page(intake_page, title="Intake", icon="💬", url_path="intake", default=True),
    st.Page(pipeline_page, title="Pipeline (live)", icon="🔀", url_path="pipeline"),
    st.Page(books_page, title="Books", icon="📚", url_path="books"),
]).run()
