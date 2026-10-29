"""Pipeline (live) page: watch the current Step Functions execution in real time."""
from __future__ import annotations

import json
import time

import streamlit as st

from .shared import sfn_client

_ICON = {"entered": "⏳", "succeeded": "✅", "failed": "❌"}
_HIDE = {"candidates", "rows"}  # large fields we summarize rather than dump


def _short(raw: str | None):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if isinstance(data, dict):
        out = {k: v for k, v in data.items() if k not in _HIDE}
        if "candidates" in data:
            out["candidates"] = f"[{len(data['candidates'])} drugs]"
        return out
    return data


def render() -> None:
    st.title("Pipeline — live Step Functions run")
    arn = st.session_state.get("exec_arn")
    if not arn:
        st.info("No run yet — start one from the **Intake** page.")
        return

    sfn = sfn_client()
    d = sfn.describe_execution(executionArn=arn)
    status = d["status"]
    st.caption(f"disease: **{st.session_state.get('exec_disease', '?')}** · "
               f"execution: **{status}**")
    st.code(arn.split(":")[-1])

    events = sfn.get_execution_history(executionArn=arn, maxResults=1000).get("events", [])
    states: list[dict] = []
    index: dict[str, dict] = {}
    failure = None
    for e in events:
        t = e["type"]
        if t.endswith("StateEntered"):
            det = e.get("stateEnteredEventDetails", {})
            s = {"name": det.get("name"), "status": "entered",
                 "input": det.get("input"), "output": None}
            index[s["name"]] = s
            states.append(s)
        elif t.endswith("StateExited"):
            det = e.get("stateExitedEventDetails", {})
            if det.get("name") in index:
                index[det["name"]]["status"] = "succeeded"
                index[det["name"]]["output"] = det.get("output")
        elif "Failed" in t:
            det = (e.get("lambdaFunctionFailedEventDetails")
                   or e.get("taskFailedEventDetails")
                   or e.get("executionFailedEventDetails") or {})
            if det:
                failure = f"{det.get('error')}: {str(det.get('cause'))[:300]}"
            for s in reversed(states):
                if s["status"] == "entered":
                    s["status"] = "failed"
                    break

    # States laid out as a flat, scannable table (no expanders).
    st.table([{"#": i + 1, "state": s["name"],
               "status": f"{_ICON.get(s['status'], '•')} {s['status']}"}
              for i, s in enumerate(states)])

    if status == "RUNNING":
        st.info("Running… this page refreshes automatically.")
        time.sleep(2)
        st.rerun()
    elif status == "SUCCEEDED":
        out = _short(d.get("output")) or {}
        ev = out.get("eval") or {}
        st.success("Pipeline succeeded — open the **Books** page to review and export.")
        c = st.columns(4)
        c[0].metric("Candidates", out.get("candidate_count", "—"))
        c[1].metric("Codebook rows", out.get("row_count", "—"))
        c[2].metric("Eval", f"{ev.get('passed_count', '—')}/{ev.get('total', '—')}")
        c[3].metric("Version", out.get("codebook_version", "—"))
        st.caption(f"Published: {out.get('published_to', '—')}")
    else:
        st.error(f"Pipeline {status}. {failure or ''}")
