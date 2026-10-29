"""Bedrock research agent -- the ONLY AI step (reference architecture step 7).

Disease area in -> candidate drugs out, each with a rationale and evidence. The model
PROPOSES; it never picks a billing code (NDC->J-code is deterministic downstream). It must
ground every candidate by calling find_products against the FDA NDC reference data, so a
candidate's evidence is a real reference record -- not the model's training recall
(Principle 02). A recorded response is cached and used as a fallback if Bedrock is
unreachable, so a live demo never hard-fails on the network.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import boto3

from ..config import get_config
from ..reference_tools import ReferenceTools
from ..trace import record

_CACHE_DIR = Path(__file__).resolve().parent / "cache"

SYSTEM = """You are a clinical coding research assistant helping build a medical codebook.
Given a disease area, propose the drugs used to treat it that are likely to appear in US
claims data.

Rules:
- Ground EVERY candidate: call find_products to confirm the drug exists in the FDA NDC
  reference data before including it. Use the SUBSTANCENAME returned by the tool.
- Do NOT propose or guess billing codes (J-codes/HCPCS). Those are resolved deterministically
  downstream from the crosswalk. Propose drugs only.
- Stay strictly on the named disease. Do not drift to a similarly named but different
  condition (e.g. "hereditary angioedema" is NOT ACE-inhibitor or acquired angioedema).
- When done, output ONLY a JSON array; each item:
  {"drug": brand or common name, "substance": SUBSTANCENAME from the tool,
   "rationale": one line on why it treats this disease,
   "evidence": a reference record you found (brand + a product_ndc)}.
"""

TOOLS = {
    "tools": [
        {
            "toolSpec": {
                "name": "find_products",
                "description": "Search the FDA NDC reference data by drug brand or substance; "
                               "returns matching products (brand, substance, route, form, NDC).",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "brand or substance"}},
                    "required": ["query"],
                }},
            }
        }
    ]
}


def _run_tool(tools: ReferenceTools, name: str, inp: dict) -> dict:
    if name == "find_products":
        q = (inp or {}).get("query", "")
        rows = tools.products_by_substance(q) or tools.products_by_brand(q)
        seen, out = set(), []
        for r in rows:
            key = r.get("product_ndc9")
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "brand": r.get("proprietary_name"), "substance": r.get("substance_name"),
                "route": r.get("route"), "dosage_form": r.get("dosage_form"),
                "product_ndc": r.get("product_ndc"),
            })
            if len(out) >= 12:
                break
        return {"matches": out, "count": len(out)}
    return {"error": f"unknown tool {name}"}


def _parse_candidates(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [c for c in data if isinstance(c, dict) and c.get("drug")]
    except json.JSONDecodeError:
        return []


def _cache_path(disease: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", disease.lower()).strip("-")
    return _CACHE_DIR / f"{slug}.json"


def research_candidates(disease_area: str, tools: ReferenceTools | None = None,
                        model_id: str | None = None, max_turns: int = 8,
                        use_cache_fallback: bool = True) -> list[dict]:
    cfg = get_config()
    tools = tools or ReferenceTools()
    model_id = model_id or cfg.bedrock_model_id
    try:
        candidates = _run_bedrock(disease_area, tools, model_id, max_turns)
        if candidates:
            try:  # best-effort; the Lambda bundle filesystem is read-only
                _CACHE_DIR.mkdir(exist_ok=True)
                _cache_path(disease_area).write_text(json.dumps(candidates, indent=2))
            except OSError:
                pass
        return candidates
    except Exception as e:  # noqa: BLE001
        record("model", "bedrock.error", err=type(e).__name__, msg=str(e)[:160])
        cache = _cache_path(disease_area)
        if use_cache_fallback and cache.exists():
            record("model", "bedrock.fallback_cache", path=str(cache))
            return json.loads(cache.read_text())
        raise


def _run_bedrock(disease_area: str, tools: ReferenceTools, model_id: str,
                 max_turns: int) -> list[dict]:
    client = boto3.client("bedrock-runtime", region_name=get_config().region)
    messages = [{"role": "user", "content": [
        {"text": f"Disease area: {disease_area}\nPropose the grounded candidate drugs."}
    ]}]
    for _ in range(max_turns):
        t0 = time.perf_counter()
        resp = client.converse(
            modelId=model_id,
            system=[{"text": SYSTEM}],
            messages=messages,
            toolConfig=TOOLS,
            inferenceConfig={"maxTokens": 2000, "temperature": 0},
        )
        record("model", "bedrock.converse", ms=round((time.perf_counter() - t0) * 1000, 1),
               stop=resp.get("stopReason"))
        out = resp["output"]["message"]
        messages.append(out)
        if resp.get("stopReason") == "tool_use":
            results = []
            for c in out["content"]:
                tu = c.get("toolUse")
                if not tu:
                    continue
                res = _run_tool(tools, tu["name"], tu.get("input", {}))
                results.append({"toolResult": {"toolUseId": tu["toolUseId"],
                                                "content": [{"json": res}]}})
            messages.append({"role": "user", "content": results})
            continue
        text = "".join(c.get("text", "") for c in out["content"])
        return _parse_candidates(text)
    return []
