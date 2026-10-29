"""End-to-end generation pipeline (the local orchestrator; mirrors the Step Functions
states 1:1). AI proposes candidate drugs; deterministic steps resolve/validate; a draft
codebook is assembled. Runs entirely under the least-privilege app role.

    python -m ambit_codebook.pipeline "hereditary angioedema"
"""
from __future__ import annotations

import sys
from collections import Counter

from .agent.research import research_candidates
from .codebook import build_codebook, write_csv
from .reference_tools import ReferenceTools
from .resolve import resolve
from .trace import Tracer, record


def _summary(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "valid": sum(1 for r in rows if r["validation_status"] == "valid"),
        "rejected": sum(1 for r in rows if str(r["validation_status"]).startswith("rejected")),
        "flagged": sum(1 for r in rows if str(r["validation_status"]).startswith("flagged")),
        "by_channel": dict(Counter(r["benefit_channel"] for r in rows if r["benefit_channel"])),
    }


def generate(disease_area: str, version: str = "2026-10",
             tools: ReferenceTools | None = None) -> dict:
    tools = tools or ReferenceTools()
    with Tracer() as tr:
        record("pipeline", "start", disease=disease_area, version=version)
        candidates = research_candidates(disease_area, tools)        # -> ResearchCandidates
        rows = resolve(tools, disease_area, candidates, version)     # -> ResolveAndValidate
        codebook_id, ver = build_codebook(disease_area, rows, version)  # -> BuildDraft
    return {
        "codebook_id": codebook_id, "version": ver, "candidates": candidates,
        "rows": rows, "summary": _summary(rows), "trace": tr.spans,
    }


def main() -> None:
    disease = sys.argv[1] if len(sys.argv) > 1 else "hereditary angioedema"
    res = generate(disease)
    out = write_csv(res["codebook_id"], res["version"], f"out/{res['codebook_id']}_v{res['version']}.csv")
    s = res["summary"]
    print(f"\nCodebook {res['codebook_id']} v{res['version']} (draft)")
    print(f"  candidates: {len(res['candidates'])}  rows: {s['total']}  "
          f"valid: {s['valid']}  rejected: {s['rejected']}  flagged: {s['flagged']}")
    print(f"  channels: {s['by_channel']}")
    print(f"  trace spans: {len(res['trace'])}")
    print(f"  CSV: {out}")


if __name__ == "__main__":
    main()
