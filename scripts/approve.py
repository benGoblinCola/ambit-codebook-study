"""Human review step (R08 / Principle 04): approve or reject a draft codebook version,
freezing it. The Step Functions pipeline produces an eval-gated draft; this is the
deliberate human action that approves it.

    python scripts/approve.py hereditary-angioedema 1 "Dr. Reviewer" approved
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ambit_codebook.review import set_review  # noqa: E402

codebook_id = sys.argv[1]
version = int(sys.argv[2])
reviewer = sys.argv[3] if len(sys.argv) > 3 else "demo-reviewer"
decision = sys.argv[4] if len(sys.argv) > 4 else "approved"

print(set_review(codebook_id, version, decision, reviewer))
